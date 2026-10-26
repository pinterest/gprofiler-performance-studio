# Webapp DB Concurrency Tuning

Environment variables that control how the webapp (`gprofiler_frontend`
container) opens and reuses PostgreSQL connections, how much DB work it does in
parallel, and how it behaves when the database is briefly unreachable.

## Background: the connection model

The webapp runs under **gunicorn** with **`uvicorn.workers.UvicornWorker`**
(async workers). The API route handlers are synchronous `def` functions, so
Starlette runs each request in a **per-worker threadpool** (anyio default: 40
threads).

Database access goes through `PostgresDB` (`src/gprofiler-dev/gprofiler_dev/postgres/`).
Each `PostgresDB` instance owns **one** psycopg2 connection guarded by an
`RLock`, so calls on a given instance are serialized. How many `PostgresDB`
instances (and therefore connections) a worker creates is selected by
`GPROFILER_POSTGRES_CONN_PER_THREAD`:

- **`FALSE` (default) — one shared connection per worker process.** A single
  process-wide `PostgresDB` is shared by all request threads; the `RLock`
  serializes DB access. Connections per worker ≈ **1**.
- **`TRUE` — one connection per request thread.** Each thread gets its own
  thread-local `PostgresDB`, so DB access runs in parallel across threads, but a
  worker can open up to `GPROFILER_WEBAPP_THREAD_POOL_SIZE` connections.

```
worker process
  ├─ ~40 request threads accepted concurrently
  └─ CONN_PER_THREAD=FALSE → 1 shared, lock-serialized connection
     CONN_PER_THREAD=TRUE  → up to (threadpool size) connections, one per thread
```

The per-thread mode gives real read/write parallelism but multiplies
connections. At fleet scale this is the dominant risk: with every worker thread
across every replica opening its own connection, connection **establishment** can
back up and storm the Aurora writer (connections pile up in `FIN-WAIT-2`, new
`connect()`s time out). See "Reconnect, retry & timeouts" for the guardrails, and
"Connection math" for sizing.

> **Historical note.** A bounded in-process pool
> (`psycopg2.pool.ThreadedConnectionPool` + semaphore) was prototyped and then
> **reverted**: request threads that block in Python *while holding* a pooled
> connection starve the semaphore process-wide. `GPROFILER_POSTGRES_POOL_SIZE` /
> `GPROFILER_POSTGRES_POOL_ACQUIRE_TIMEOUT` are still parsed in `config.py` but
> are **not wired into the connection path** today. The durable direction for
> hard-capping connections is an **external pooler** (PgBouncer transaction mode
> or RDS Proxy), which pools at the network layer without the in-process wedge.

## The knobs

| Environment variable | Where read | Default | Effect |
| --- | --- | --- | --- |
| `GPROFILER_POSTGRES_CONN_PER_THREAD` | `src/gprofiler-dev/gprofiler_dev/config.py` | `FALSE` | Connection model selector. `FALSE` = one shared, lock-serialized connection per worker (~1 conn/worker). `TRUE` = one connection per request thread (up to threadpool-size conns/worker). |
| `GUNICORN_PROCESS_COUNT` | `src/gprofiler/run.sh` | `nproc` | Number of gunicorn worker **processes** per replica. Each process opens its own connection(s). |
| `GPROFILER_WEBAPP_THREAD_POOL_SIZE` | `src/gprofiler/backend/config.py` | `0` (anyio default 40) | Per-worker threadpool size = max concurrent requests per worker. In `CONN_PER_THREAD=TRUE` mode this is also the per-worker connection ceiling. |
| `GPROFILER_POSTGRES_CONNECT_TIMEOUT` | `src/gprofiler-dev/gprofiler_dev/config.py` | `10` (seconds) | libpq `connect_timeout` for each connection attempt. |
| `GPROFILER_POSTGRES_MAX_RETRIES` | `src/gprofiler-dev/gprofiler_dev/config.py` | `3` | Max attempts for `PostgresDB.execute()` on a transient connection error. |
| `GPROFILER_POSTGRES_RETRY_BACKOFF_BASE` | `src/gprofiler-dev/gprofiler_dev/config.py` | `0.2` (seconds) | Base for exponential backoff between retries. |
| `GPROFILER_POSTGRES_RETRY_BACKOFF_CAP` | `src/gprofiler-dev/gprofiler_dev/config.py` | `5.0` (seconds) | Upper bound on a single backoff wait. |
| `GPROFILER_POSTGRES_RETRY_BACKOFF_JITTER` | `src/gprofiler-dev/gprofiler_dev/config.py` | `0.5` (seconds) | Random jitter added to each backoff wait. |
| `GPROFILER_POSTGRES_POOL_SIZE` | `src/gprofiler-dev/gprofiler_dev/config.py` | `10` | **Currently inactive** (the in-process pool was reverted; see the historical note). |
| `GPROFILER_POSTGRES_POOL_ACQUIRE_TIMEOUT` | `src/gprofiler-dev/gprofiler_dev/config.py` | `10` | **Currently inactive** (see above). |

### `GPROFILER_POSTGRES_CONN_PER_THREAD` — the connection-model knob

