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

-- Cheaper workload_status refresh.
--
-- The previous build (optimize_workload_precompute_build.sql) re-joined the fresh
-- hosts with their containers (and three times with their processes) in four
-- statements, and fed every scope's wide container rows to per-group COUNT(DISTINCT)
-- and (array_agg(... ORDER BY heartbeat_timestamp))[1] aggregates -- one sort per
-- aggregate per group. Its cost grows with containers and pods, so it outgrows the
-- one-minute cadence as container-reporting (e.g. Kubernetes) hosts are added.
--
-- This version reads the inventory once into narrow temp tables and keeps the passes
-- over container and process rows few:
--   * one aggregate per (service, namespace, pod) container group -- NULL namespace and
--     pod groups included -- that the pod, namespace and service rows are built from;
--   * distinct counts at the grains where rows are (nearly) unique are taken once and
--     reused. A group's distinct (host, container name) pairs are its share of the
--     container tab count and, when it spans one host, its container_count. A pod's
--     process rows are its process_count when they come from one host that lists each
--     pid once. The rare pods that span hosts, or sit on a host listing a pid twice,
--     are recounted exactly from their own rows.
-- Output (tables, columns, values, sort_seq order) is unchanged, with one refinement:
-- a row's l_* columns now always come from a single source row -- the latest-beating
-- host of the group and its lowest container row -- where the previous per-column
-- array_agg picks could mix containers of that host.
--
-- Rollback: re-run optimize_workload_precompute_build.sql.

CREATE OR REPLACE PROCEDURE refresh_workload_snapshot(IN fresh_interval interval DEFAULT '15 minutes')
    LANGUAGE plpgsql
    SET work_mem = '128MB'
