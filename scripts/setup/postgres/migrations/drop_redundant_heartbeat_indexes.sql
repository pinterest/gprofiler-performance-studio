-- Drop seven indexes on the heartbeat / command tables that no query needs.
--
-- Why: every index is maintained on every insert, and on every UPDATE that touches an
-- indexed column. The heartbeat path is the write-heaviest path in the system (one
-- HostHeartbeats UPDATE per host per 30 s; HeartbeatProcesses is one row per process
-- fleet-wide), so unused indexes there are paid for continuously.
--
--   1. idx_hostheartbeats_heartbeat_timestamp
--        heartbeat_timestamp is rewritten on every beat. With an index on it Postgres
--        cannot do a heap-only (in-place) update: each beat writes a new row version and
--        updates every index on the table. Every reader filters by recency over the
--        whole table (<= ~150k rows), which is a cheap sequential scan without it.
--   2. idx_hb_processes_process_name
--        A text index over the largest table. Nothing filters by process_name without
--        first narrowing to a container (which uses the UNIQUE key).
--   3. idx_hostheartbeats_hostname            -> prefix of unique_host_heartbeat (hostname, service_name)
--   4. idx_hb_containers_host_id              -> prefix of unique_heartbeat_container (host_id, container_id)
--   5. idx_hb_processes_container_row_id      -> prefix of unique_heartbeat_process (container_row_id, pid)
--   6. idx_profilingcommands_hostname         -> prefix of unique_profiling_command_per_host (hostname, service_name)
--   7. idx_profilingcommands_hostname_service -> exact duplicate of that UNIQUE constraint
--        A B-tree on (a, b) serves every lookup on (a) alone, so 3-7 are pure duplicates.
--
-- Ordering: run this BEFORE the inventory_digest rollout. The first deploy of
-- change-detected inventory performs a one-time load of the whole fleet's inventory
-- (every host's first beat differs), and each inserted row maintains every index that
-- still exists.
--
-- Safety: DROP INDEX CONCURRENTLY takes no lock that blocks reads or writes, and is
-- idempotent via IF EXISTS. It cannot run inside a transaction block, so apply this
-- file with autocommit on (plain `psql -f` is fine; do not wrap in BEGIN/COMMIT).
-- Undo = CREATE INDEX CONCURRENTLY with the definitions from gprofiler_recreate.sql
-- history; older application code does not depend on any of these indexes.

DROP INDEX CONCURRENTLY IF EXISTS idx_hostheartbeats_heartbeat_timestamp;
DROP INDEX CONCURRENTLY IF EXISTS idx_hb_processes_process_name;
DROP INDEX CONCURRENTLY IF EXISTS idx_hostheartbeats_hostname;
DROP INDEX CONCURRENTLY IF EXISTS idx_hb_containers_host_id;
DROP INDEX CONCURRENTLY IF EXISTS idx_hb_processes_container_row_id;
DROP INDEX CONCURRENTLY IF EXISTS idx_profilingcommands_hostname;
DROP INDEX CONCURRENTLY IF EXISTS idx_profilingcommands_hostname_service;
