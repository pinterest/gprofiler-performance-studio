# Workload-level profiling at scale: why the first version fell over, and the fix

This is the parent document for the five-PR stack that makes workload-level (dynamic)
profiling work at fleet scale — on the order of 150k hosts and 100M processes — instead of
a few hundred hosts. It explains the problem in plain language, the design that fixes it,
why that design is safe to roll out, and how the PRs fit together. The detailed contract
lives in [`WORKLOAD_LEVEL_PROFILING_SPEC.md`](WORKLOAD_LEVEL_PROFILING_SPEC.md); this
document is the "why" and the map.

The PR stack (each based on the previous one):

| PR | Branch | One line |
| --- | --- | --- |
| [#116](https://github.com/pinterest/gprofiler-performance-studio/pull/116) | `scale/01-drop-redundant-indexes` | Drop 7 indexes nothing needs |
| [#117](https://github.com/pinterest/gprofiler-performance-studio/pull/117) | `scale/02-inventory-digest-sync-budget` | Server-side fingerprint skip + sync budget (the fix) |
| [#118](https://github.com/pinterest/gprofiler-performance-studio/pull/118) | `scale/03-digest-only-heartbeats` | Server accepts digest-only beats, replies `need_inventory` |
| [#119](https://github.com/pinterest/gprofiler-performance-studio/pull/119) | `scale/04-remove-heartbeat-scaffolding` | Delete the layer that moved the work around |
| [#120](https://github.com/pinterest/gprofiler-performance-studio/pull/120) | `scale/05-per-host-counters` | Status page never scans `HeartbeatProcesses` |

Agent side: [pinterest/gprofiler #87](https://github.com/pinterest/gprofiler/pull/87) — the
agent sends inventory only when it changed. Its own write-up is
[`docs/WORKLOAD_LEVEL_PROFILING_AT_SCALE.md`](https://github.com/pinterest/gprofiler/blob/master/docs/WORKLOAD_LEVEL_PROFILING_AT_SCALE.md)
in the agent repo.

---

## 1. The one fact that explains everything

Every gProfiler agent sends a heartbeat every 30 seconds. In the original workload-level
profiling design (PR #81) that heartbeat was not a small "I'm alive" ping. It carried the
**complete list of every container and every process on the host, every time, whether
anything had changed or not.**

```
agent (1 host)  --every 30 s-->  POST /api/metrics/heartbeat
                                 hostname, service, status, perf events
                                 + ALL containers
                                 + ALL processes (pid, name)        -->  webapp  -->  Postgres
```

Database work was therefore proportional to **inventory size × beat rate**, not to how
often the inventory actually changed. Inventory changes rarely (a deploy, a pod
reschedule); the database paid for 100% of it every 30 seconds.

| Fleet | Beats per second | Process rows re-sent per 30 s |
| --- | --- | --- |
| ~33k hosts, ~3M processes | ~1,100 | ~3M |
| 150k hosts, 100M processes (target) | ~5,000 | ~100M |

At the target that is ~3.3M process rows per second arriving at Postgres, forever. No
tuning of Postgres reaches this. Only sending less does.

## 2. What the original design did on every heartbeat

Functionally correct, and fine for a few hundred hosts. One heartbeat took this path:

1. a request thread picks up the POST
2. insert-or-update the host's row in `HostHeartbeats`
3. **DELETE all containers for the host** (cascading to its processes)
4. **INSERT every container again**, one by one
5. **INSERT every process again**
6. COMMIT
7. read the current profiling command and reply

Four weak spots, all of which were later hit:

- **A. Delete + re-insert everything, every beat.** Even when nothing changed, every process
  row was deleted and recreated. In Postgres a delete leaves a dead row that must be
  cleaned up later, and every insert burns a new ID from a sequence.
- **B. Written on the request thread, synchronously.** The agent's request waited for steps
  2–6. The same threads also serve the UI, so when the database got slow, heartbeats and
  the UI got slow together.
- **C. One DB connection per worker process, behind a lock.** This predates #81: one
  `DBManager` per worker opened one connection and guarded it with a lock, so 40 request
  threads took turns on one connection. Invisible while a query held the lock for ~1 ms;
  #81 made a heartbeat hold it for hundreds of milliseconds, and the queue behind the lock
  became multi-second stalls. Removing the lock without a new cap is where the connection
  problems came from.
- **D. Status page recomputed from raw tables on every view.** `workload_status` ran
  `COUNT(DISTINCT …)` over all hosts, containers and processes each time someone opened the
  page. At millions of rows that took minutes, so the page timed out.

## 3. The fifteen follow-up fixes, and why they did not help

Fifteen PRs (#98–#113) landed in eight days on top of #81. Each one was locally reasonable
and each one uncovered the next bottleneck, because the amount of work never shrank — it
only moved:

```
slow page → 1 connection → thousands of connections → pool blocks UI reads
→ background writer → deadlocks → sort → single-file queue → two transactions
→ slow delete plan → connection leak + rolling restart → outage
```

| PR | What it did | Verdict |
| --- | --- | --- |
| #98 / #99 | Precomputed status-page tables rebuilt by a cron job; thread-pool / connection knobs | keep |
| #100 | One DB connection per thread instead of one shared connection | keep |
| #101 | Insert only new rows, update only changed rows; sequence `CACHE 500` | keep |
| #102 | In-app connection pool | reverted by #104 |
| #103 | Async heartbeat writer: in-memory buffer flushed every 1 s in one transaction; silent drop with `200 OK` when full | removed by #119 |
| #104 | Back to per-thread connections; server-side idle timeout | keep the idle timeout |
| #105 | Sort batches to avoid deadlocks (reverted 29 min later); "fresh" window 2 min → 15 min | window undone by #119 |
| #106 | Revert the sort; keep 3× deadlock retry | retry removed by #119 |
| #107 | Two-transaction split; batch inventory SQL (delete-missing / insert-new / update-changed) | keep the SQL, drop the batch wrapper |
| #111 | Rewrite the slow "delete what is gone" query; `work_mem` hint | keep the rewrite, drop the hint |
| #112 | Read the command once per beat instead of 3–4 times | keep |
| #113 | Close the old connection before reconnecting; randomized growing retry delay; connect timeout 3 s → 10 s | merge first — a real bug independent of inventory |

The end state was a webapp rolling restart under this code opening thousands of
connections at the same instant, with every failed connect retried after exactly one
second by every thread at once and the old half-open socket never closed. The database
fell behind, health checks went to tens of seconds, and production was rolled back to the
pre-#81 image.

## 4. The fix: send inventory only when it changes, then delete the scaffolding

Everything above tried to make Postgres absorb the same amount of work faster. The fix
makes the amount of work depend on how much actually changed.

### 4.1 A fingerprint for the inventory

The agent already holds the full list of containers and processes in memory; it just needs
to notice when that list is identical to last time. "Identical" is checked with a
**fingerprint**: a short fixed-size code computed from the whole list (a sha256 hash). Same
list → same fingerprint. The column on `HostHeartbeats` is `inventory_digest`.

The canonical form is defined once (`gprofiler_dev/heartbeat_inventory.py`) and reused
byte-for-byte by the agent: containers sorted by id, processes sorted by pid, the same
filters the sync applies, `DIGEST_VERSION = "1"`, two pinned test vectors on both sides.

### 4.2 The comparison costs nothing extra (#117)

There is no separate fetch. Every heartbeat already runs exactly one small statement —
find the host's row, update its timestamp — and has since before #81. The fingerprint
becomes one more column in that same statement, which also reports whether the stored
value differed:

```sql
UPDATE HostHeartbeats
   SET heartbeat_timestamp = now(), status = 'active', inventory_digest = 'a3f9…'
 WHERE hostname = 'host-123' AND service_name = 'ads'
RETURNING ID, (old inventory_digest <> 'a3f9…') AS changed;   -- false → beat is done
```

- unchanged (the overwhelming majority of beats): one index lookup, ~1 ms, zero inventory
  rows touched
- changed, or nothing stored yet: run the existing per-host diff-only sync (#107's SQL, for
  one host) in the same transaction, and store the new digest with it

Comparing two 64-character strings instead of checking ~700 rows is like comparing two
file checksums instead of diffing the files line by line. The hashing runs on the webapp's
CPU (or the agent's), never on the database.

### 4.3 A budget on how many full syncs run per second (#117)

Each worker process has a counter: at most `GPROFILER_INVENTORY_SYNC_PER_SEC` full syncs per
second (default 2; `<= 0` means unlimited). When the counter is spent, the beat does the
one-row update only, replies OK, and **does not store the digest**. The host beats again in
30 seconds and asks again. A deferred beat writes zero inventory rows, so continued beating
never multiplies the work.

Fleet-wide total = budget × number of worker processes. The budget is per process because
a shared counter in Postgres would cost a query per beat, which defeats the point.

### 4.4 "Not reported" is not "empty" (#117)

`containers` omitted (`None`) means "I did not send inventory this time" and does no
inventory work. `containers: []` means "I have no containers" and prunes the stored rows.
The route no longer coerces `None` to `[]`.

Every beat is counted in `heartbeat_inventory{outcome=…}`:
`synced | unchanged | deferred | not_reported | digest_match | digest_mismatch`.

### 4.5 Digest-only beats and `need_inventory` (#118)

With #117 alone the agent still sends the full payload every beat and the server merely
stops paying for it in the database. #118 lets the agent stop sending it:

- `HeartbeatRequest.inventory_digest: Optional[str]` — the agent's fingerprint
- `HeartbeatResponse.need_inventory: Optional[bool]` — `None` for agents that sent no digest
  (old agents), `False` when the server holds the inventory for that digest, `True`
  otherwise

`need_inventory` is decided from the digest **committed on the host row**, not from this
beat's payload, so a deferred sync is answered `True` and the agent keeps sending until the
server really holds it.

The server always stores its own computed digest; the agent's digest is only compared
against it. If the two sides ever disagree (version skew), that agent sends full inventory
every beat — today's traffic, one warning per agent version — never silent data loss.

`inventory_pending_hosts` (fresh hosts with no committed digest) is in the
`workload_status` response and on the Profiling Status page, so rollout progress is
visible.

### 4.6 The agent rule (pinterest/gprofiler #87)

Always send `inventory_digest`; include `containers` only when the digest differs from the
last **acknowledged** one. Acknowledge only on an explicit `need_inventory: false`. Field
absent (old server), non-200, or exception → forget the acknowledgement and keep sending
full payloads. A restart or a server that lost state costs one full beat, never a missing
inventory. Against an old server the unknown request field is ignored and containers keep
flowing every beat.

### 4.7 Delete the layer that moved the work around (#119)

Once an unchanged beat costs the same as a heartbeat did before #81 — one small row update
and one read, which is exactly what production runs today, synchronously, with no buffer —
the buffer / batching / deadlock layer has nothing left to do and can be deleted rather
than tuned:

- removed: `heartbeat_writer.py`, `bulk_upsert_host_heartbeats`, `_bulk_sync_inventory`,
  `_write_with_deadlock_retry`, the `work_mem` hint, writer start-up in `main.py`
- the request thread does: upsert host → digest compare → budget → the existing per-host
  diff-only sync, one transaction; `need_inventory` is derived from the upsert outcome
- knobs deleted: `GPROFILER_HEARTBEAT_ASYNC_WRITES`, `GPROFILER_HEARTBEAT_FLUSH_INTERVAL_SEC`,
  `GPROFILER_HEARTBEAT_BUFFER_MAX_HOSTS`, `GPROFILER_POSTGRES_CONN_PER_THREAD` (per-thread
  is the only mode), `GPROFILER_POSTGRES_POOL_SIZE`, `GPROFILER_POSTGRES_POOL_ACQUIRE_TIMEOUT`
- `GPROFILER_WORKLOAD_FRESH_INTERVAL` default back from 15 minutes to 2 minutes: beats are
  written within milliseconds again, so 2 minutes is accurate and 15 only hid lag

One host per transaction also means no two writers can deadlock each other.

### 4.8 Per-container process counters (#120)

`HeartbeatContainers.process_count integer NOT NULL DEFAULT 0` is written in the same
guarded INSERT/UPDATE as the container row (unchanged containers still write nothing). The
status-page rebuild (`refresh_workload_snapshot`) and the live service / namespace / pod
summaries use `SUM(process_count)` and never read `HeartbeatProcesses`; the live path only
drops to the process grain when a process-level filter forces it.

Semantic change on purpose: a coarse-scope `process_count` is now the number of processes
in the group. The old `COUNT(DISTINCT (pid, process_name))` collapsed identical processes
on different hosts and disagreed with the tab count.

### 4.9 Seven indexes nothing needs (#116)

Dropped with `DROP INDEX CONCURRENTLY` (online, no lock that blocks reads or writes; undo is
`CREATE INDEX CONCURRENTLY`):

- `idx_hostheartbeats_heartbeat_timestamp` — indexed *and* rewritten on every beat. When an
  indexed column changes Postgres cannot overwrite the row in place; it writes a new copy
  and updates every index on the table. A "newer than 2 minutes" filter over 150k rows does
  not need an index.
- `idx_hb_processes_process_name` — a text index over what will be 100M rows, maintained on
  every insert/delete, used by nothing in the heartbeat path.
- `idx_hostheartbeats_hostname`, `idx_hb_containers_host_id`,
  `idx_hb_processes_container_row_id`, `idx_profilingcommands_hostname`,
  `idx_profilingcommands_hostname_service` — each is the first column of (or identical to)
  an existing UNIQUE constraint on the same table, which already answers those lookups.

Drop these *before* the first rollout, so the one-time initial load maintains two indexes
per process row instead of four.

## 5. Why this is safe to roll out

### 5.1 Restarts do not cause a write storm

The fingerprint's source of truth is the `inventory_digest` column in Postgres, written
whenever inventory is actually synced. The agent only keeps a copy.

| Who restarts | Fingerprint lost? | First beat afterwards | Inventory writes |
| --- | --- | --- | --- |
| webapp | no — it is in Postgres | compare incoming payload to column → same → skip | 0 |
| one agent | only the in-memory copy | one full payload → server compares → skip | 0 (one pid changes if the agent's own process is in the list — a handful of rows) |
| fleet agent rollout | copies only | rolling over hours; each = one parse + compare | 0 — less load than today's every beat |
| host reboot / node drain | no, but inventory really changed | full payload → diff SQL | only the changed rows — unavoidable in any design |
| Postgres restored from backup | yes | server sees no digest → syncs; replies `need_inventory: true` to digest-only agents | one sync per host, once, paced by the budget |
| first rollout (column is new) | nothing stored yet | every host differs → full sync, paced by the budget | the one-time initial load, then steady state |

### 5.2 Release day is not a herd

On release every host needs its inventory written once — ~100M rows at the target that no
design can avoid writing. The question is only how fast. A heartbeat now has two parts:

- **Part A — "I'm alive":** update one row (timestamp, fingerprint). ~1 ms. Always done.
- **Part B — "here is my inventory":** write ~700 container/process rows. Only when needed
  *and* allowed by the budget.

What one host experiences:

```
beat 1  (t=0)    A    budget spent → no B                 1 ms
beat 2  (t=30)   A    budget spent → no B                 1 ms
   ...
beat k           A    budget free  → B: rows written, digest stored
beat k+1         A    digest matches → skip B             1 ms   ← forever
```

The host writes its rows exactly once. Total inventory written during the load is
hosts × rows-per-host no matter how many beats happen in between; the extra beats only add
Part A's (5,000/s × 1 ms ≈ 5 connections busy).

How long: with 56 worker processes and a budget of 2/s each, 112 hosts are synced per
second; 150,000 ÷ 112 ≈ 22 minutes. At a budget of 0.5 (the conservative starting value)
≈ 90 minutes. Arrivals are already spread evenly because each agent's 30 s clock started
at its own boot time. After that, Part B runs only for the few percent of hosts whose
inventory actually changed.

### 5.3 Command control still works on digest-only beats — verified

The worry: once a beat is just a hostname and a hash, does a "start profiling" for a
service, a host, or a pod still reach the agent? Yes. The command lookup and the
auto-subscribe run on every beat regardless of what the payload carries, and the reply
carries the command exactly as before. Checked end to end against the #118/#119 backend
with the real agent `HeartbeatClient`:

| Scope | What was sent | What arrived on the next digest-only beat |
| --- | --- | --- |
| service | start for a service (no selector) | every host of the service got start, then stop — no pid restriction |
| host | start for one named host | only that host got the command; others got none |
| pod | start for one pod | the host running the pod got start with that pod's pids in `combined_config.pids`, taken from the synced inventory |
| pod, host not yet synced | start for a pod on a host whose initial sync the budget deferred | no command (correct — the server does not know the pod's pids yet); a new request after the sync picks it up. That gap is what `inventory_pending_hosts` shows during rollout |

## 6. Connections: what runs inside one webapp container, and the math

nginx accepts HTTPS and passes requests to gunicorn, which is only a process manager: it
starts N copies of the app (N ≈ CPUs, typically 8). Each copy is a **worker** — a separate
process with its own memory and DB connections. Inside each worker, FastAPI runs request
code on a pool of threads (anyio's default is 40 — nobody chose that number). Each thread
holds its own DB connection, so:

```
containers × workers × threads = connections
   7       ×    8    ×   40    = 2,240   (default)
   7       ×    8    ×   10    =   560   (GPROFILER_WEBAPP_THREAD_POOL_SIZE=10)
```

Once beats are short (~1 ms), ten threads per worker are plenty, and the connection count
is a quarter of what it was. A connection middleman (PgBouncer or RDS Proxy) would cap it
further and is still a reasonable option, but it is no longer required.

Writing on the request thread again means weak spot B returns in a mild form: a thread is
busy for ~1 ms per beat, a few ms on a changed beat. At 5,000 beats/s across 56 workers
that is a few percent of thread time. A wave of changed hosts (a big deploy restarting
10k pods) would briefly tie up many threads; if that ever shows up in UI latency, run
heartbeat ingest as its own deployment of the same image so the UI never shares threads
with ingest.

## 7. Rolling it out

Production runs the pre-#81 image. Deploying anything earlier than #117 would put back
"full inventory every beat" for no user value, so the first code deploy is step 3.

| Step | Merge | Prod | Watch |
| --- | --- | --- | --- |
| 1 | #113 (+ pin the studio commit your webapp image is built from) | no | connections on restart |
| 2 | #116 | **database only** — the migration is online; old code does not need these indexes | no query regressions |
| 3 | #117 + #118; config `GPROFILER_WEBAPP_THREAD_POOL_SIZE=10`, `GPROFILER_INVENTORY_SYNC_PER_SEC=0.5` | **first code deploy (point A).** One webapp host first, pause, then the rest. Raise the budget 0.5 → 1 → 2 in one-line config changes | `inventory_pending_hosts` climbs then drains to 0; DB CPU; UI p95 flat |
| 4 | agent #87 | rolling agent rollout over days, **only after step 3 is in prod** | payload size ~300 B; `need_inventory` ≈ change rate |
| 5 | #119 + #120; remove the deleted env vars from config | **point B**, after ≥ 1 week of step 3 in prod with `inventory_pending_hosts ≈ 0`. Run the #120 migration first | heartbeat p99; refresh job time; UI p95 |

Migrations, in order (`scripts/setup/postgres/migrations/`):

1. `drop_redundant_heartbeat_indexes.sql` (#116) — run with autocommit; `DROP INDEX CONCURRENTLY` cannot run inside a transaction
2. `add_inventory_digest_to_hostheartbeats.sql` (#117) — adds a nullable column; no rewrite, no backfill
3. `add_process_count_to_heartbeat_containers.sql` (#120) — adds the column, backfills in committed 10k-row batches, redefines `refresh_workload_snapshot`

Rollback at every step is the previous image. The new columns and the dropped indexes are
harmless to older code.

## 8. Glossary

- **dead row** — space left behind by a delete/update until a background cleaner (vacuum) reclaims it
- **sequence** — the counter that hands out auto-increment IDs
- **lock** — a "one at a time" gate
- **fingerprint / digest / hash** — a short fixed-size code computed from data; same data → same code
- **worker** — one gunicorn child process running the app; has its own DB connections
- **request thread** — one of the threads inside a worker that runs request code
- **token bucket / budget** — a counter that refills at a fixed rate and is spent one unit per admitted action
- **PgBouncer / RDS Proxy** — a connection middleman that keeps a small fixed number of real database connections and lends them out per query
