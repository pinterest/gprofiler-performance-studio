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

-- Prune the workload inventory of hosts that stopped heartbeating.
--
-- HeartbeatContainers / HeartbeatProcesses rows are otherwise removed only together
-- with their HostHeartbeats row, and host rows are never deleted, so every host that
-- goes away (scale-in, node replacement, decommission) keeps its last inventory
-- forever. Nothing reads it -- every inventory reader is limited to hosts with a
-- recent beat -- but refresh_workload_snapshot scans both tables in full on every
-- run, so its build time keeps growing with hosts that no longer exist.
--
-- Staleness is judged on HostHeartbeats.updated_at, which the database sets on every
-- beat (heartbeat_timestamp is the agent's clock), and relative to the newest beat
-- rather than to now(): if beats stop arriving altogether (webapp or ingest outage)
-- the cutoff stops moving and nothing more is pruned, instead of the whole fleet's
-- inventory being dropped and re-sent once beats resume.
--
-- A pruned host's inventory_digest is cleared in the same transaction, so a host that
-- comes back is answered need_inventory=true and re-sends its full inventory. The
-- host row itself is kept. Each host's row is re-checked under its row lock, so a
-- host that beats again while the procedure runs is left alone.
--
-- Work is done in batches of batch_hosts hosts, one transaction each, at most
-- max_batches per call; a larger backlog drains over the following calls. Run by the
-- periodic-tasks worker (deploy/periodic_tasks/prune_stale_host_inventory.sh).

CREATE OR REPLACE PROCEDURE prune_stale_host_inventory(
    IN stale_after interval DEFAULT '1 hour',
    IN batch_hosts integer DEFAULT 200,
    IN max_batches integer DEFAULT 50
)
    LANGUAGE plpgsql
AS $procedure$
DECLARE
    cutoff       timestamp;
    candidates   bigint[];
    pruned       bigint[];
    n_rows       bigint;
    n_hosts      bigint := 0;
    n_containers bigint := 0;
    t0           timestamptz := clock_timestamp();
BEGIN
    SELECT MAX(updated_at) - stale_after INTO cutoff FROM HostHeartbeats;

    SELECT array_agg(id ORDER BY id) INTO candidates
    FROM (
        SELECT h.id
        FROM HostHeartbeats h
        WHERE h.updated_at < cutoff
          AND EXISTS (SELECT 1 FROM HeartbeatContainers c WHERE c.host_id = h.id)
        ORDER BY h.id
        LIMIT batch_hosts * max_batches
    ) s;

    FOR i IN 0 .. coalesce(cardinality(candidates), 0) - 1 BY batch_hosts LOOP
        -- SKIP LOCKED: a host whose row is locked is being written by a beat right now.
        WITH stale AS (
            SELECT id FROM HostHeartbeats
            WHERE id = ANY (candidates[i + 1 : i + batch_hosts])
              AND updated_at < cutoff
            FOR UPDATE SKIP LOCKED
        ), cleared AS (
            UPDATE HostHeartbeats h SET inventory_digest = NULL
            FROM stale
            WHERE h.id = stale.id
            RETURNING h.id
        )
        SELECT array_agg(id) INTO pruned FROM cleared;

        IF pruned IS NOT NULL THEN
            DELETE FROM HeartbeatContainers WHERE host_id = ANY (pruned);
            GET DIAGNOSTICS n_rows = ROW_COUNT;
            n_hosts := n_hosts + cardinality(pruned);
            n_containers := n_containers + n_rows;
        END IF;
        COMMIT;
    END LOOP;

    RAISE NOTICE 'prune_stale_host_inventory: pruned % hosts (% containers and their processes) with no beat for %, in %',
        n_hosts, n_containers, stale_after, clock_timestamp() - t0;
END;
$procedure$;