AS $procedure$
DECLARE
    cur_gen  smallint;
    new_gen  smallint;
    sum_tbl  text;
    cnt_tbl  text;
    t0       timestamptz := clock_timestamp();
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

    -- Fresh hosts with their command (ProfilingCommands is unique per host and
    -- service). recency ranks hosts newest beat first; a group's l_* columns come
    -- from its lowest-recency host. svc_id ranks service names in the database
    -- collation, so ordering by svc_id is ordering by service_name.
    CREATE TEMP TABLE wl_fresh AS
    SELECT h.id AS host_id, h.hostname, host(h.ip_address) AS ip_address, h.service_name,
           h.agent_version, h.run_mode, h.heartbeat_timestamp,
           row_number() OVER (ORDER BY h.heartbeat_timestamp DESC, h.id) AS recency,
           dense_rank() OVER (ORDER BY h.service_name) AS svc_id,
           COALESCE(c.command_type, 'N/A') AS command_type, c.status AS command_status, c.combined_config,
           (c.command_type = 'start' AND c.status IN ('pending', 'sent', 'completed')) AS host_active
    FROM HostHeartbeats h
    LEFT JOIN ProfilingCommands c ON c.hostname = h.hostname AND c.service_name = h.service_name
    WHERE h.heartbeat_timestamp > NOW() - fresh_interval;
    ANALYZE wl_fresh;

    CREATE TEMP TABLE wl_svc AS
    SELECT DISTINCT svc_id, service_name FROM wl_fresh;

    -- Their containers, carrying the host columns the groups aggregate and pick on.
    CREATE TEMP TABLE wl_cont AS
    SELECT hc.id AS container_row_id, f.host_id, f.recency, f.svc_id, f.heartbeat_timestamp, f.host_active,
           hc.namespace, hc.pod_name, hc.container_name, hc.workload_name, hc.workload_kind
    FROM wl_fresh f
    JOIN HeartbeatContainers hc ON hc.host_id = f.host_id;
    ANALYZE wl_cont;

    -- One row per (service, namespace, pod) container group. host_container_count is
    -- its number of distinct (host, container name) pairs; min_host = max_host marks a
    -- group on one host.
    CREATE TEMP TABLE wl_grp AS
    SELECT svc_id, namespace, pod_name, COUNT(container_name) AS host_container_count,
           bool_or(any_active) AS any_active, MAX(l_heartbeat_timestamp) AS l_heartbeat_timestamp,
           MIN(latest) AS latest, MIN(host_id) AS min_host, MAX(host_id) AS max_host
    FROM (SELECT svc_id, namespace, pod_name, host_id, container_name, bool_or(host_active) AS any_active,
                 MAX(heartbeat_timestamp) AS l_heartbeat_timestamp, MIN(ARRAY[recency, container_row_id]) AS latest
          FROM wl_cont
          GROUP BY svc_id, namespace, pod_name, host_id, container_name) d
    GROUP BY svc_id, namespace, pod_name;
    ANALYZE wl_grp;

    -- Distinct container names per (service, namespace), and distinct hosts per namespace.
    CREATE TEMP TABLE wl_names AS
    SELECT DISTINCT svc_id, namespace, container_name FROM wl_cont WHERE container_name IS NOT NULL;

    CREATE TEMP TABLE wl_ns_hosts AS
    SELECT svc_id, namespace, COUNT(*) AS host_count
    FROM (SELECT DISTINCT svc_id, namespace, host_id FROM wl_cont WHERE namespace IS NOT NULL) d
    GROUP BY svc_id, namespace;

    -- Process counts, from one join of the processes:
    --   service / namespace: distinct (pid, process_name), deduplicated across hosts
    --     (concat_ws skips a NULL name, so 'pid' and 'pid:name' keep the pairs apart);
    --   pod: process rows and the hosts they come from;
    --   host: process rows and distinct pids (the process tab count).
    -- Grouping under COLLATE "C" is byte equality, the same as the database collation's
    -- (deterministic) equality, and avoids locale-aware comparisons.
    CREATE TEMP TABLE wl_proc AS
    WITH p AS MATERIALIZED (
        SELECT c.svc_id, c.namespace, c.pod_name, c.host_id, hp.pid, hp.process_name
        FROM wl_cont c
        JOIN HeartbeatProcesses hp ON hp.container_row_id = c.container_row_id
    )
    SELECT CASE GROUPING(namespace COLLATE "C") WHEN 0 THEN 'namespace' ELSE 'service' END AS kind,
           svc_id, (namespace COLLATE "C") COLLATE "default" AS namespace, NULL::text AS pod_name,
           NULL::bigint AS host_id, COUNT(DISTINCT concat_ws(':', pid, process_name) COLLATE "C") AS n,
           NULL::bigint AS d, NULL::bigint AS min_host, NULL::bigint AS max_host
    FROM p
    GROUP BY GROUPING SETS ((svc_id, namespace COLLATE "C"), (svc_id))
    UNION ALL
    SELECT 'pod', svc_id, namespace, pod_name, NULL, COUNT(*), NULL, MIN(host_id), MAX(host_id)
    FROM p
    WHERE pod_name IS NOT NULL
    GROUP BY svc_id, namespace, pod_name
    UNION ALL
    SELECT 'host', NULL, NULL, NULL, host_id, COUNT(*), COUNT(DISTINCT pid), NULL, NULL
    FROM p
    GROUP BY host_id;
    ANALYZE wl_proc;

    -- Pod counts. A pod on one host has host_count 1 and container_count equal to its
    -- (host, container name) pairs; its process rows are its distinct (pid, name) pairs
    -- when they come from one host that lists each pid once. Other pods are recounted
    -- from their own rows. pod_name is set here, so (service, pod_name) are the hash
    -- keys and the NULL-safe namespace match is a cheap filter.
    CREATE TEMP TABLE wl_pod AS
    SELECT g.svc_id, g.namespace, g.pod_name, g.any_active, g.l_heartbeat_timestamp, g.latest,
           g.min_host = g.max_host AND (pp.min_host IS NULL OR (pp.min_host = pp.max_host AND ph.n = ph.d)) AS regular,
           g.host_container_count, COALESCE(pp.n, 0) AS process_rows
    FROM wl_grp g
    LEFT JOIN wl_proc pp ON pp.kind = 'pod' AND pp.svc_id = g.svc_id AND pp.pod_name = g.pod_name
                        AND pp.namespace IS NOT DISTINCT FROM g.namespace
    LEFT JOIN wl_proc ph ON ph.kind = 'host' AND ph.host_id = pp.min_host
    WHERE g.pod_name IS NOT NULL;
    ANALYZE wl_pod;

    CREATE TEMP TABLE wl_pod_recount AS
    SELECT c.svc_id, c.namespace, c.pod_name, COUNT(DISTINCT c.host_id) AS host_count,
           COUNT(DISTINCT c.container_name) AS container_count,
           COUNT(DISTINCT CASE WHEN hp.pid IS NOT NULL THEN concat_ws(':', hp.pid, hp.process_name) END) AS process_count
    FROM wl_pod o
    JOIN wl_cont c ON c.svc_id = o.svc_id AND c.pod_name = o.pod_name AND c.namespace IS NOT DISTINCT FROM o.namespace
    LEFT JOIN HeartbeatProcesses hp ON hp.container_row_id = c.container_row_id
    WHERE NOT o.regular
    GROUP BY c.svc_id, c.namespace, c.pod_name;

    -- service: grouped at the host grain, so hosts without containers count too.
    EXECUTE format($f$
        INSERT INTO %1$I (%2$s)
        WITH
        grp AS (
            SELECT svc_id, COUNT(*) AS host_count, bool_or(host_active) AS any_active,
                   MAX(heartbeat_timestamp) AS l_heartbeat_timestamp, MIN(recency) AS latest
            FROM wl_fresh
            GROUP BY svc_id),
        nss AS (
            SELECT svc_id, COUNT(*) AS namespace_count FROM wl_ns_hosts GROUP BY svc_id),
        pods AS (
            SELECT svc_id, COUNT(DISTINCT pod_name COLLATE "C") AS pod_count
            FROM wl_grp
            WHERE pod_name IS NOT NULL
            GROUP BY svc_id),
        conts AS (
            SELECT svc_id, COUNT(DISTINCT container_name COLLATE "C") AS container_count
            FROM wl_names
            GROUP BY svc_id),
        latest_cont AS (
            SELECT DISTINCT ON (host_id) host_id, namespace, pod_name, container_name, workload_name, workload_kind
            FROM wl_cont
            WHERE recency IN (SELECT latest FROM grp)
            ORDER BY host_id, container_row_id)
        SELECT 'service', g.svc_id, s.service_name,
               s.service_name, NULL, NULL, NULL, NULL,
               g.host_count, COALESCE(ns.namespace_count, 0), COALESCE(pd.pod_count, 0), COALESCE(ct.container_count, 0),
               COALESCE(pc.n, 0), NULL::integer[],
               f.hostname, f.ip_address, lc.namespace, lc.pod_name, lc.container_name, lc.workload_name, lc.workload_kind,
               NULL::text, NULL::integer, f.command_type, f.command_status, f.combined_config, g.any_active, NULL::text,
               f.agent_version, f.run_mode, g.l_heartbeat_timestamp
        FROM grp g
        JOIN wl_svc s ON s.svc_id = g.svc_id
        JOIN wl_fresh f ON f.recency = g.latest
        LEFT JOIN latest_cont lc ON lc.host_id = f.host_id
        LEFT JOIN nss ns ON ns.svc_id = g.svc_id
        LEFT JOIN pods pd ON pd.svc_id = g.svc_id
        LEFT JOIN conts ct ON ct.svc_id = g.svc_id
        LEFT JOIN wl_proc pc ON pc.kind = 'service' AND pc.svc_id = g.svc_id
    $f$, sum_tbl, sum_cols);

    -- namespace
    EXECUTE format($f$
        INSERT INTO %1$I (%2$s)
        WITH
        grp AS (
            SELECT svc_id, namespace, bool_or(any_active) AS any_active,
                   MAX(l_heartbeat_timestamp) AS l_heartbeat_timestamp, MIN(latest) AS latest,
                   COUNT(pod_name) AS pod_count
            FROM wl_grp
            WHERE namespace IS NOT NULL
            GROUP BY svc_id, namespace),
        conts AS (
            SELECT svc_id, namespace, COUNT(*) AS container_count
            FROM wl_names
            WHERE namespace IS NOT NULL
            GROUP BY svc_id, namespace)
        SELECT 'namespace', row_number() OVER (ORDER BY g.svc_id, g.namespace),
               s.service_name || '|' || g.namespace,
               s.service_name, NULL, g.namespace, NULL, NULL,
               nh.host_count, 1, g.pod_count, COALESCE(ct.container_count, 0), COALESCE(pc.n, 0), NULL::integer[],
               f.hostname, f.ip_address, l.namespace, l.pod_name, l.container_name, l.workload_name, l.workload_kind,
               NULL::text, NULL::integer, f.command_type, f.command_status, f.combined_config, g.any_active, NULL::text,
               f.agent_version, f.run_mode, g.l_heartbeat_timestamp
        FROM grp g
        JOIN wl_svc s ON s.svc_id = g.svc_id
        JOIN wl_ns_hosts nh ON nh.svc_id = g.svc_id AND nh.namespace = g.namespace
        JOIN wl_cont l ON l.container_row_id = g.latest[2]
        JOIN wl_fresh f ON f.host_id = l.host_id
        LEFT JOIN conts ct ON ct.svc_id = g.svc_id AND ct.namespace = g.namespace
        LEFT JOIN wl_proc pc ON pc.kind = 'namespace' AND pc.svc_id = g.svc_id AND pc.namespace = g.namespace
    $f$, sum_tbl, sum_cols);

    -- pod: sort_seq is numbered on the narrow pod rows, before the wide host and
    -- container columns are joined on.
    EXECUTE format($f$
        INSERT INTO %1$I (%2$s)
        SELECT 'pod', k.sort_seq, s.service_name || '|' || COALESCE(k.namespace, '') || '|' || k.pod_name,
               s.service_name, NULL, k.namespace, k.pod_name, NULL,
               k.host_count, (k.namespace IS NOT NULL)::integer, 1, k.container_count, k.process_count, NULL::integer[],
               f.hostname, f.ip_address, l.namespace, l.pod_name, l.container_name, l.workload_name, l.workload_kind,
               NULL::text, NULL::integer, f.command_type, f.command_status, f.combined_config, k.any_active, NULL::text,
               f.agent_version, f.run_mode, k.l_heartbeat_timestamp
        FROM (SELECT o.svc_id, o.namespace, o.pod_name, o.any_active, o.l_heartbeat_timestamp, o.latest,
                     COALESCE(r.host_count, 1) AS host_count,
                     COALESCE(r.container_count, o.host_container_count) AS container_count,
                     COALESCE(r.process_count, o.process_rows) AS process_count,
                     row_number() OVER (ORDER BY o.svc_id, o.namespace, o.pod_name) AS sort_seq
              FROM wl_pod o
              LEFT JOIN wl_pod_recount r ON r.svc_id = o.svc_id AND r.pod_name = o.pod_name
                                        AND r.namespace IS NOT DISTINCT FROM o.namespace) k
        JOIN wl_svc s ON s.svc_id = k.svc_id
        JOIN wl_cont l ON l.container_row_id = k.latest[2]
        JOIN wl_fresh f ON f.host_id = l.host_id
    $f$, sum_tbl, sum_cols);

    -- Tab counts. Within a service a hostname is one host row, so host_id stands in
    -- for hostname in the container count.
    EXECUTE format($f$
        INSERT INTO %1$I (scope, count)
        SELECT 'service', COUNT(*) FROM %2$I WHERE scope = 'service'
        UNION ALL
        SELECT 'namespace', COUNT(*) FROM %2$I WHERE scope = 'namespace'
        UNION ALL
        SELECT 'pod', COUNT(*) FROM %2$I WHERE scope = 'pod'
        UNION ALL
        SELECT 'host', COUNT(*) FROM wl_fresh
        UNION ALL
        SELECT 'active_hosts', COUNT(*) FROM (SELECT DISTINCT hostname FROM wl_fresh) d
        UNION ALL
        SELECT 'container', COALESCE(SUM(host_container_count), 0) FROM wl_grp
        UNION ALL
        SELECT 'process', COALESCE(SUM(d), 0) FROM wl_proc WHERE kind = 'host'
    $f$, cnt_tbl, sum_tbl);

    DROP TABLE wl_fresh, wl_svc, wl_cont, wl_grp, wl_names, wl_ns_hosts, wl_proc, wl_pod, wl_pod_recount;
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
