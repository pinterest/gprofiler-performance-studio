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

-- TYPES

CREATE TYPE providername AS ENUM (
    'AWS',
    'GCP',
    'Azure',
    'Unknown');


CREATE TABLE InstanceCloudMetadata (
    ID bigserial PRIMARY KEY,
    meta jsonb NOT NULL,
    hash_meta text UNIQUE NOT NULL,
    ts timestamp DEFAULT CURRENT_TIMESTAMP
);



CREATE TABLE Libcs (
    ID bigserial PRIMARY KEY,
    type text NOT NULL,
    version text NOT NULL,
    ts timestamp DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "unique libc" UNIQUE (type, version)
);


CREATE TABLE MachineTypes (
    ID bigserial PRIMARY KEY,
    provider ProviderName NOT NULL,
    name text NOT NULL,
    ts timestamp DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "unique machine type" UNIQUE (provider, name)
);


CREATE TABLE OSes (
    ID bigserial PRIMARY KEY,
    system_name text NOT NULL,
    "name" text NOT NULL,
    "release" text NOT NULL,
    ts timestamp NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "unique OS" UNIQUE (system_name, name, release)
);


CREATE TABLE ProfilerVersions (
    ID bigserial PRIMARY KEY,
    major bigint NOT NULL CONSTRAINT "non-negative major number" CHECK(major >= 0),
    minor bigint NOT NULL CONSTRAINT "non-negative minor number" CHECK(minor >= 0),
    patch bigint NOT NULL CONSTRAINT "non-negative patch number" CHECK(patch >= 0),
    weight bigint NOT NULL,
    name text,
    ts timestamp DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "unique profiler_version" UNIQUE (major, minor, patch)
);


CREATE TYPE ServiceType AS ENUM ('instances', 'pods', 'containers');
CREATE TYPE EnvType AS ENUM ('instances', 'k8s', 'containers', 'ecs');


