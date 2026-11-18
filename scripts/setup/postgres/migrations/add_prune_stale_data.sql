--
-- Copyright (C) 2023 Intel Corporation
--
-- Licensed under the Apache License, Version 2.0 (the "License");
-- you may not use this file except in compliance with the License.
-- You may obtain a copy of the License at
--
--    http://www.apache.org/licenses/LICENSE-2.0
--
-- Unless required by applicable law or agreed to in writing, software
-- distributed under the License is distributed on an "AS IS" BASIS,
-- WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
-- See the License for the specific language governing permissions and
-- limitations under the License.
--

-- Prune the rows that nothing else ever deletes and that pile up with host churn.
--
-- The periodic-tasks worker (deploy/periodic_tasks/prune_stale_data.sh) calls these every
-- 10 minutes, each with its own retention, after prune_stale_host_inventory
-- (migrations/add_prune_stale_host_inventory.sql):
--
--   prune_stale_profiling_commands   the last profiling command of a departed host
--   prune_stale_host_heartbeats      the HostHeartbeats row of a departed host
--   prune_old_profiling_executions   profiling executions nothing has touched in a while
--   prune_stale_profiler_processes   agent processes not seen in a while (sooner if they
--                                    ran for under 2 minutes)
--
-- All four collect candidates first, then delete them in batches of batch_rows, one
-- transaction each, at most max_batches per call; a larger backlog drains over the
-- following calls. Each batch is re-checked under the row lock, and rows locked by a
-- concurrent writer are skipped and picked up by a later call. Host and agent-process
-- staleness is measured against the newest beat or upload rather than now(): if they stop
-- arriving altogether, the cutoff stops moving and nothing more is pruned.


-- prune_stale_profiling_commands
--
-- ProfilingCommands holds one row per (hostname, service_name) and nothing deletes it, so
-- every host that ever received a command keeps its last command after it goes away
-- (scale-in, node replacement, decommission). With host churn most of the table ends up
-- belonging to hosts that no longer exist. The workload views join every command row,
-- and a new host that reuses a departed host's name inherits that host's last command.
--
-- A command is pruned when its host has not beaten for stale_after and the command itself
-- was not issued in that time either (the application resets created_at whenever it
-- re-issues a command), so a command created for a host that has not registered yet is
-- kept for stale_after too. Staleness is judged on HostHeartbeats.updated_at, which the
-- database sets on every beat.
--
-- A pruned host that comes back is treated like a new host: it has no command until one
-- is issued, or it is enrolled into its service's active service-wide subscription if
-- there is one. An explicit per-host start or stop therefore does not survive an absence
-- longer than stale_after, hence the long default.

CREATE OR REPLACE PROCEDURE prune_stale_profiling_commands(
    IN stale_after interval DEFAULT '7 days',
    IN batch_rows integer DEFAULT 1000,
    IN max_batches integer DEFAULT 50
)
    LANGUAGE plpgsql
AS $procedure$
DECLARE
    cutoff     timestamp;
    candidates bigint[];
    n_rows     bigint;
    n_commands bigint := 0;
    t0         timestamptz := clock_timestamp();
BEGIN
    SELECT MAX(updated_at) - stale_after INTO cutoff FROM HostHeartbeats;

    SELECT array_agg(id ORDER BY id) INTO candidates
    FROM (
        SELECT c.id
        FROM ProfilingCommands c
        WHERE c.created_at < cutoff
          AND NOT EXISTS (
              SELECT 1 FROM HostHeartbeats h
              WHERE h.hostname = c.hostname
                AND h.service_name = c.service_name
                AND h.updated_at >= cutoff
          )
        ORDER BY c.id
        LIMIT batch_rows * max_batches
    ) s;

    FOR i IN 0 .. coalesce(cardinality(candidates), 0) - 1 BY batch_rows LOOP
        -- Re-checked under the row lock; SKIP LOCKED: a locked command is being re-issued
        -- or updated right now.
        WITH stale AS (
            SELECT c.id
            FROM ProfilingCommands c
            WHERE c.id = ANY (candidates[i + 1 : i + batch_rows])
              AND c.created_at < cutoff
              AND NOT EXISTS (
                  SELECT 1 FROM HostHeartbeats h
                  WHERE h.hostname = c.hostname
                    AND h.service_name = c.service_name
                    AND h.updated_at >= cutoff
              )
            FOR UPDATE OF c SKIP LOCKED
        )
        DELETE FROM ProfilingCommands c USING stale WHERE c.id = stale.id;
        GET DIAGNOSTICS n_rows = ROW_COUNT;
        n_commands := n_commands + n_rows;
        COMMIT;
    END LOOP;

    RAISE NOTICE 'prune_stale_profiling_commands: deleted % commands of hosts with no beat for %, in %',
        n_commands, stale_after, clock_timestamp() - t0;
