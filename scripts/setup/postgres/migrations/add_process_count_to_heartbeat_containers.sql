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

-- Per-container process counter, so fleet-wide process counts become a SUM over
-- HeartbeatContainers (~1.5M rows) instead of a scan of HeartbeatProcesses (~100M rows).
--
-- The inventory sync maintains the column from this release on, in the same guarded
-- INSERT / UPDATE that writes the container row (no extra statements). This migration
--   1. adds the column,
--   2. backfills it for containers that exist today,
--   3. redefines refresh_workload_snapshot() so the precomputed tab counts and
--      service/namespace/pod summaries sum the counter instead of reading
--      HeartbeatProcesses (and defaults its freshness window to 2 minutes, matching
--      GPROFILER_WORKLOAD_FRESH_INTERVAL).
--
-- Order: run after add_inventory_digest_to_hostheartbeats.sql. Safe to re-run.
--
-- Backfill cost: one UPDATE per batch of 10,000 containers, each committed separately so
-- no batch holds row locks for more than a few hundred ms and concurrent heartbeat syncs
-- are never blocked for long. At ~1.5M containers expect a few minutes end to end.
-- Run with psql outside a transaction block (CALL commits inside the loop).

ALTER TABLE HeartbeatContainers ADD COLUMN IF NOT EXISTS process_count integer NOT NULL DEFAULT 0;

CREATE OR REPLACE PROCEDURE backfill_heartbeat_container_process_count(batch_size integer DEFAULT 10000)
    LANGUAGE plpgsql
AS $$
DECLARE
    lo bigint := 0;
    hi bigint;
    max_id bigint;
BEGIN
    SELECT COALESCE(MAX(id), 0) INTO max_id FROM HeartbeatContainers;
    WHILE lo < max_id LOOP
        hi := lo + batch_size;
        UPDATE HeartbeatContainers hc
        SET process_count = sub.cnt
        FROM (
            SELECT c.id, COUNT(hp.id) AS cnt
            FROM HeartbeatContainers c
            LEFT JOIN HeartbeatProcesses hp ON hp.container_row_id = c.id
            WHERE c.id > lo AND c.id <= hi
            GROUP BY c.id
        ) sub
        WHERE hc.id = sub.id AND hc.process_count IS DISTINCT FROM sub.cnt;
        COMMIT;
        lo := hi;
    END LOOP;
END;
$$;

CALL backfill_heartbeat_container_process_count();

DROP PROCEDURE backfill_heartbeat_container_process_count(integer);

-- Optimized workload_status refresh.
--
-- The first version built Layer 2 by aggregating the flat, process-grain
-- workload_snapshot (~1.9M rows) with naive 7x COUNT(DISTINCT) tab counts and
-- array_agg(DISTINCT pid) coarse GROUP BYs -- the exact patterns the live
-- endpoint was optimized away from -- which took ~18 min at prod scale and,
-- under the ~30s cron, piled up on the meta-row lock.
--
-- This version:
--   * Computes Layer 2 (tab counts + service/namespace/pod summaries) from the
--     SOURCE tables at the appropriate grain, reusing the tiered / two-grain
--     tricks (COUNT(*) FROM (SELECT DISTINCT ...) counts; container-grain
--     aggregates; host-grain any_active). Prod read-only prototypes: tab_counts
--     7.7s, service summary 5.3s.
--   * Never reads HeartbeatProcesses (~100M rows at fleet scale): every process
--     total is SUM(HeartbeatContainers.process_count), a per-container counter the
--     inventory sync maintains. Process counts are therefore "processes on fresh
--     hosts in the group", the same quantity the process tab counts.
--   * Guards against overlap with a non-blocking advisory lock, so a slow build
--     can never queue behind another (no pile-up), regardless of cron cadence.
--   * No longer populates the Layer 1 workload_snapshot table (nothing reads it
--     yet -- filtered reads still use the live path). Re-introduce an optimized
--     snapshot build when the filtered-read-from-snapshot path is implemented.
--
-- any_active for the coarse scopes is computed at the host grain (a host with an
-- active whole-host command marks its groups active). For service scope this is
-- provably identical to the PID-aware value; for namespace/pod it can over-mark
-- only under PID-targeted commands, which are effectively unused in practice.