CREATE TABLE Instances (
    ID bigserial PRIMARY KEY,
    mac macaddr NOT NULL,
    identifier text,
    ts timestamp DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX "unique instance" ON instances USING btree (mac, identifier);
CREATE UNIQUE INDEX "unique instance (no identifier)" ON public.instances USING btree (mac, ((identifier IS NULL))) WHERE (identifier IS NULL);


CREATE TABLE Kernels (
    ID bigserial PRIMARY KEY,
    os bigint NOT NULL CONSTRAINT "kernel must belong to a valid OS" REFERENCES OSes,
    release text NOT NULL,
    version text NOT NULL,
    hardware_type text NOT NULL,
    ts timestamp DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "unique kernel" UNIQUE (os, release, version, hardware_type)
);


CREATE TABLE Machines (
    ID bigserial PRIMARY KEY,
    type bigint NOT NULL CONSTRAINT "type must belong to a valid MachineType" REFERENCES MachineTypes,
    processors bigint NOT NULL CONSTRAINT "positive number of processors" CHECK(processors > 0),
    memory bigint NOT NULL CONSTRAINT "positive memory" CHECK(memory > 0),
    ts timestamp DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "unique machine" UNIQUE (type, processors, memory)
);


CREATE TYPE RunMode AS ENUM ('k8s', 'container', 'standalone_executable', 'local_python');

CREATE TABLE ProfilerRunEnvironments (
    ID bigserial PRIMARY KEY,
    python_version text NOT NULL,
    libc bigint NOT NULL CONSTRAINT "profiler_run_environment must belong to a valid libc" REFERENCES Libcs,
    run_mode RunMode NOT NULL,
    ts timestamp DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "unique profiler_run_environment" UNIQUE (python_version, libc, run_mode)
);


CREATE TABLE ProfilerTokens (
    ID bigserial PRIMARY KEY,
    "token" text NOT NULL,
    disabled timestamp NULL,
    ts timestamp NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT profilertokens_token_key UNIQUE (token)
);


CREATE TABLE Services (
    ID bigserial PRIMARY KEY,
    "name" text NOT NULL,
    hidden bool NOT NULL DEFAULT false,
    ts timestamp NULL DEFAULT CURRENT_TIMESTAMP,
    service_type servicetype NOT NULL DEFAULT 'instances'::servicetype,
    cluster_id bigint CONSTRAINT "service must belong to a valid cluster-service" REFERENCES Services,
    is_cluster bool NOT NULL DEFAULT false,
    env_type envtype NULL,
    profiler_sample_threshold float8 NULL,
    CONSTRAINT "unique service" UNIQUE (name)
);

CREATE INDEX services_hidden_idx ON services USING btree (hidden);


CREATE TABLE TokenAssociations (
    ID bigserial PRIMARY KEY,
    token bigint NOT NULL CONSTRAINT "token_association must belong to a valid profiler" REFERENCES ProfilerTokens,
    service_name text NOT NULL,
    service bigint UNIQUE NOT NULL CONSTRAINT "token_association must belong to a valid service" REFERENCES Services,
    last_seen timestamp NULL DEFAULT CURRENT_TIMESTAMP,
    ts timestamp NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT tokenassociations_service_key UNIQUE (service),
    CONSTRAINT "unique token_association" UNIQUE (token, service_name)
);


CREATE OR REPLACE RULE ServicesDeleteProtection
    AS ON DELETE TO Services DO INSTEAD
    UPDATE Services SET hidden = true WHERE Services.ID = OLD.ID;


CREATE TABLE InstanceRuns (
    ID bigserial PRIMARY KEY,
    instance bigint NOT NULL CONSTRAINT "instance_run must belong to a valid instance" REFERENCES Instances,
    boot_time timestamp NOT NULL,
    machine bigint NOT NULL CONSTRAINT "instance_run must have a valid machine" REFERENCES Machines,
    kernel bigint NOT NULL CONSTRAINT "instance_run must have a valid kernel" REFERENCES Kernels,
    ts timestamp NULL DEFAULT CURRENT_TIMESTAMP,
    metadata bigint CONSTRAINT "instance_run must have a valid metadata" REFERENCES InstanceCloudMetadata,
    CONSTRAINT "unique instance_run" UNIQUE (instance, boot_time)
);

CREATE INDEX instanceruns_instance_idx ON instanceruns USING btree (instance);
CREATE INDEX instanceruns_kernel_idx ON instanceruns USING btree (kernel);
CREATE INDEX instanceruns_machine_idx ON instanceruns USING btree (machine);
CREATE INDEX instanceruns_metadata_idx ON instanceruns USING btree (metadata);


CREATE TABLE ProfilerFilters
(
    ID bigserial PRIMARY KEY,
    service bigint NOT NULL CONSTRAINT "ProfilerFilter must belong to a valid service" REFERENCES Services,
    filter_content jsonb NOT NULL,
    ts timestamp DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE ProfilerProcesses (
    ID bigserial PRIMARY KEY,
    instance_run bigint NOT NULL CONSTRAINT "profiler_process must belong to a valid instance_run" REFERENCES InstanceRuns,
    profiler_version bigint NOT NULL CONSTRAINT "profiler_process must have a valid profiler_version" REFERENCES ProfilerVersions,
    profiler_run_environment bigint NOT NULL CONSTRAINT "profiler_process must have a valid profiler_run_environment" REFERENCES ProfilerRunEnvironments,
    pid bigint NOT NULL CONSTRAINT "non-negative pid" CHECK(pid >= 0),
    spawn_local_time timestamp NOT NULL,
    public_ip text NOT NULL,
    private_ip text NOT NULL,
    hostname text NOT NULL,
    service bigint NOT NULL CONSTRAINT "profiler_process must belong to a valid service" REFERENCES Services,
    last_seen timestamp DEFAULT CURRENT_TIMESTAMP CONSTRAINT "last_seen must be after ts" CHECK(last_seen >= ts),
    spawn_uptime int8 NOT NULL,
    run_arguments jsonb NOT NULL,
    ts timestamp NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "unique profiler_process" UNIQUE (instance_run, profiler_version, pid, spawn_local_time)
);
CREATE INDEX profilerprocesses_last_seen_idx ON profilerprocesses USING btree (last_seen);
CREATE INDEX profilerprocesses_profiler_run_environment_idx ON profilerprocesses USING btree (profiler_run_environment);
CREATE INDEX profilerprocesses_profiler_version_idx ON profilerprocesses USING btree (profiler_version);
CREATE INDEX profilerprocesses_service_idx ON profilerprocesses USING btree (service);
CREATE INDEX profilerprocesses_service_last_seen_idx ON profilerprocesses USING btree (service, last_seen DESC);



CREATE TABLE ProfilerServiceHourlyUsages (
    ID bigserial PRIMARY KEY,
    service bigint NOT NULL CONSTRAINT "profiler_service_hourly_usage must belong to a valid service" REFERENCES Services,
    start_date timestamp NOT NULL CONSTRAINT "start_date must be a whole hour" CHECK(date_trunc('hour', start_date) = start_date),
    running_hours float8 NOT NULL,
    core_hours float8 NOT NULL,
    lowest_agent_version bigint NOT NULL CONSTRAINT "prof_service_hourly_usage must have a valid profiler_version" REFERENCES ProfilerVersions,
    ts timestamp NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "unique profiler_service_hourly_usage" UNIQUE (service, start_date)
);

CREATE TABLE ProfilerSnapshots (
    ID bigserial PRIMARY KEY,
    service bigint NOT NULL CONSTRAINT "profiler_snapshot must belong to a valid service" REFERENCES Services,
    start_time timestamp NULL,
    end_time timestamp NULL CONSTRAINT "end_date must be after start_date" CHECK(end_time > start_time),
    hidden bool NULL DEFAULT false,
    ts timestamp NULL DEFAULT CURRENT_TIMESTAMP,
    filter_content jsonb NULL
);

-- AdhocFlamegraphMetadata table for storing PMU events and other adhoc profiling metadata
CREATE TABLE AdhocFlamegraphMetadata (
    ID bigserial PRIMARY KEY,
    service_id bigint NOT NULL,
    hostname text NOT NULL,
    s3_key text NOT NULL UNIQUE,
    perf_events text[],
    start_time timestamp NOT NULL,
    end_time timestamp NOT NULL,
    file_size bigint,
    created_at timestamp DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_adhoc_flamegraph_service 
        FOREIGN KEY (service_id) 
        REFERENCES Services(ID) 
        ON DELETE CASCADE
);

CREATE INDEX idx_adhoc_metadata_service_time ON AdhocFlamegraphMetadata(service_id, start_time DESC);
CREATE INDEX idx_adhoc_metadata_s3_key ON AdhocFlamegraphMetadata(s3_key);
CREATE INDEX idx_adhoc_metadata_hostname ON AdhocFlamegraphMetadata(hostname);

CREATE TABLE MinesweeperFrames (
    ID bigserial PRIMARY KEY,
    snapshot bigint NOT NULL CONSTRAINT "minesweeper_frame must belong to a valid snapshot" REFERENCES ProfilerSnapshots,
    "level" int8 NULL,
    "start" int8 NULL,
    duration int8 NULL,
    ts timestamp NULL DEFAULT CURRENT_TIMESTAMP
);


-- Additional Types for Profiling System
CREATE TYPE ProfilingMode AS ENUM ('cpu', 'allocation', 'none');
CREATE TYPE ProfilingRequestStatus AS ENUM ('pending', 'assigned', 'completed', 'failed', 'cancelled');
CREATE TYPE CommandStatus AS ENUM ('pending', 'sent', 'completed', 'failed');
CREATE TYPE HostStatus AS ENUM ('active', 'idle', 'error', 'offline');

-- Host Heartbeat Table (simplified)
CREATE TABLE HostHeartbeats (
    ID bigserial PRIMARY KEY,
    hostname text NOT NULL,
    ip_address inet NOT NULL,
    service_name text NOT NULL,
    agent_version text NULL,
    run_mode text NULL,
    namespace text NULL,
    pod_name text NULL,
    last_command_id uuid NULL,
    received_command_ids uuid[] NULL,
    executed_command_ids uuid[] NULL,
    status HostStatus NOT NULL DEFAULT 'active',
    heartbeat_timestamp timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    supported_perf_events text[] NULL,
    created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- sha256 of the inventory last synced into HeartbeatContainers/HeartbeatProcesses for
    -- this host (see migrations/add_inventory_digest_to_hostheartbeats.sql). A beat whose
    -- inventory hashes to this value does no inventory work. NULL = not yet synced under
    -- change detection. (Last column so a fresh install matches the migrated layout.)
    inventory_digest text NULL,
    CONSTRAINT "unique_host_heartbeat" UNIQUE (hostname, service_name)
);

-- Essential indexes for heartbeats.
-- Deliberately NOT indexed (see migrations/drop_redundant_heartbeat_indexes.sql):
--   * hostname            -- leading column of unique_host_heartbeat already serves it
--   * heartbeat_timestamp -- rewritten on every beat; an index on it forces a full
--                            row re-write + all-index maintenance per beat (no HOT
--                            update). Every reader filters by recency over the whole
--                            table (<=150k rows), which is a cheap sequential scan.
CREATE INDEX idx_hostheartbeats_service_name ON HostHeartbeats (service_name);
CREATE INDEX idx_hostheartbeats_status ON HostHeartbeats (status);
CREATE INDEX idx_hostheartbeats_namespace ON HostHeartbeats (namespace);
CREATE INDEX idx_hostheartbeats_pod_name ON HostHeartbeats (pod_name);

-- Structured workload inventory (normalized form of the container/process data
-- reported in each heartbeat). Every process is scoped to a container, so this
-- currently models containerized workloads only; non-containerized (e.g.
-- systemd/bare-metal) processes are not represented here and are covered by
-- host-scope profiling instead.
CREATE TABLE HeartbeatContainers (
    id bigserial PRIMARY KEY,
    host_id bigint NOT NULL REFERENCES HostHeartbeats (ID) ON DELETE CASCADE,
    container_id text NULL,
    container_name text NULL,
    runtime text NULL,
    namespace text NULL,
    pod_name text NULL,
    workload_name text NULL,
    workload_kind text NULL,
    updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT unique_heartbeat_container UNIQUE (host_id, container_id)
);

-- host_id lookups are served by unique_heartbeat_container (host_id, container_id).
CREATE INDEX idx_hb_containers_namespace ON HeartbeatContainers (namespace);
CREATE INDEX idx_hb_containers_pod_name ON HeartbeatContainers (pod_name);
CREATE INDEX idx_hb_containers_workload_name ON HeartbeatContainers (workload_name);

CREATE TABLE HeartbeatProcesses (
    id bigserial PRIMARY KEY,
    container_row_id bigint NOT NULL REFERENCES HeartbeatContainers (id) ON DELETE CASCADE,
    pid integer NOT NULL,
    process_name text NULL,
    updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT unique_heartbeat_process UNIQUE (container_row_id, pid)
);

-- No secondary indexes on HeartbeatProcesses: container_row_id lookups are served by
-- unique_heartbeat_process (container_row_id, pid), and nothing filters by
-- process_name without first narrowing to a container. This table is the largest
-- (one row per process fleet-wide) so every extra index is paid on every insert.

-- Cache a block of ids per backend so high-frequency heartbeat inserts don't
-- contend on the sequence buffer lock (see migrations/increase_heartbeat_sequence_cache.sql).
ALTER SEQUENCE hostheartbeats_id_seq      CACHE 500;
ALTER SEQUENCE heartbeatcontainers_id_seq CACHE 500;
ALTER SEQUENCE heartbeatprocesses_id_seq  CACHE 500;

-- Profiling Requests Table (simplified)
CREATE TABLE ProfilingRequests (
    ID bigserial PRIMARY KEY,
    request_id uuid NOT NULL UNIQUE,
    service_name text NOT NULL,
    request_type text NOT NULL CHECK (request_type IN ('start', 'stop')),
    continuous boolean NOT NULL DEFAULT false,
    duration integer NULL DEFAULT 60,
    frequency integer NULL DEFAULT 11,
    profiling_mode ProfilingMode NOT NULL DEFAULT 'cpu',
    target_hostnames text[] NOT NULL,
    pids integer[] NULL,
    stop_level text NULL DEFAULT 'process' CHECK (stop_level IN ('process', 'host')),
    additional_args jsonb NULL,
    status ProfilingRequestStatus NOT NULL DEFAULT 'pending',
    assigned_to_hostname text NULL,
    assigned_at timestamp NULL,
    completed_at timestamp NULL,
    estimated_completion_time timestamp NULL,
    service_id bigint NULL CONSTRAINT "fk_profiling_request_service" REFERENCES Services(ID),
    created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Essential indexes for profiling requests
CREATE INDEX idx_profilingrequests_request_id ON ProfilingRequests (request_id);
CREATE INDEX idx_profilingrequests_service_name ON ProfilingRequests (service_name);
CREATE INDEX idx_profilingrequests_status ON ProfilingRequests (status);
CREATE INDEX idx_profilingrequests_request_type ON ProfilingRequests (request_type);
CREATE INDEX idx_profilingrequests_created_at ON ProfilingRequests (created_at);

-- Profiling Commands Table (simplified)
CREATE TABLE ProfilingCommands (
    ID bigserial PRIMARY KEY,
    command_id uuid NOT NULL,
    hostname text NOT NULL,
    service_name text NOT NULL,
    command_type text NOT NULL CHECK (command_type IN ('start', 'stop')),
    request_ids uuid[] NOT NULL,
    combined_config jsonb NULL,
    status CommandStatus NOT NULL DEFAULT 'pending',
    created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_at timestamp NULL,
    completed_at timestamp NULL,
    execution_time integer NULL,
    error_message text NULL,
    results_path text NULL,
    updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "unique_profiling_command_per_host" UNIQUE (hostname, service_name)
);

-- Essential indexes for profiling commands.
-- (hostname) and (hostname, service_name) lookups are served by
-- unique_profiling_command_per_host.
CREATE INDEX idx_profilingcommands_command_id ON ProfilingCommands (command_id);
CREATE INDEX idx_profilingcommands_service_name ON ProfilingCommands (service_name);
CREATE INDEX idx_profilingcommands_status ON ProfilingCommands (status);

-- Profiling Executions Table (optional - for audit trail)
CREATE TABLE ProfilingExecutions (
    ID bigserial PRIMARY KEY,
    command_id uuid NOT NULL,
    hostname text NOT NULL,
    profiling_request_id uuid NOT NULL CONSTRAINT "fk_profiling_execution_request" REFERENCES ProfilingRequests(request_id),
    status ProfilingRequestStatus NOT NULL DEFAULT 'pending',
    started_at timestamp NULL,
    completed_at timestamp NULL,
    execution_time integer NULL,
    error_message text NULL,
    results_path text NULL,
    created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    -- Adding the constraint that db_manager.py expects for ON CONFLICT
    CONSTRAINT "unique_profiling_execution" UNIQUE (command_id, hostname)
);

-- Essential indexes for profiling executions
CREATE INDEX idx_profilingexecutions_command_id ON ProfilingExecutions (command_id);
CREATE INDEX idx_profilingexecutions_hostname ON ProfilingExecutions (hostname);
CREATE INDEX idx_profilingexecutions_profiling_request_id ON ProfilingExecutions (profiling_request_id);
CREATE INDEX idx_profilingexecutions_status ON ProfilingExecutions (status);

-- FUNCTIONS

CREATE OR REPLACE FUNCTION calc_profiler_usage_history(start_date timestamp without time zone, end_date timestamp without time zone, interval_s bigint, max_iterations bigint DEFAULT 3)
 RETURNS TABLE(start_time timestamp without time zone, end_time timestamp without time zone, service bigint, running_hours double precision, core_hours double precision, lowest_agent_version bigint)
 LANGUAGE plpgsql
AS $function$
    BEGIN
        RETURN QUERY
            WITH TimeSeries AS (
                SELECT
                   (start_date + interval '1 seconds' * interval_s * generate_series) AS start_time,
                   (start_date + interval '1 seconds' * interval_s * (generate_series + 1)) AS end_time
                FROM GENERATE_SERIES(0, LEAST(max_iterations, CAST (EXTRACT (EPOCH FROM end_date - start_date) AS bigint) / interval_s) - 1)
            ), RelevantProfilerProcesses AS (
                SELECT ProfilerProcesses.service, ProfilerProcesses.instance_run, ProfilerProcesses.last_seen, ProfilerProcesses.profiler_version, ProfilerProcesses.ts as first_seen
                FROM ProfilerProcesses
                INNER JOIN Services ON Services.ID = ProfilerProcesses.service
                WHERE Services.cluster_id IS NULL
                    AND ProfilerProcesses.last_seen >= start_date
                    AND ProfilerProcesses.ts < end_date
            ), TimeSeriesProcesess AS (
                SELECT
                    TimeSeries.start_time,
                    TimeSeries.end_time,
                    RelevantProfilerProcesses.service,
                    Machines.processors,
                    EXTRACT(EPOCH FROM (LEAST(RelevantProfilerProcesses.last_seen, TimeSeries.end_time) -
                                        GREATEST(RelevantProfilerProcesses.first_seen, TimeSeries.start_time))) AS duration,
                    Machines.type AS machine_type,
                    InstanceRuns.metadata,
                    ProfilerVersions.ID as version_id,
                    ProfilerVersions.weight
                FROM RelevantProfilerProcesses
                INNER JOIN TimeSeries ON RelevantProfilerProcesses.last_seen >= TimeSeries.start_time
                    AND RelevantProfilerProcesses.first_seen < TimeSeries.end_time
                INNER JOIN InstanceRuns ON InstanceRuns.ID = RelevantProfilerProcesses.instance_run
                INNER JOIN Machines ON Machines.ID = InstanceRuns.machine
                INNER JOIN ProfilerVersions ON ProfilerVersions.ID = RelevantProfilerProcesses.profiler_version
            )
            SELECT
                TimeSeriesProcesess.start_time,
                TimeSeriesProcesess.end_time,
                TimeSeriesProcesess.service,
                CAST(SUM(TimeSeriesProcesess.duration) / 3600 AS DOUBLE PRECISION) AS running_hours,
                CAST(SUM(TimeSeriesProcesess.processors * TimeSeriesProcesess.duration) / 3600 AS DOUBLE PRECISION) AS core_hours,
                FIRST(TimeSeriesProcesess.version_id ORDER BY TimeSeriesProcesess.weight) AS agent_version_lowest
            FROM TimeSeriesProcesess
            GROUP BY TimeSeriesProcesess.start_time, TimeSeriesProcesess.end_time, TimeSeriesProcesess.service
            ORDER BY TimeSeriesProcesess.start_time, TimeSeriesProcesess.service;
    END; $function$
;

CREATE OR REPLACE FUNCTION get_deployment(my_cluster_id bigint, service_name text, stype servicetype DEFAULT 'instances'::servicetype, create_hidden boolean DEFAULT false, namespace text DEFAULT NULL::text)
 RETURNS bigint
 LANGUAGE plpgsql
AS $function$
    DECLARE
        service_id bigint;
        service_is_cluster boolean;
        service_full_name text;
    BEGIN
        SELECT CASE WHEN namespace IS NOT NULL THEN CONCAT(service_name, '_', namespace) ELSE service_name END
        INTO service_full_name;

        IF namespace IS NULL THEN
            SELECT Services.ID
            INTO service_id
            FROM Services
            WHERE Services.cluster_id = my_cluster_id
            AND Services.name = service_name
            AND NOT hidden;

            IF service_id IS NOT NULL THEN
                RETURN service_id;
            END IF;
        END IF;

        IF namespace IS NOT NULL THEN
            SELECT Services.ID
            INTO service_id
            FROM Services
            WHERE Services.cluster_id = my_cluster_id
            AND NOT hidden
            AND Services.name = service_name;

            IF service_id IS NOT NULL THEN
                UPDATE Services
                SET hidden = true
                WHERE Services.ID = service_id;
            END IF;

            SELECT Services.ID
            INTO service_id
            FROM Services
            WHERE Services.cluster_id = my_cluster_id
            AND Services.name = service_full_name;

            IF service_id IS NOT NULL THEN
                RETURN service_id;
            END IF;
        END IF;

        INSERT INTO Services(name, service_type, hidden, cluster_id)
        VALUES (service_full_name, stype, create_hidden, my_cluster_id)
        ON CONFLICT DO NOTHING
        RETURNING ID INTO service_id;

        IF service_id IS NOT NULL THEN
            UPDATE Services
            SET is_cluster = TRUE
            WHERE ID = my_cluster_id;

            RETURN -service_id;
        END IF;

        SELECT Services.ID
        INTO service_id
        FROM Services
        WHERE Services.cluster_id = my_cluster_id
        AND Services.name = service_full_name;

        RETURN service_id;
    END; $function$
;

CREATE OR REPLACE FUNCTION get_instance(mac_ macaddr, identifier_ text)
 RETURNS bigint
 LANGUAGE plpgsql
AS $function$
    DECLARE
        instance_id bigint;
    BEGIN
        IF identifier_ IS NULL THEN
            SELECT Instances.ID
            INTO instance_id
            FROM Instances
            WHERE Instances.mac = mac_ AND Instances.identifier IS NULL;
        ELSE
            SELECT Instances.ID
            INTO instance_id
            FROM Instances
            WHERE Instances.mac = mac_ AND Instances.identifier = identifier_;
        END IF;

        IF instance_id IS NOT NULL THEN
            RETURN instance_id;
        END IF;

        INSERT INTO Instances(mac, identifier)
        VALUES (mac_, identifier_)
        ON CONFLICT DO NOTHING
        RETURNING -ID INTO instance_id;

        IF instance_id IS NULL THEN
            IF identifier_ IS NULL THEN
                SELECT Instances.ID
                INTO instance_id
                FROM Instances
                WHERE Instances.mac = mac_ AND Instances.identifier IS NULL;
            ELSE
                SELECT Instances.ID
                INTO instance_id
                FROM Instances
                WHERE Instances.mac = mac_ AND Instances.identifier = identifier_;
            END IF;
        END IF;

        RETURN instance_id;
    END; $function$
;

CREATE OR REPLACE FUNCTION get_instance_run(instance_id bigint, boot_time_ timestamp without time zone, machine_id bigint, kernel_id bigint, metadata_id bigint)
 RETURNS TABLE(instance_run_id bigint, is_new_instance_run boolean)
 LANGUAGE plpgsql
AS $function$
    DECLARE
        instance_run_id bigint;
        machine_query bigint;
        kernel_query bigint;
        metadata_query bigint;
        is_new_instance_run boolean;
        machine_type_current bigint;
        machine_type_other bigint;
        machine_processors_current bigint;
        machine_processors_other bigint;
        machine_memory_current bigint;
        machine_memory_other bigint;
        os_current bigint;
        os_other bigint;
        release_current text;
        release_other text;
        version_current text;
        version_other text;
        hardware_type_current text;
        hardware_type_other text;
    BEGIN
        SELECT InstanceRuns.ID, InstanceRuns.machine, InstanceRuns.kernel, InstanceRuns.metadata
            INTO instance_run_id, machine_query, kernel_query, metadata_query
            FROM InstanceRuns
            WHERE InstanceRuns.instance = instance_id AND
                  InstanceRuns.boot_time = boot_time_;
        is_new_instance_run = FALSE;
        IF instance_run_id IS NOT NULL THEN
            IF machine_query != machine_id AND kernel_query = kernel_id THEN
                SELECT Machines.type, Machines.processors, Machines.memory
                    INTO machine_type_current, machine_processors_current, machine_memory_current
                    FROM Machines WHERE ID = machine_id;
                SELECT Machines.type, Machines.processors, Machines.memory
                    INTO machine_type_other, machine_processors_other, machine_memory_other
                    FROM Machines WHERE ID = machine_query;
                IF machine_type_current != 1 AND machine_type_other = 1 AND metadata_query IS NULL THEN
                    UPDATE InstanceRuns
                    SET machine = machine_id, metadata = metadata_id
                    WHERE ID = instance_run_id;
                    RETURN QUERY SELECT * FROM (VALUES (instance_run_id, is_new_instance_run)) AS t (instance_run_id, is_new_instance_run);
                    RETURN;
                ELSIF machine_type_current = 1 AND machine_type_other != 1 AND metadata_id IS NULL THEN
                    -- silently ignore conflict since DB data is correct and connected agent's metadata failed to fetch
                    RETURN QUERY SELECT * FROM (VALUES (instance_run_id, is_new_instance_run)) AS t (instance_run_id, is_new_instance_run);
                    RETURN;
                ELSIF machine_type_current = machine_type_other AND machine_processors_current = machine_processors_other AND ABS(machine_memory_current - machine_memory_other) < 2 THEN
                    -- silently ignore conflict since change in memory is minor
                    RETURN QUERY SELECT * FROM (VALUES (instance_run_id, is_new_instance_run)) AS t (instance_run_id, is_new_instance_run);
                    RETURN;
                END IF;
            END IF;
            IF kernel_query != kernel_id AND machine_query = machine_id THEN
                SELECT Kernels.os, Kernels.release, Kernels.version, Kernels.hardware_type
                INTO os_current, release_current, version_current, hardware_type_current
                FROM Kernels WHERE ID = kernel_id;
                SELECT Kernels.os, Kernels.release, Kernels.version, Kernels.hardware_type
                INTO os_other, release_other, version_other, hardware_type_other
                FROM Kernels WHERE ID = kernel_query;

                IF os_current != os_other AND release_current = release_other AND
                   version_current = version_other AND hardware_type_current = hardware_type_other THEN
                    UPDATE InstanceRuns
                    SET kernel = kernel_id
                    WHERE ID = instance_run_id;
                    RETURN QUERY SELECT * FROM (VALUES (instance_run_id, is_new_instance_run)) AS t (instance_run_id, is_new_instance_run);
                    RETURN;
                END IF;
            END IF;
            IF machine_query != machine_id OR kernel_query != kernel_id THEN
                RETURN QUERY SELECT * FROM (VALUES (-instance_run_id, is_new_instance_run)) AS t (instance_run_id, is_new_instance_run);  -- indicates that there is a conflict (that we allow)
                RETURN;
            END IF;
            RETURN QUERY SELECT * FROM (VALUES (instance_run_id, is_new_instance_run)) AS t (instance_run_id, is_new_instance_run);
            RETURN;
        END IF;
        INSERT INTO InstanceRuns(instance, boot_time, machine, kernel, metadata)
            VALUES (instance_id, boot_time_, machine_id, kernel_id, metadata_id)
            ON CONFLICT DO NOTHING
            RETURNING ID INTO instance_run_id;
        IF instance_run_id IS NOT NULL THEN
            is_new_instance_run = TRUE;
        ELSE
            -- race condition from first SELECT to INSERT; lets fetch the row that was added async
            SELECT InstanceRuns.ID
                INTO instance_run_id
                FROM InstanceRuns
                WHERE InstanceRuns.instance = instance_id AND
                      InstanceRuns.boot_time = boot_time_;
        END IF;
        RETURN QUERY SELECT * FROM (VALUES (instance_run_id, is_new_instance_run)) AS t (instance_run_id, is_new_instance_run);
    END; $function$
;

CREATE OR REPLACE FUNCTION get_profiler_process(instance_run_id bigint, profiler_version_id bigint, pid_ bigint, spawn_local_time_ timestamp without time zone, spawn_uptime_ bigint, public_ip text, private_ip text, hostname text, service_id bigint, profiler_run_environment_id bigint, run_arguments jsonb)
 RETURNS TABLE(profiler_process_id bigint, is_new_profiler_process boolean)
 LANGUAGE plpgsql
AS $function$
    DECLARE
        profiler_process_id bigint;
        spawn_uptime_query bigint;
        is_new_profiler_process boolean;
    BEGIN
        SELECT ProfilerProcesses.ID, ProfilerProcesses.spawn_uptime
            INTO profiler_process_id, spawn_uptime_query
            FROM ProfilerProcesses
            WHERE ProfilerProcesses.instance_run = instance_run_id AND
                  ProfilerProcesses.profiler_version = profiler_version_id AND
                  ProfilerProcesses.pid = pid_ AND
                  ProfilerProcesses.spawn_local_time = spawn_local_time_;

        IF profiler_process_id IS NOT NULL THEN
            is_new_profiler_process = FALSE; --Existing Profiler agent installation
            IF spawn_uptime_query != spawn_uptime_ THEN
                RETURN QUERY SELECT * FROM (VALUES (-profiler_process_id, is_new_profiler_process)) AS t (profiler_process_id, is_new_profiler_process);  -- indicates that there is a conflict (that we allow)
            ELSE
                RETURN QUERY SELECT * FROM (VALUES (profiler_process_id, is_new_profiler_process)) AS t (profiler_process_id, is_new_profiler_process);
            END IF;
            RETURN;
        END IF;

        INSERT INTO ProfilerProcesses(instance_run, profiler_version, pid, spawn_local_time, spawn_uptime, public_ip, private_ip, hostname, service, profiler_run_environment, run_arguments)
            VALUES (instance_run_id, profiler_version_id, pid_, spawn_local_time_, spawn_uptime_, public_ip, private_ip, hostname, service_id, profiler_run_environment_id, run_arguments)
            ON CONFLICT DO NOTHING
            RETURNING ID INTO profiler_process_id;
        is_new_profiler_process = TRUE; --New Profiler_process
        RETURN QUERY SELECT * FROM (VALUES (profiler_process_id, is_new_profiler_process)) AS t (profiler_process_id, is_new_profiler_process);
    END; $function$
;

CREATE OR REPLACE FUNCTION get_service(service_name text, stype servicetype DEFAULT 'instances'::servicetype, create_hidden boolean DEFAULT false, use_dot_logic boolean DEFAULT true, service_env_type envtype DEFAULT NULL::envtype)
 RETURNS bigint
 LANGUAGE plpgsql
AS $function$
    DECLARE
        service_id bigint;
        my_cluster_id bigint;
        service_is_cluster boolean;
        db_env_type EnvType;
    BEGIN
        SELECT Services.ID, Services.env_type
        INTO service_id, db_env_type
        FROM Services
        WHERE Services.name = service_name;

        IF service_id IS NOT NULL THEN
            IF db_env_type IS NULL AND service_env_type IS NOT NULL THEN
                UPDATE Services
                SET env_type = service_env_type
                WHERE ID = service_id;
            END IF;
            RETURN service_id;
        END IF;

        IF use_dot_logic IS TRUE AND position('.' in service_name) != 0 THEN
            SELECT Services.ID, Services.is_cluster
            INTO my_cluster_id, service_is_cluster
            FROM Services
            WHERE Services.name = SPLIT_PART(service_name, '.', 1);

            IF my_cluster_id IS NOT NULL AND NOT service_is_cluster THEN
                UPDATE Services
                SET is_cluster = TRUE
                WHERE ID = my_cluster_id;
            END IF;
        END IF;

        INSERT INTO Services(name, service_type, hidden, cluster_id, env_type)
        VALUES (service_name, stype, create_hidden, my_cluster_id, service_env_type)
        ON CONFLICT DO NOTHING
        RETURNING ID INTO service_id;

        IF service_id IS NOT NULL THEN
            RETURN -service_id;
        END IF;

        SELECT Services.ID, Services.env_type
        INTO service_id, db_env_type
        FROM Services
        WHERE Services.name = service_name;

        IF db_env_type IS NULL AND service_env_type IS NOT NULL THEN
            UPDATE Services
            SET env_type = service_env_type
            WHERE ID = service_id;
        END IF;

        RETURN service_id;
    END; $function$
;

CREATE OR REPLACE PROCEDURE update_profiler_service_hourly_usages(IN max_iterations bigint DEFAULT 3)
 LANGUAGE plpgsql
AS $procedure$
    DECLARE
        start_date timestamp;  -- will hold the last calculated value + 1h
        end_date timestamp;  -- will hold the last complete hour, which we can complete the calculation over
        connected_time INTERVAL := '10 minutes';
    BEGIN
        SELECT (ProfilerServiceHourlyUsages.start_date + interval '1 hours'), date_trunc('hour', CURRENT_TIMESTAMP - connected_time)
        INTO start_date, end_date
        FROM ProfilerServiceHourlyUsages
        ORDER BY ProfilerServiceHourlyUsages.start_date DESC
        LIMIT 1;
        IF start_date IS NULL THEN
            start_date = date_trunc('hour', CURRENT_TIMESTAMP - interval '1 hour' * 3);
        END IF;
        IF end_date IS NULL THEN
            end_date = date_trunc('hour', CURRENT_TIMESTAMP - connected_time);
        END IF;
        end_date = LEAST(end_date, start_date + interval '1 hour' * max_iterations);
        RAISE NOTICE 'start_date: %, end_date: %', start_date, end_date;
        INSERT INTO ProfilerServiceHourlyUsages(start_date, service, running_hours, core_hours, lowest_agent_version)
        SELECT start_time, service, running_hours, core_hours, lowest_agent_version
        FROM calc_profiler_usage_history(start_date, end_date, 60 * 60, max_iterations);
    END;
$procedure$
;



CREATE FUNCTION first_agg(anyelement, anyelement) RETURNS anyelement
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
    AS $_$
        SELECT $1;
$_$;


CREATE AGGREGATE first(anyelement) (
    SFUNC = first_agg,
    STYPE = anyelement
);



CREATE FUNCTION last_agg(anyelement, anyelement) RETURNS anyelement
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
    AS $_$
        SELECT $2;
$_$;



CREATE AGGREGATE last(anyelement) (
    SFUNC = last_agg,
    STYPE = anyelement
);


create function zz_concat(text, text) returns text as
    'select md5($1 || $2);' language 'sql';

create aggregate zz_hashagg(text) (
    sfunc = zz_concat,
    stype = text,
    initcond = '');



-- ============================================================================
-- Precomputed workload_status store (see migrations/add_workload_precompute_store.sql)
-- ============================================================================
-- ---------------------------------------------------------------- generation meta
CREATE TABLE IF NOT EXISTS workload_snapshot_meta (
    id smallint PRIMARY KEY DEFAULT 1,
    active_generation smallint NOT NULL DEFAULT 0,
    built_at timestamp NULL,
    build_duration_ms integer NULL,
    snapshot_rows bigint NULL,
    CONSTRAINT workload_snapshot_meta_singleton CHECK (id = 1)
);

INSERT INTO workload_snapshot_meta (id, active_generation)
VALUES (1, 0)
ON CONFLICT (id) DO NOTHING;

-- ------------------------------------------------------------- Layer 1: snapshot
-- One physical table per generation; identical schema. The row grain is one row
-- per (fresh host, container, process); hosts with no containers / containers
-- with no processes contribute a row with NULL child columns (LEFT JOIN grain),
-- matching the previous live flatten exactly.
DO $$
DECLARE
    gen text;
BEGIN
    FOREACH gen IN ARRAY ARRAY['0', '1'] LOOP
        EXECUTE format($f$
            CREATE TABLE IF NOT EXISTS workload_snapshot_%1$s (
                host_id bigint NOT NULL,
                hostname text NOT NULL,
                ip_address text NULL,
                service_name text NOT NULL,
                agent_version text NULL,
                run_mode text NULL,
                heartbeat_timestamp timestamp NOT NULL,
                namespace text NULL,
                pod_name text NULL,
                container_name text NULL,
                workload_name text NULL,
                workload_kind text NULL,
                pid integer NULL,
                process_name text NULL,
                command_type text NULL,
                command_status text NULL,
                combined_config jsonb NULL,
                profiling_status text NULL
            )
        $f$, gen);
        -- Filter-column indexes so filtered reads touch only the matching subset.
        EXECUTE format('CREATE INDEX IF NOT EXISTS idx_wl_snap_%1$s_service ON workload_snapshot_%1$s (service_name)', gen);
        EXECUTE format('CREATE INDEX IF NOT EXISTS idx_wl_snap_%1$s_hostname ON workload_snapshot_%1$s (hostname)', gen);
        EXECUTE format('CREATE INDEX IF NOT EXISTS idx_wl_snap_%1$s_namespace ON workload_snapshot_%1$s (namespace)', gen);
        EXECUTE format('CREATE INDEX IF NOT EXISTS idx_wl_snap_%1$s_pod ON workload_snapshot_%1$s (pod_name)', gen);
        EXECUTE format('CREATE INDEX IF NOT EXISTS idx_wl_snap_%1$s_container ON workload_snapshot_%1$s (container_name)', gen);
        EXECUTE format('CREATE INDEX IF NOT EXISTS idx_wl_snap_%1$s_process ON workload_snapshot_%1$s (process_name)', gen);
        EXECUTE format('CREATE INDEX IF NOT EXISTS idx_wl_snap_%1$s_pid ON workload_snapshot_%1$s (pid)', gen);
    END LOOP;
END $$;

-- ------------------------------------------------- Layer 2: per-scope grouped rows
-- Precomputed grouped rows for the coarse, few/large-entity scopes that are slow
-- to aggregate live (service / namespace / pod / host). Fine scopes
-- (container / process) are served from the Layer 1 snapshot directly. Column
-- shape mirrors the dicts get_workload_inventory_status returns, so the reader
-- reuses the existing row-building code.
DO $$
DECLARE
    gen text;
BEGIN
    FOREACH gen IN ARRAY ARRAY['0', '1'] LOOP
        EXECUTE format($f$
            CREATE TABLE IF NOT EXISTS workload_scope_summary_%1$s (
                scope text NOT NULL,
                sort_seq integer NOT NULL,          -- default (key) ordering position
                row_id text NOT NULL,
                service_name text NULL,
                hostname text NULL,
                namespace text NULL,
                pod_name text NULL,
                container_name text NULL,
                host_count integer NULL,
                namespace_count integer NULL,
                pod_count integer NULL,
                container_count integer NULL,
                process_count integer NULL,
                pids integer[] NULL,
                l_hostname text NULL,
                l_ip_address text NULL,
                l_namespace text NULL,
                l_pod_name text NULL,
                l_container_name text NULL,
                l_workload_name text NULL,
                l_workload_kind text NULL,
                l_process_name text NULL,
                l_pid integer NULL,
                l_command_type text NULL,
                l_command_status text NULL,
                l_combined_config jsonb NULL,
                any_active boolean NULL,
                l_profiling_status text NULL,
                l_agent_version text NULL,
                l_run_mode text NULL,
                l_heartbeat_timestamp timestamp NULL
            )
        $f$, gen);
        EXECUTE format('CREATE INDEX IF NOT EXISTS idx_wl_summary_%1$s_scope_seq ON workload_scope_summary_%1$s (scope, sort_seq)', gen);
    END LOOP;
END $$;

-- ------------------------------------------------------- Layer 2: tab counts
CREATE TABLE IF NOT EXISTS workload_tab_counts_0 (
    scope text PRIMARY KEY,
    count bigint NOT NULL
);
CREATE TABLE IF NOT EXISTS workload_tab_counts_1 (
    scope text PRIMARY KEY,
    count bigint NOT NULL
);

-- --------------------------------------------------------------- read-side views
-- Point at generation 0 initially; the refresh worker re-points these on swap.
CREATE OR REPLACE VIEW workload_snapshot AS SELECT * FROM workload_snapshot_0;
CREATE OR REPLACE VIEW workload_scope_summary AS SELECT * FROM workload_scope_summary_0;
CREATE OR REPLACE VIEW workload_tab_counts AS SELECT * FROM workload_tab_counts_0;

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
-- any_active for the coarse scopes is computed at the host grain (a host with an
-- active whole-host command marks its groups active). For service scope this is
-- provably identical to the PID-aware value; for namespace/pod it can over-mark
-- only under PID-targeted commands, which are effectively unused in practice.

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

-- Prune the workload inventory (HeartbeatContainers / HeartbeatProcesses) of hosts with
-- no beat for stale_after, measured against the newest beat so an ingest outage prunes
-- nothing; clears the host's inventory_digest so a returning host re-sends it. Run by
-- the periodic-tasks worker. Rationale: migrations/add_prune_stale_host_inventory.sql.
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
