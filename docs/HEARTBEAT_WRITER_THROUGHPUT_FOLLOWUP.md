# Follow-up: heartbeat writer throughput & lock contention

## Context

The async heartbeat writer (`HeartbeatWriter` → `DBManager.bulk_upsert_host_heartbeats`)
cannot keep up with the fleet, and its write path is prone to lock contention.
This surfaced twice:

- **Deadlocks (fixed in #105 via retry):** concurrent flushes' multi-row
  `INSERT ... ON CONFLICT` on `HostHeartbeats` deadlocked (~18 / 5 min), and a
  failed flush dropped the whole batch.
- **Lock convoy (reverted after #105):** forcing a deterministic batch order to
  avoid the deadlocks made all workers serialize on the same hot rows while
  holding them through the long inventory sync — transactions ran 300–600 s+
  with 50+ writers blocked.

Both are symptoms of the same root cause.

## Root cause

At full scale:

- Incoming: ~1,100 heartbeats/s (fleet ~33k, agents beat every ~30 s).
- Persisted: only ~20–38 distinct hosts/s.
- Each flush transaction runs the **parent `HostHeartbeats` upsert and the
  per-host inventory sync in one transaction**, holding row locks on the hot
  parent rows for the entire (long) inventory pass.

The inventory sync is **per host**: for each host in the batch it runs a
`DELETE` + guarded `INSERT` + `UPDATE` + `SELECT` on `HeartbeatContainers`, and
for each container a `DELETE`/`INSERT`/`UPDATE` on `HeartbeatProcesses`. That is
dozens of sequential round-trips per host, so a batch of a few thousand hosts
holds locks for hundreds of seconds. Long lock hold + cross-worker batch overlap
⇒ deadlock (random order) or convoy (sorted order), and low throughput either
way.

## Proposed fix

### 1. Short parent-upsert transaction
Commit the `HostHeartbeats` upsert (the `execute_values` INSERT ... ON CONFLICT)
in its **own small transaction**, so the hot rows every freshness query reads are
locked for milliseconds, not minutes. Capture the `RETURNING id, hostname,
service_name` mapping, commit, then do inventory separately.

### 2. Bulk the inventory sync across the whole batch
Replace the per-host loop with **set-based** operations over the entire batch:

- Build `(host_id, container_id, …)` rows for every container across all hosts;
  one `execute_values` insert-new + one `UPDATE ... FROM (VALUES …)` changed-only
  + one bulk prune (`DELETE … WHERE (host_id, container_id) NOT IN batch`).
- Same shape for `HeartbeatProcesses` keyed by `(container_row_id, pid)`.

This turns `O(hosts × containers)` round-trips into a handful per flush, so the
inventory transaction is short and touches each row once.

### 3. Keep the bounded deadlock-retry
With short transactions, contention drops sharply; the existing retry mops up
the rare residual deadlock. **Do not** re-introduce a global batch sort.

### Optional: reduce cross-worker overlap
If contention persists, shard hosts to a single writer by hashing
`(hostname, service_name)` so a given host is only ever written by one worker —
eliminating cross-worker contention entirely.

## Expected outcome
- Persist throughput rises toward the incoming rate; per-host freshness returns
  to ~tens of seconds.
- Deadlocks/convoys become negligible.
- The workload "fresh" window (`GPROFILER_WORKLOAD_FRESH_INTERVAL`, currently
  `15 minutes`) can be tightened back down once freshness recovers.

## Validation plan
- Spec-test `bulk_upsert_host_heartbeats` inventory correctness (insert / update
  changed-only / prune removed) against a real Postgres in the dev container.
- Load-test in the dev container: N concurrent writer threads upserting
  overlapping host sets; assert no dropped batches and short transaction times.
- Prod canary: after deploy, confirm `pg_stat_activity` shows no multi-second
  `HostHeartbeats` write transactions and `updated_at` write rate approaches the
  incoming beat rate.

## Risk / notes
- Splitting parent and inventory into two transactions means a reader can briefly
  see a host whose inventory is one beat behind — acceptable (inventory is
  eventually consistent and re-sent every beat).
- Bulk prune must scope deletes per host set actually present in the batch to
  avoid deleting inventory for hosts not in this flush.