CREATE OR REPLACE PROCEDURE refresh_workload_snapshot(IN fresh_interval interval DEFAULT '2 minutes')
    LANGUAGE plpgsql
AS $procedure$
DECLARE
    cur_gen  smallint;
    new_gen  smallint;
    sum_tbl  text;
    cnt_tbl  text;
    t0       timestamptz := clock_timestamp();
    src_cte  text;
    agg_tail text;
    sum_cols text := 'scope, sort_seq, row_id, service_name, hostname, namespace, pod_name, container_name,'
                  || 'host_count, namespace_count, pod_count, container_count, process_count, pids,'
                  || 'l_hostname, l_ip_address, l_namespace, l_pod_name, l_container_name, l_workload_name, l_workload_kind,'
                  || 'l_process_name, l_pid, l_command_type, l_command_status, l_combined_config, any_active, l_profiling_status,'
                  || 'l_agent_version, l_run_mode, l_heartbeat_timestamp';
BEGIN
    -- Non-blocking guard: if a refresh is already running, skip instead of queuing.
    IF NOT pg_try_advisory_lock(3126073) THEN
        RAISE NOTICE 'refresh_workload_snapshot: another refresh is running, skipping';
        RETURN;
    END IF;

    SELECT active_generation INTO cur_gen FROM workload_snapshot_meta WHERE id = 1;
    new_gen := 1 - cur_gen;
    sum_tbl := 'workload_scope_summary_' || new_gen;
    cnt_tbl := 'workload_tab_counts_'    || new_gen;

    EXECUTE format('TRUNCATE %I', sum_tbl);
    EXECUTE format('TRUNCATE %I', cnt_tbl);

    -- Tab counts: tiered, each counted at the shallowest grain that exposes it,
    -- via COUNT(*) FROM (SELECT DISTINCT ...) (hash-distinct, not sort-per-agg).
    EXECUTE format($f$
        INSERT INTO %1$I (scope, count)
        WITH fresh AS MATERIALIZED (
            SELECT id, hostname, service_name FROM HostHeartbeats
            WHERE heartbeat_timestamp > NOW() - %2$L::interval
        )
        SELECT k, v FROM (
            SELECT
                (SELECT COUNT(DISTINCT service_name) FROM fresh) AS c_service,
                (SELECT COUNT(DISTINCT hostname) FROM fresh) AS active_hosts,
                (SELECT COUNT(*) FROM (SELECT DISTINCT service_name, hostname FROM fresh) d) AS c_host,
                (SELECT COUNT(*) FROM (SELECT DISTINCT f.service_name, hc.namespace
                    FROM fresh f JOIN HeartbeatContainers hc ON hc.host_id = f.id
                    WHERE hc.namespace IS NOT NULL) d) AS c_namespace,
                (SELECT COUNT(*) FROM (SELECT DISTINCT f.service_name, hc.namespace, hc.pod_name
                    FROM fresh f JOIN HeartbeatContainers hc ON hc.host_id = f.id
                    WHERE hc.pod_name IS NOT NULL) d) AS c_pod,
                (SELECT COUNT(*) FROM (SELECT DISTINCT f.service_name, f.hostname, hc.namespace, hc.pod_name, hc.container_name
                    FROM fresh f JOIN HeartbeatContainers hc ON hc.host_id = f.id
                    WHERE hc.container_name IS NOT NULL) d) AS c_container,
                (SELECT COALESCE(SUM(hc.process_count), 0)
                    FROM fresh f JOIN HeartbeatContainers hc ON hc.host_id = f.id) AS c_process
        ) t,
        LATERAL (VALUES ('service', c_service), ('host', c_host), ('namespace', c_namespace),
                        ('pod', c_pod), ('container', c_container), ('process', c_process),
                        ('active_hosts', active_hosts)) x(k, v)
    $f$, cnt_tbl, fresh_interval);

    -- Shared container-grain source for the coarse summaries. process_count is the
    -- per-container counter maintained by the inventory sync, so every process total
    -- below is a SUM over this relation and HeartbeatProcesses is never read here.
    src_cte := format($c$
        fresh AS MATERIALIZED (
            SELECT id, hostname, host(ip_address) AS ip_address, service_name, agent_version, run_mode, heartbeat_timestamp
            FROM HostHeartbeats WHERE heartbeat_timestamp > NOW() - %L::interval
        ),
        cc AS (SELECT hostname, service_name, command_type, status, combined_config FROM ProfilingCommands),
        cont AS (
            SELECT f.id, f.hostname, f.ip_address, f.service_name, f.agent_version, f.run_mode, f.heartbeat_timestamp,
                   hc.namespace, hc.pod_name, hc.container_name, hc.workload_name, hc.workload_kind,
                   COALESCE(hc.process_count, 0) AS process_count,
                   COALESCE(c.command_type, 'N/A') AS command_type, c.status AS command_status, c.combined_config,
                   (c.command_type = 'start' AND c.status IN ('pending','sent','completed')) AS host_active
            FROM fresh f
            LEFT JOIN cc c ON c.hostname = f.hostname AND c.service_name = f.service_name
            LEFT JOIN HeartbeatContainers hc ON hc.host_id = f.id
        )
    $c$, fresh_interval);

    -- Per-group aggregate tail over the container-grain `cont` relation (unqualified cols).
    agg_tail := $a$
        COUNT(DISTINCT hostname) AS host_count,
        COUNT(DISTINCT namespace) FILTER (WHERE namespace IS NOT NULL) AS namespace_count,
        COUNT(DISTINCT pod_name) FILTER (WHERE pod_name IS NOT NULL) AS pod_count,
        COUNT(DISTINCT container_name) FILTER (WHERE container_name IS NOT NULL) AS container_count,
        SUM(process_count)::integer AS process_count,
        bool_or(host_active) AS any_active,
        (array_agg(hostname ORDER BY heartbeat_timestamp DESC NULLS LAST))[1] AS l_hostname,
        (array_agg(ip_address ORDER BY heartbeat_timestamp DESC NULLS LAST))[1] AS l_ip_address,
        (array_agg(namespace ORDER BY heartbeat_timestamp DESC NULLS LAST))[1] AS l_namespace,
        (array_agg(pod_name ORDER BY heartbeat_timestamp DESC NULLS LAST))[1] AS l_pod_name,
        (array_agg(container_name ORDER BY heartbeat_timestamp DESC NULLS LAST))[1] AS l_container_name,
        (array_agg(workload_name ORDER BY heartbeat_timestamp DESC NULLS LAST))[1] AS l_workload_name,
        (array_agg(workload_kind ORDER BY heartbeat_timestamp DESC NULLS LAST))[1] AS l_workload_kind,
        (array_agg(command_type ORDER BY heartbeat_timestamp DESC NULLS LAST))[1] AS l_command_type,
        (array_agg(command_status ORDER BY heartbeat_timestamp DESC NULLS LAST))[1] AS l_command_status,
        (array_agg(combined_config ORDER BY heartbeat_timestamp DESC NULLS LAST))[1] AS l_combined_config,
        (array_agg(agent_version ORDER BY heartbeat_timestamp DESC NULLS LAST))[1] AS l_agent_version,
        (array_agg(run_mode ORDER BY heartbeat_timestamp DESC NULLS LAST))[1] AS l_run_mode,
        MAX(heartbeat_timestamp) AS l_heartbeat_timestamp
    $a$;

    -- service
    EXECUTE format($f$
        INSERT INTO %1$I (%2$s)
        WITH %3$s
        SELECT 'service', row_number() OVER (ORDER BY ca.service_name ASC NULLS LAST), ca.service_name,
               ca.service_name, NULL, NULL, NULL, NULL,
               ca.host_count, ca.namespace_count, ca.pod_count, ca.container_count, COALESCE(ca.process_count, 0), NULL::integer[],
               ca.l_hostname, ca.l_ip_address, ca.l_namespace, ca.l_pod_name, ca.l_container_name, ca.l_workload_name, ca.l_workload_kind,
               NULL::text, NULL::integer, ca.l_command_type, ca.l_command_status, ca.l_combined_config, ca.any_active, NULL::text,
               ca.l_agent_version, ca.l_run_mode, ca.l_heartbeat_timestamp
        FROM (SELECT service_name, %4$s FROM cont GROUP BY service_name) ca
    $f$, sum_tbl, sum_cols, src_cte, agg_tail);

    -- namespace
    EXECUTE format($f$
        INSERT INTO %1$I (%2$s)
        WITH %3$s
        SELECT 'namespace', row_number() OVER (ORDER BY ca.service_name ASC NULLS LAST, ca.namespace ASC NULLS LAST),
               ca.service_name || '|' || COALESCE(ca.namespace, ''),
               ca.service_name, NULL, ca.namespace, NULL, NULL,
               ca.host_count, ca.namespace_count, ca.pod_count, ca.container_count, COALESCE(ca.process_count, 0), NULL::integer[],
               ca.l_hostname, ca.l_ip_address, ca.l_namespace, ca.l_pod_name, ca.l_container_name, ca.l_workload_name, ca.l_workload_kind,
               NULL::text, NULL::integer, ca.l_command_type, ca.l_command_status, ca.l_combined_config, ca.any_active, NULL::text,
               ca.l_agent_version, ca.l_run_mode, ca.l_heartbeat_timestamp
        FROM (SELECT service_name, namespace, %4$s FROM cont WHERE namespace IS NOT NULL GROUP BY service_name, namespace) ca
    $f$, sum_tbl, sum_cols, src_cte, agg_tail);

    -- pod
    EXECUTE format($f$
        INSERT INTO %1$I (%2$s)
        WITH %3$s
        SELECT 'pod', row_number() OVER (ORDER BY ca.service_name ASC NULLS LAST, ca.namespace ASC NULLS LAST, ca.pod_name ASC NULLS LAST),
               ca.service_name || '|' || COALESCE(ca.namespace, '') || '|' || COALESCE(ca.pod_name, ''),
               ca.service_name, NULL, ca.namespace, ca.pod_name, NULL,
               ca.host_count, ca.namespace_count, ca.pod_count, ca.container_count, COALESCE(ca.process_count, 0), NULL::integer[],
               ca.l_hostname, ca.l_ip_address, ca.l_namespace, ca.l_pod_name, ca.l_container_name, ca.l_workload_name, ca.l_workload_kind,
               NULL::text, NULL::integer, ca.l_command_type, ca.l_command_status, ca.l_combined_config, ca.any_active, NULL::text,
               ca.l_agent_version, ca.l_run_mode, ca.l_heartbeat_timestamp
        FROM (SELECT service_name, namespace, pod_name, %4$s FROM cont WHERE pod_name IS NOT NULL GROUP BY service_name, namespace, pod_name) ca
    $f$, sum_tbl, sum_cols, src_cte, agg_tail);

    EXECUTE format('ANALYZE %I', sum_tbl);

    -- Atomic swap of the read-side views + flip the pointer, in this transaction.
    EXECUTE format('CREATE OR REPLACE VIEW workload_scope_summary AS SELECT * FROM %I', sum_tbl);
    EXECUTE format('CREATE OR REPLACE VIEW workload_tab_counts AS SELECT * FROM %I', cnt_tbl);

    UPDATE workload_snapshot_meta
    SET active_generation = new_gen,
        built_at = clock_timestamp(),
        build_duration_ms = (EXTRACT(EPOCH FROM (clock_timestamp() - t0)) * 1000)::integer
    WHERE id = 1;

    PERFORM pg_advisory_unlock(3126073);
END;
$procedure$;
