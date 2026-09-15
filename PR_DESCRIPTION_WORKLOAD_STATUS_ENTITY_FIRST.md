# Fix `GET /profiling/workload_status` timeout at populated-child-table scale (follow-ups (b) + (c))

## Summary

Now that the workload agent is rolled out and the majority of agents report container /
process inventory, `HeartbeatContainers` (~316K rows) and `HeartbeatProcesses` (~1.08M
rows) are populated. The `workload_status` endpoint's queries flatten
`fresh_hosts ⋈ containers ⋈ processes` into a **~1.08M-row** cross product, and the
endpoint times out again — the materialized-fresh-hosts fix (PR #95, follow-up (a)) only
addressed the host-table scan, not the child-table fan-out.

This change implements the two documented follow-ups from
[docs/WORKLOAD_STATUS_PERF_FOLLOWUPS.md](docs/WORKLOAD_STATUS_PERF_FOLLOWUPS.md):

- **(c) targeted / tiered `tab_counts`** — replace the single 7×`COUNT(DISTINCT tuple)`
  pass (which now times out) with per-tier counts over the shallowest base that exposes
  each column.
- **(b) entity-first pagination** — page the driving entity first and hydrate only that
  page, adapted into a **hybrid** that also keeps a single-pass `GROUP BY` for the few /
  large-entity scopes where entity-first regresses.

All changes are query-only (one file, `db_manager.py`); no schema or API changes.

## The problem (measured on PROD, read-only `EXPLAIN`/`\timing`)

Current scale: `hostheartbeats` 694K (~31.6K fresh / 2 min), `heartbeatcontainers` 316K,
`heartbeatprocesses` 1.08M. The fresh flatten is ~1.08M rows.

| Query (current code) | Time |
| --- | --- |
| `tab_counts` (7×`COUNT(DISTINCT tuple)` over the flatten) | **timeout (>30s)** |
| grouped rows, `scope=service` | ~11–17s |
| grouped rows, `scope=host` (default view) | ~10s |

## The fix

### (c) Tiered `tab_counts` — `_workload_tab_counts`

Each count is computed from the shallowest base that exposes its columns, using
`COUNT(*) FROM (SELECT DISTINCT …)` (a hash-distinct) instead of `COUNT(DISTINCT tuple)`
(a sort per aggregate):

- `service` / `host` / `active_hosts` → `fresh_hosts` only.
- `namespace` / `pod` / `container` → `fresh_hosts ⋈ HeartbeatContainers`.
- `process` → 3-way join, distinct on the integer `(host_id, pid)` (1:1 with
  `(service_name, hostname, pid)` via `unique_host_heartbeat`), which is far cheaper than
  the wide text tuple.

The distinct-subquery rewrite alone took the process count from **13.2s → 0.84s**. All
filters are still applied at every tier, so the counts are identical to the previous
single-pass semantics.

### (b) Hybrid grouped rows — `_query_workload_groups`

The strategy is chosen by whether the scope keys on a specific host
(`hostname ∈ key_cols`):

- **Entity-first** (`host`, `container`, `process` — many small entities): enumerate the
  page of key tuples from the shallow key set, then hydrate **only that page** via a
  `LATERAL`. The hydrate reads `HostHeartbeats` **directly** (indexed on
  `service_name`/`hostname`), correlating only the host-level keys with `=`; finer keys
  are applied as NULL-safe residual filters (see "Planner notes"). `total_count` is a
  single `COUNT(*)` over the key set; `COUNT(*) OVER ()` is dropped.
- **Single-pass** (`namespace`, `pod` — few / large entities): one `GROUP BY` over the
  flatten, paginated. A per-entity `LATERAL` here would re-scan whole services repeatedly,
  so a single scan that computes every group is faster.
- **Two-grain single-pass** (`service`): the counts + latest metadata are computed at the
  cheap container grain (~316K rows), `process_count` via `COUNT(*) FROM (SELECT DISTINCT
  …) GROUP BY`, and `any_active` at the host grain. For **service scope only**, host-grain
  `any_active` is provably identical to the PID-aware value — a command's target PIDs
  always belong to that service's own hosts (verified on prod: 0/460 services differ). This
  avoids aggregating the full ~1.08M process flatten. Process-grain representatives
  (`l_process_name`/`l_pid`/`pids`) are not shown at service scope and are returned
  NULL/empty. When a process-tier filter is present it falls back to the plain single-pass
  to stay correct.

The entity-first and single-pass paths share the exact same aggregate SELECT list
(`_workload_agg_columns`), so they return byte-identical row shapes; the Python
post-processing is unchanged for every path.

### Results (PROD, page 0, default sort)

| Scope | Before | After |
| --- | --- | --- |
| `tab_counts` | **timeout** | ~4s (tier A 0.4s + B 1.1s + C 0.8s) |
| grouped `host` (default view) | ~10s | **~0.6s** |
| grouped `namespace` | timeout*/~11s | ~2.7s |
| grouped `pod` | — | ~3.1s |
| grouped `container` | — | ~1.8s |
| grouped `process` | — | ~3.5s |
| grouped `service` | ~11–17s | ~6.4s |

\* A naive entity-first `namespace` hydrate timed out because the planner drove from a
global `idx_hb_containers_namespace` scan on common namespaces; the hybrid avoids this.

## Planner notes (why the query is shaped this way)

- The hydrate reads `HostHeartbeats` directly, **not** the materialized `fresh_hosts` CTE:
  a CTE has no index, so correlating `fh.service_name = p.service_name` against it seq-scans
  all ~31K fresh hosts per page entity.
- Finer key columns (`namespace`, `pod_name`, …) are **residual** filters on the
  host-bounded set, never pushed onto the child tables. Pushing them lets the planner pick
  a global `idx_hb_containers_namespace` scan that explodes on `default` / `kube-system`.
- `namespace` / `pod_name` are frequently NULL even when a container name is present
  (~264K such container rows in PROD), so nullable intermediate keys use
  `IS NOT DISTINCT FROM` (NULL-safe) rather than `=`.

## Correctness validation (PROD, read-only)

Spot-checked new output against independent authoritative aggregations:

- Entity-first `host` scope counts — exact match (`container=14`, `process=42`, `namespace=0`).
- Entity-first `container` scope incl. NULL-namespace containers — exact
  (`process` per container `4/4/2`).
- Tiered `tab_counts` — exact (`service/host/active/ns/pod/container/process` all matched).
- Single-pass `namespace` scope — exact (small drift only in a busy namespace between runs,
  which is expected: the 2-minute fresh window is live and counts churn run-to-run).
- Two-grain `service` scope — `container_count` exact; `host_count`/`process_count` differ
  only by live-window churn on a huge busy service; `any_active` matches the PID-aware value
  (0/460 services differ).

## Testing

- `python -m py_compile` clean; no new linter findings in the changed code.
- Linters (`black`/`flake8`/`mypy`) are not installed in the sandbox — please run
  `./lint.sh --ci` in CI.
- **Gate on the workload acceptance tests** `AT-S1..S17`
  ([src/tests/e2e/test_workload_acceptance.py](src/tests/e2e/test_workload_acceptance.py),
  incl. AT-S16 pagination and AT-S17 sorting) against a seeded DB before rollout, since the
  grouped/tab-count SQL was substantially rewritten.

## Known limits / follow-ups

- `scope=service` is ~6.4s (down from ~16s, still up from ~1.4s pre-child-tables). The
  remaining cost is the container-grain latest-metadata `array_agg`s; the structural fix is
  the **retention cleanup** named in the follow-ups doc, which shrinks the base tables ~20×
  and benefits every scope.
- Sorting a large-entity scope by a deep aggregate column (e.g. `container` by
  `process_name`) forces the key set to the full flatten (~11s); this is a non-default,
  user-initiated path.
- The two-grain `service` path returns NULL/empty for `l_process_name`/`l_pid`/`pids`
  (never shown at service scope) and computes `any_active` at the host grain (identical to
  PID-aware for service scope). Namespace/pod keep the fully PID-aware single-pass.
