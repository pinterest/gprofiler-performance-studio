# Workload Status (`GET /profiling/workload_status`) — performance follow-ups

## Context

At ~34K active hosts / ~684K total `HostHeartbeats` rows (PROD, Sep 2026) the endpoint
timed out (>30s). Root causes, from prod `EXPLAIN (ANALYZE)`:

1. **Bad plan from `ORDER BY/GROUP BY service_name`** — the planner did a full scan of
   `idx_hostheartbeats_service_name` (cost ~126K) over all 684K rows, applying the
   `heartbeat_timestamp > now()-2min` freshness filter as a per-row heap filter, to avoid a
   sort. Random I/O over the whole table → timeout.
2. **Table bloat / no retention** — only ~34K of 684K hosts are active; ~650K are stale
   decommissioned hosts that are never cleaned up. Every request scans all of them.
3. **`COUNT(*) OVER ()`** (`total_groups`) prevents `LIMIT` from short-circuiting.

## Shipped in this change (a)

- Restrict to the active fleet **first**, in a `fresh_hosts AS MATERIALIZED (…)` CTE, so
  grouping/sorting can no longer drive the full-index scan.
- Dropped the redundant `latest_commands` window (`ProfilingCommands` is already
  `UNIQUE (hostname, service_name)`).

Measured on PROD (read-only `EXPLAIN ANALYZE`), `scope=host`, page 0:

| Query | Before | After (a) |
| --- | --- | --- |
| grouped rows | **timeout (>30s)** | **~1.4s** |

Uniform across all scopes, no schema change, no semantic change (all filters still applied
in `filtered`).

---

## Follow-up (b) — entity-first pagination

**Idea:** page the *driving entity* first, then aggregate only the page. For each scope,
select the page of grouping-key tuples from the minimal driving table (e.g. `fresh_hosts`
for host/service; `HeartbeatContainers ⋈ fresh_hosts` for namespace/pod/container;
`HeartbeatProcesses ⋈ …` for process) with `ORDER BY … LIMIT/OFFSET`, then compute the
per-row counts/metadata via `LATERAL` joins for just those ≤`page_size` entities. Get
`total_count` from a single `COUNT(*)` over the driving set and drop `COUNT(*) OVER ()`.

**Measured (prod, host scope):** ~1.4s → **~159ms**.

**Risk / effort:** query-only, but higher correctness risk than (a): filters must be pushed
to the correct level (the tricky case is a *deep filter at a shallow scope*, e.g.
`scope=host` with a `process_name` filter must still join processes to decide which hosts
qualify). Per-scope specialization → more surface area. **Gate on the `AT-S1..S17`
workload-acceptance tests against a seeded DB before rollout.**

## Follow-up (c) — targeted `tab_counts`

**Idea:** the tab-count query currently runs one flatten with **7 `COUNT(DISTINCT …)`**
(~900ms even at 34K rows, with temp spill). Replace with targeted per-tier counts that
share the `fresh_hosts` CTE:

- `service` / `host` / `active_hosts` → from `fresh_hosts` only (no child joins).
- `namespace` / `pod` / `container` → `HeartbeatContainers ⋈ fresh_hosts` (2-way).
- `process` → `HeartbeatProcesses ⋈ HeartbeatContainers ⋈ fresh_hosts` (single `DISTINCT`).

Optionally **cache** `tab_counts` for a short TTL (they change slowly; the freshness window
is already 2 min) and/or compute them only when scope/filters change, not on every page.

**Expected gain:** ~900ms → a few hundred ms (or ~0 when cached). Query-only.

## Follow-up — retention cleanup (root cause)

The unbounded growth of `HostHeartbeats` (and, once the workload agent rolls out,
`HeartbeatContainers` / `HeartbeatProcesses`) is the structural driver: 95% of rows are
stale. A periodic cleanup that deletes hosts whose `heartbeat_timestamp` is older than a
retention threshold (child tables cascade via FK) would shrink the table ~20×.

- The `~116ms` parallel seq scan to extract the fresh set drops to ~10ms.
- Every scan, index, and autovacuum on these tables gets ~20× cheaper.
- Natural home: the `deploy/periodic_tasks` container (cron), matching the existing
  aggregation/logrotate jobs.

**Expected gain:** with (b)+(c)+retention combined, the endpoint should land in the
**tens of ms** range at current scale.

## Suggested order

1. **(a)** — shipped (kills the timeout).
2. **Retention job** — biggest structural win, benefits everything.
3. **(b)+(c)** — brings steady-state latency to ~150ms, gated on the acceptance tests.