END;
$procedure$;


-- prune_stale_host_heartbeats
--
-- HostHeartbeats holds one row per (hostname, service_name) and nothing deletes it either:
-- prune_stale_host_inventory empties a departed host's inventory but keeps the row. The
-- table has no index on beat time (by design, see drop_redundant_heartbeat_indexes.sql),
-- so every reader that filters by recency scans the rows of every host that ever beat.
--
-- A row is pruned once its host has not beaten for stale_after, unless the host still has
-- a profiling command: the row is the only record of when the host was last seen, which
-- prune_stale_profiling_commands needs to keep the command for its own, longer window.
-- Once the command is pruned, the row goes on the next call. Deleting the row cascades to
-- whatever inventory is left. A host that comes back gets a new row on its next beat and
-- re-sends its full inventory.

CREATE OR REPLACE PROCEDURE prune_stale_host_heartbeats(
    IN stale_after interval DEFAULT '1 day',
    IN batch_rows integer DEFAULT 1000,
    IN max_batches integer DEFAULT 50
)
    LANGUAGE plpgsql
AS $procedure$
DECLARE
    cutoff     timestamp;
    candidates bigint[];
    n_rows     bigint;
    n_hosts    bigint := 0;
    t0         timestamptz := clock_timestamp();
BEGIN
    SELECT MAX(updated_at) - stale_after INTO cutoff FROM HostHeartbeats;

    SELECT array_agg(id ORDER BY id) INTO candidates
    FROM (
        SELECT h.id
        FROM HostHeartbeats h
        WHERE h.updated_at < cutoff
          AND NOT EXISTS (
              SELECT 1 FROM ProfilingCommands c
              WHERE c.hostname = h.hostname
                AND c.service_name = h.service_name
          )
        ORDER BY h.id
        LIMIT batch_rows * max_batches
    ) s;

    FOR i IN 0 .. coalesce(cardinality(candidates), 0) - 1 BY batch_rows LOOP
        -- Re-checked under the row lock; SKIP LOCKED: a locked host is beating right now.
        WITH stale AS (
            SELECT h.id
            FROM HostHeartbeats h
            WHERE h.id = ANY (candidates[i + 1 : i + batch_rows])
              AND h.updated_at < cutoff
              AND NOT EXISTS (
                  SELECT 1 FROM ProfilingCommands c
                  WHERE c.hostname = h.hostname
                    AND c.service_name = h.service_name
              )
            FOR UPDATE OF h SKIP LOCKED
        )
        DELETE FROM HostHeartbeats h USING stale WHERE h.id = stale.id;
        GET DIAGNOSTICS n_rows = ROW_COUNT;
        n_hosts := n_hosts + n_rows;
        COMMIT;
    END LOOP;

    RAISE NOTICE 'prune_stale_host_heartbeats: deleted % hosts with no beat for % and no profiling command, in %',
        n_hosts, stale_after, clock_timestamp() - t0;
END;
$procedure$;


-- prune_old_profiling_executions
--
-- ProfilingExecutions gets a row per host for every command sent to it and keeps it
-- forever. An execution is pruned once nothing has touched it (created, re-assigned or
-- completed) for older_than, unless it belongs to its host's current command, which a
-- completion report looks up. A completion reported for an older command whose execution
-- is gone is rejected like one for an unknown command. Age here is plain wall-clock age.

CREATE OR REPLACE PROCEDURE prune_old_profiling_executions(
    IN older_than interval DEFAULT '30 days',
    IN batch_rows integer DEFAULT 1000,
    IN max_batches integer DEFAULT 50
)
    LANGUAGE plpgsql
AS $procedure$
DECLARE
    cutoff       timestamp := LOCALTIMESTAMP - older_than;
    candidates   bigint[];
    n_rows       bigint;
    n_executions bigint := 0;
    t0           timestamptz := clock_timestamp();
