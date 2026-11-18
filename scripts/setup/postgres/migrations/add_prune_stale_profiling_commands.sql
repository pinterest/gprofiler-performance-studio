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

-- Prune the profiling commands of hosts that stopped heartbeating.
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
-- kept for stale_after too.
--
-- Staleness is judged on HostHeartbeats.updated_at, which the database sets on every
-- beat, relative to the newest beat rather than to now() (as prune_stale_host_inventory
-- does): if beats stop arriving altogether, the cutoff stops moving and nothing more is
-- pruned.
--
-- A pruned host that comes back is treated like a new host: it has no command until one
-- is issued, or it is enrolled into its service's active service-wide subscription if
-- there is one. An explicit per-host start or stop therefore does not survive an absence
-- longer than stale_after, hence the long default.
--
-- Work is done in batches of batch_rows commands, one transaction each, at most
-- max_batches per call; a larger backlog drains over the following calls. Rows locked by
-- a concurrent writer are skipped and picked up by a later call. Run by the periodic-tasks
-- worker (deploy/periodic_tasks/prune_stale_profiling_commands.sh).

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