`FALSE` trades per-worker parallelism for a hard, tiny connection footprint (one
serialized connection per worker). `TRUE` gives real parallelism per worker at
the cost of up to `threadpool` connections each. Total fleet connections scale as
shown in "Connection math"; keep them well under the database `max_connections`.

## Reconnect, retry & timeouts

`PostgresDB.execute()` retries transient connection errors
(`OperationalError` / `InterfaceError`) up to `GPROFILER_POSTGRES_MAX_RETRIES`
attempts, reconnecting between tries. Two behaviors matter under load:

- **`_reconnect()` closes the old connection before opening a new one.** If the
  stale socket were only dropped for the garbage collector to reclaim, it lingers
  in `FIN-WAIT-2`; under load thousands accumulate and saturate the database's
  connection **accept** path. Closing first keeps the socket count bounded.
- **Backoff is exponential with jitter**, not a fixed sleep. A fixed sleep makes
  every thread reconnect in lockstep, turning a brief blip into a self-sustaining
  connection storm. The wait is
  `min(BASE × 2**attempt, CAP) + random(0, JITTER)`, so retry waves
  de-synchronize instead of hammering the accept path together.

`execute()` also accepts an optional `max_retries` argument so latency-sensitive
callers can pass a low value (e.g. `1`) to **fail fast and shed load** rather than
block on reconnect attempts while the accept path is saturated.

`GPROFILER_POSTGRES_CONNECT_TIMEOUT` defaults to **10 s**: long enough that a
brief latency blip does not trip a reconnect wave, and well under the gunicorn
worker timeout (300 s). Pair it with the bounded backoff above rather than
relying on the timeout in isolation.

## Connection math

Total DB connections opened by the webapp is bounded by:

```
CONN_PER_THREAD=FALSE:  connections ≈ replicas × GUNICORN_PROCESS_COUNT
CONN_PER_THREAD=TRUE:   connections ≈ replicas × GUNICORN_PROCESS_COUNT × GPROFILER_WEBAPP_THREAD_POOL_SIZE
```

Keep the total comfortably under the database's `max_connections` (Aurora here is
`5000`) with margin for periodic tasks and admin sessions. Connection count is
necessary but not sufficient — the writer must also have the CPU/ACU to service
the work; a past incident saturated ACU at 100%, not a connection-count limit.

**Examples (`nproc = 8`, 6 replicas):**

| Config | Conns/worker | Conns/replica | 6 replicas |
| --- | --- | --- | --- |
| `CONN_PER_THREAD=FALSE`, 8 workers | 1 | 8 | 48 |
| `CONN_PER_THREAD=TRUE`, 8 workers, threadpool 40 | up to 40 | up to 320 | up to 1920 |

The gap between those rows is the storm risk the connection model controls.

## Sizing for load (Little's Law)

The number of DB operations in flight at once is `L = λ × W`, where `λ` is the
request rate and `W` is how long each request holds a connection. Your available
concurrency must exceed `L`, with headroom for bursts.

- With `CONN_PER_THREAD=TRUE`, per-worker concurrency is the threadpool size.
- With `CONN_PER_THREAD=FALSE`, DB access is serialized within a worker, so
  per-worker DB concurrency is effectively **1** — fleet DB concurrency is
  `replicas × workers`. Keep per-request hold time `W` low (see below) so this
  serialized path still clears the offered load.

The dominant driver is the agent heartbeat: **~30k hosts × 1 per 30 s ≈
1000 heartbeat QPS**. Heartbeat writes are buffered off the request thread by the
async heartbeat writer (coalesced batch flush), so the request path holds a
connection only briefly; the flush thread does the batched write. Keep an eye on
the **live fine read scopes**, which can hold a connection for seconds
(container ~4 s, process ~8 s) and dominate `W` when used.

> **Connections enable parallelism, they do not create DB throughput.** 1000
> write-heavy heartbeats/s is real load on the Aurora writer regardless of how
> many connections you open. If the writer saturates (CPU, lock/WAL), the fix is
> to cut per-heartbeat cost (batch/coalesce writes) or scale the DB — not to add
> more connections.

## Recommended production settings

Start with the low-footprint model and only move to per-thread if you measure a
DB-concurrency bottleneck while the writer still has headroom:

```bash
# One shared, lock-serialized connection per worker (~1 conn/worker).
GPROFILER_POSTGRES_CONN_PER_THREAD=FALSE

# Process count; total connections = replicas * workers (in FALSE mode).
GUNICORN_PROCESS_COUNT=8

# Tolerate a brief blip; fail well under the 300s worker timeout.
GPROFILER_POSTGRES_CONNECT_TIMEOUT=10
```

If you switch to `CONN_PER_THREAD=TRUE` for parallelism, lower
`GPROFILER_WEBAPP_THREAD_POOL_SIZE` deliberately so
`replicas × workers × threadpool` stays well under `max_connections`, and watch
the connection accept path.

## Verifying

Check live connection usage against the limit:

```sql
SHOW max_connections;
SELECT count(*) AS total,
       count(*) FILTER (WHERE state = 'active') AS active
FROM pg_stat_activity;
```

On a webapp host, watch the socket states to the DB (a healthy host shows a
small, stable `ESTAB` count and **no** growing `FIN-WAIT-2`):

```bash
ss -tan | awk '$5 ~ /:5432$/ {print $1}' | sort | uniq -c
```
