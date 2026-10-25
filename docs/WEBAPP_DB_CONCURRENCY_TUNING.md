# Webapp DB Concurrency Tuning

How the webapp (`gprofiler_frontend` container) talks to Postgres, which two
environment variables control it, and how to set them for a large fleet.

## What runs inside one webapp container

```
nginx
 └─ gunicorn (process manager)
     └─ GUNICORN_PROCESS_COUNT worker processes (default: nproc)
         └─ each: a threadpool of GPROFILER_WEBAPP_THREAD_POOL_SIZE threads (default 40)
             └─ each thread: exactly one Postgres connection, opened lazily, kept for life
```

Route handlers are synchronous `def` functions, so Starlette runs each request on
a thread from the per-process pool. `gprofiler_dev.postgres.get_postgres_db()`
hands every thread its own `PostgresDB` connection (psycopg2 connections must not
be shared between threads). There is no separate connection pool and no buffering
layer; the thread count *is* the connection count.

```
connections ≈ containers × GUNICORN_PROCESS_COUNT × GPROFILER_WEBAPP_THREAD_POOL_SIZE
```

With the framework default of 40 threads, 7 containers × 8 workers × 40 is
**2,240** connections — most of them idle, but all of them costing memory and
connection slots on the database. With 10 threads it is **560**.

## The knobs

| Environment variable | Where read | Default | Effect |
| --- | --- | --- | --- |
| `GPROFILER_WEBAPP_THREAD_POOL_SIZE` | `src/gprofiler/backend/config.py` | `0` (= anyio default 40) | Threads per worker process = concurrent requests per worker = Postgres connections per worker. **The knob to size connections with.** |
| `GUNICORN_PROCESS_COUNT` | `src/gprofiler/run.sh` | `nproc` | Worker processes per container. Multiplies the thread/connection count. |
| `GPROFILER_INVENTORY_SYNC_PER_SEC` | `src/gprofiler-dev/gprofiler_dev/config.py` | `2` | Full inventory syncs admitted per second *per worker process* when a host's inventory has changed (see below). `0` or negative = unlimited. |

Everything else that used to be here (`GPROFILER_POSTGRES_POOL_SIZE`,
`GPROFILER_POSTGRES_POOL_ACQUIRE_TIMEOUT`, `GPROFILER_POSTGRES_CONN_PER_THREAD`,
`GPROFILER_HEARTBEAT_ASYNC_WRITES`, `GPROFILER_HEARTBEAT_FLUSH_INTERVAL_SEC`,
`GPROFILER_HEARTBEAT_BUFFER_MAX_HOSTS`) has been removed. They tuned a bounded
pool and a buffered heartbeat writer that are no longer in the code; setting them
has no effect.

## Why a small threadpool is enough

The dominant traffic is the agent heartbeat: every host POSTs once per 30 s, so
150k hosts ≈ **5,000 heartbeats/s** across the fleet. Each heartbeat holds its
thread's connection for one short transaction:

* **Steady state:** one `UPSERT` of the host row (freshness timestamp, status,
  command ids). A change-detecting agent sends only a digest of its inventory;
  the server compares it with the digest stored on the host row and does no
  inventory work when they match. ~1 ms.
* **Inventory changed** (pod scheduled, process started/stopped): the diff-only
  sync of `HeartbeatContainers` / `HeartbeatProcesses` for that one host, in the
  same transaction. A few ms to tens of ms depending on how much changed.

By Little's law the number of busy connections is `rate × hold time`:
5,000/s × ~1 ms ≈ **5 busy connections** fleet-wide in steady state. 56 worker
processes × 10 threads = 560 connections is two orders of magnitude of headroom.
Raise the thread count only if requests queue at the threadpool while the
database still has CPU to spare — not because the connection count looks low.

> Connections enable parallelism; they do not create database throughput. If
> the writer saturates, more connections make it worse (more concurrent writers
> contending for the same rows). The fixes that worked were to cut what each
> heartbeat does (digest comparison, change-only sync), not to add connections.

## The inventory sync budget

When many hosts' inventories differ from what is stored at the same time — first
rollout of digest-based change detection (every stored row differs), a restore
from backup, a large reschedule wave — the sync work would otherwise arrive all
at once. `GPROFILER_INVENTORY_SYNC_PER_SEC` is a token bucket per worker process:
that many differing hosts per second get their inventory synced; the rest get
their freshness update, keep their old digest, and are synced on a later beat.

* Fleet-wide sync rate = value × worker processes (e.g. `2` × 56 = 112 hosts/s;
  150k hosts ≈ 23 minutes for a full initial load).
* A deferred beat costs the same ~1 ms as an unchanged one and writes no
  inventory rows, so deferring is cheap; the budget only bounds the expensive
  part.
* Watch `heartbeat_inventory{outcome="deferred"}` climb and then drain to ~0,
  and `inventory_pending_hosts` in the workload status response fall to the
  number of hosts whose agents do not report inventory at all. Raise the budget
  while the writer has headroom; lower it if write latency climbs.
* Until a host's inventory has synced, pod/container/process-scope targeting
  cannot see that host (service and host scope are unaffected).

## Recommended production settings

```bash
GUNICORN_PROCESS_COUNT=8              # per container (prod: 7 containers -> 56 workers)
GPROFILER_WEBAPP_THREAD_POOL_SIZE=10  # 560 connections fleet-wide, ~5 busy
GPROFILER_INVENTORY_SYNC_PER_SEC=0.5  # first rollout: ~28 hosts/s fleet-wide; raise to 1, then 2 as "deferred" drains
```

Dev and small deployments can leave all three unset (defaults: nproc, 40, 2).

## Verifying

```sql
SHOW max_connections;
SELECT count(*) AS total,
       count(*) FILTER (WHERE state = 'active') AS active
FROM pg_stat_activity
WHERE usename = current_user;

-- hosts whose inventory is still waiting to be synced
SELECT count(*) FROM HostHeartbeats
WHERE heartbeat_timestamp > now() - interval '2 minutes' AND inventory_digest IS NULL;
```

`total` should sit near `containers × workers × threads`; `active` should be a
handful. Postgres reaps sessions idle for more than 5 minutes
(`scripts/setup/postgres/migrations/set_idle_session_timeout.sql`), so the total
tracks recent activity rather than the high-water mark.
