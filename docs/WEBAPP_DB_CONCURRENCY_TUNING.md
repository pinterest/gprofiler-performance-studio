# Webapp DB Concurrency Tuning

Environment variables that control how much database work the webapp
(`gprofiler_frontend` container) can do in parallel, and how to set them in
production.

## Background: why this matters

The webapp runs under **gunicorn** with **`uvicorn.workers.UvicornWorker`**
(async workers). The API route handlers are synchronous `def` functions, so
Starlette runs each request in a **per-worker threadpool** (anyio default: 40
threads).

Database access goes through `PostgresDB`, which holds a **bounded, per-process
connection pool** (`psycopg2.pool.ThreadedConnectionPool` plus a semaphore for
blocking backpressure). Worker threads borrow a connection for the duration of a
query/transaction and return it:

```
worker process
  ├─ ~40 request threads accepted concurrently
  └─ pool of N connections  ← threads borrow/return; >N wait (backpressure)
```

This gives real read/write parallelism **up to the pool size** while capping how
many connections each process opens. When the pool is fully in use, extra threads
wait for a free connection (up to the acquire timeout) instead of opening more.
That cap is what matters at scale: an earlier per-thread design (one unbounded
connection per thread) worked in a single process but, once every worker thread
across the fleet opened its own connection, **stormed the Aurora writer** —
connection establishment backed up to 20+ s, hitting 100% ACU and ~1.6k
connections and failing heartbeats. The pool bounds and reuses connections so that
cannot happen.

## The knobs

| Environment variable | Where read | Default | Effect |
| --- | --- | --- | --- |
| `GPROFILER_POSTGRES_POOL_SIZE` | `src/gprofiler-dev/gprofiler_dev/config.py` | `10` | Max DB connections in each process's pool = max parallel DB ops per worker. |
| `GPROFILER_POSTGRES_POOL_ACQUIRE_TIMEOUT` | `src/gprofiler-dev/gprofiler_dev/config.py` | `10` (seconds) | How long a thread waits for a free pooled connection before the request errors. |
| `GUNICORN_PROCESS_COUNT` | `src/gprofiler/run.sh` | `nproc` | Number of gunicorn worker **processes** per replica. Each process has its own pool. |
| `GPROFILER_WEBAPP_THREAD_POOL_SIZE` | `src/gprofiler/backend/config.py` | `0` (anyio default 40) | Per-worker threadpool size for sync routes = max concurrent requests per worker. Requests beyond the pool size wait for a connection. |
| `GPROFILER_POSTGRES_CONN_PER_THREAD` | `src/gprofiler-dev/gprofiler_dev/config.py` | `FALSE` | **Deprecated / ignored.** The old per-thread-connection mode; replaced by the bounded pool. |

### `GPROFILER_POSTGRES_POOL_SIZE` — the primary knob

Caps how many DB operations a single worker runs at once (and how many
connections it opens). Bigger = more parallelism per worker, but more connections
against the database. Total fleet connections are bounded by
`replicas × workers × pool size` (see the connection math below), so size it for
the concurrency you need — not higher.

### `GPROFILER_POSTGRES_POOL_ACQUIRE_TIMEOUT` — backpressure ceiling

When every pooled connection is busy, additional request threads wait this long
for one to free up, then fail fast. Keep it at or below your load-balancer
timeout so a saturated pool sheds load instead of piling up.

### `GUNICORN_PROCESS_COUNT` — process count

Each worker process has its own pool, so total connections scale with the worker
count. Workers are I/O-bound on the DB, so more workers than CPU cores is fine —
but every worker multiplies the connection total.

### `GPROFILER_WEBAPP_THREAD_POOL_SIZE` — request concurrency per worker

How many sync requests a worker runs at once (anyio default 40). Requests beyond
`GPROFILER_POSTGRES_POOL_SIZE` simply wait for a pooled connection, so this can
stay at the default; the DB pool is the real concurrency bound.

## Connection math

Total DB connections opened by the webapp is bounded by:

```
connections ≈ replicas × GUNICORN_PROCESS_COUNT × GPROFILER_POSTGRES_POOL_SIZE
```

Unlike the old per-thread design this is a hard cap, and connections are reused,
so there is no establishment storm. Keep the total comfortably under the
database's `max_connections` (Aurora here is `5000`) with margin for periodic
tasks and admin sessions. Connection count is necessary but not sufficient — the
writer must also have the CPU/ACU to service the work (the incident that motivated
this pool was 100% ACU, not a connection-count limit).

**Examples (per the current prod cluster, `nproc = 8`):**

| Config | Conns/replica | 6 replicas |
| --- | --- | --- |
| 8 workers, pool 10 | 80 | 480 |
| 8 workers, pool 20 | 160 | 960 |
| 16 workers, pool 10 | 160 | 960 |

## Sizing for load (Little's Law)

The number of DB operations in flight at once is `L = λ × W`, where `λ` is the
request rate and `W` is how long each request holds a connection. Your total
provisioned concurrency (`replicas × workers × pool size`) must exceed `L`, with
headroom for bursts.

The dominant driver here is the agent heartbeat: **30k hosts × 1 per 30 s ≈
1000 heartbeat QPS**. Each heartbeat is write-heavy — `upsert_host_heartbeat`
(host upsert + container/process diff-sync) plus a profiling-command lookup and
possible status updates — so `W` is on the order of tens of milliseconds:

| Heartbeat hold time `W` | Busy connections `L` at 1000 QPS |
| --- | --- |
| 30 ms | 30 |
| 50 ms | 50 |
| 100 ms | 100 |
| 150 ms | 150 |

Add read traffic on top. Coarse read scopes now serve from the store in ~10 ms
(negligible), but the **live fine scopes hold a connection for seconds**
(container ~4 s, process ~8 s), so each concurrent fine-scope request consumes
several connection-seconds. Budget generously for these.

Target total provisioned concurrency at **2–3× the computed `L`** so bursts and
slow reads don't exhaust the pool. Measure `W` from your own metrics
(`pg_stat_activity`, request latency) and re-derive — the table is a starting
estimate.

> **Connections enable parallelism, they do not create DB throughput.** 1000
> write-heavy heartbeats/s is real load on the Aurora writer regardless of how
> many connections you open. If the writer saturates (CPU, lock/WAL), the fix is
> to cut per-heartbeat cost (batch the inventory writes) or scale the DB — not to
> add more connections.

## Recommended production settings

Start conservative and raise the pool only if you need more parallelism:

```bash
# Bounded connections per worker (max parallel DB ops per worker).
GPROFILER_POSTGRES_POOL_SIZE=10

# Shed load rather than pile up when the pool is saturated.
GPROFILER_POSTGRES_POOL_ACQUIRE_TIMEOUT=10

# Process count; total connections = replicas * workers * pool size.
GUNICORN_PROCESS_COUNT=8
```

With 6 replicas × 8 workers × pool 10 = **480** connections max — reused, no
storm, well under `max_connections`. Because heartbeats are now cheap (the
inventory sync is diff-only), a modest pool comfortably covers the ~1000
heartbeat QPS plus reads; raise `GPROFILER_POSTGRES_POOL_SIZE` only if you observe
threads waiting on the pool (acquire timeouts) while the writer still has ACU
headroom.

## Verifying

Check live connection usage against the limit:

```sql
SHOW max_connections;
SELECT count(*) AS total,
       count(*) FILTER (WHERE state = 'active') AS active
FROM pg_stat_activity;
```