BEGIN
    SELECT array_agg(id ORDER BY id) INTO candidates
    FROM (
        SELECT e.id
        FROM ProfilingExecutions e
        WHERE GREATEST(e.created_at, e.started_at, e.completed_at) < cutoff
          AND NOT EXISTS (
              SELECT 1 FROM ProfilingCommands c
              WHERE c.command_id = e.command_id
                AND c.hostname = e.hostname
          )
        ORDER BY e.id
        LIMIT batch_rows * max_batches
    ) s;

    FOR i IN 0 .. coalesce(cardinality(candidates), 0) - 1 BY batch_rows LOOP
        -- Re-checked under the row lock; SKIP LOCKED: a locked execution is being updated
        -- right now.
        WITH old AS (
            SELECT e.id
            FROM ProfilingExecutions e
            WHERE e.id = ANY (candidates[i + 1 : i + batch_rows])
              AND GREATEST(e.created_at, e.started_at, e.completed_at) < cutoff
              AND NOT EXISTS (
                  SELECT 1 FROM ProfilingCommands c
                  WHERE c.command_id = e.command_id
                    AND c.hostname = e.hostname
              )
            FOR UPDATE OF e SKIP LOCKED
        )
        DELETE FROM ProfilingExecutions e USING old WHERE e.id = old.id;
        GET DIAGNOSTICS n_rows = ROW_COUNT;
        n_executions := n_executions + n_rows;
        COMMIT;
    END LOOP;

    RAISE NOTICE 'prune_old_profiling_executions: deleted % executions untouched for %, in %',
        n_executions, older_than, clock_timestamp() - t0;
END;
$procedure$;


-- prune_stale_profiler_processes
--
-- ProfilerProcesses gets a row for every agent process that registers (one per agent
-- start) and keeps it forever; uploads refresh its last_seen. Agents that crash-loop or run
-- in short-lived containers add rows by the million. A row is pruned once it has not been
-- seen for stale_after, or for 7 days if the process ran for under 2 minutes
-- (last_seen - ts). The nodes-and-cores graph is the only reader of past rows: it shows
-- nothing for windows older than stale_after and leaves those short-lived processes out of
-- windows older than 7 days.

CREATE OR REPLACE PROCEDURE prune_stale_profiler_processes(
    IN stale_after interval DEFAULT '90 days',
    IN batch_rows integer DEFAULT 1000,
    IN max_batches integer DEFAULT 50
)
    LANGUAGE plpgsql
AS $procedure$
DECLARE
    short_lived       constant interval := '2 minutes';
    short_lived_after constant interval := '7 days';
    newest            timestamp;
    cutoff            timestamp;
    short_cutoff      timestamp;
    candidates        bigint[];
    n_rows            bigint;
    n_processes       bigint := 0;
    t0                timestamptz := clock_timestamp();
BEGIN
    SELECT MAX(last_seen) INTO newest FROM ProfilerProcesses;
    cutoff := newest - stale_after;
    short_cutoff := newest - short_lived_after;

    SELECT array_agg(id ORDER BY id) INTO candidates
    FROM (
        SELECT p.id
        FROM ProfilerProcesses p
        -- The first bound is implied by the second; it lets the last_seen index serve both rules.
        WHERE p.last_seen < GREATEST(cutoff, short_cutoff)
          AND (p.last_seen < cutoff OR (p.last_seen < short_cutoff AND p.last_seen - p.ts < short_lived))
        ORDER BY p.last_seen
        LIMIT batch_rows * max_batches
    ) s;

    FOR i IN 0 .. coalesce(cardinality(candidates), 0) - 1 BY batch_rows LOOP
        -- Re-checked under the row lock; SKIP LOCKED: a locked process is being updated by an
        -- upload right now.
        WITH stale AS (
            SELECT p.id
            FROM ProfilerProcesses p
            WHERE p.id = ANY (candidates[i + 1 : i + batch_rows])
              AND (p.last_seen < cutoff OR (p.last_seen < short_cutoff AND p.last_seen - p.ts < short_lived))
            FOR UPDATE SKIP LOCKED
        )
        DELETE FROM ProfilerProcesses p USING stale WHERE p.id = stale.id;
        GET DIAGNOSTICS n_rows = ROW_COUNT;
        n_processes := n_processes + n_rows;
        COMMIT;
    END LOOP;

    RAISE NOTICE 'prune_stale_profiler_processes: deleted % agent processes not seen for % (% if they ran under 2 minutes), in %',
        n_processes, stale_after, short_lived_after, clock_timestamp() - t0;
END;
$procedure$;
