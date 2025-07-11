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

-- Additional tables and functions for heartbeat profiling system

-- First, update ProfilingRequests table to include command_type and stop_level
ALTER TABLE ProfilingRequests 
ADD COLUMN IF NOT EXISTS command_type text DEFAULT 'start' CHECK (command_type IN ('start', 'stop'));

ALTER TABLE ProfilingRequests 
ADD COLUMN IF NOT EXISTS stop_level text DEFAULT 'process' CHECK (stop_level IN ('process', 'host'));

-- Table for storing host heartbeat information
CREATE TABLE IF NOT EXISTS HostHeartbeats (
    ID bigserial PRIMARY KEY,
    hostname text NOT NULL,
    ip_address inet NOT NULL,
    service_name text NOT NULL,
    last_command_id uuid,
    status HostStatus DEFAULT 'active',
    heartbeat_timestamp timestamp DEFAULT CURRENT_TIMESTAMP,
    created_at timestamp DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp DEFAULT CURRENT_TIMESTAMP,
    
    -- Unique constraint to ensure one record per hostname
    UNIQUE(hostname)
);

-- Table for storing profiling commands sent to hosts
CREATE TABLE IF NOT EXISTS ProfilingCommands (
    ID bigserial PRIMARY KEY,
    command_id uuid UNIQUE NOT NULL DEFAULT gen_random_uuid(),
    command_type text NOT NULL CHECK (command_type IN ('start', 'stop')),
    hostname text NOT NULL,
    service_name text NOT NULL,
    combined_config jsonb, -- Combined configuration from multiple requests
    request_ids uuid[], -- Array of request IDs that make up this command
    status text DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'completed', 'failed')),
    created_at timestamp DEFAULT CURRENT_TIMESTAMP,
    sent_at timestamp,
    completed_at timestamp,
    execution_time integer, -- Execution time in seconds
    error_message text,
    results_path text, -- S3 path or local path to results
    
    -- Unique constraint to ensure one command per hostname/service at a time
    UNIQUE(hostname, service_name)
);

-- Table for tracking profiling executions (detailed execution records)
CREATE TABLE IF NOT EXISTS ProfilingExecutions (
    ID bigserial PRIMARY KEY,
    profiling_request_id bigint NOT NULL REFERENCES ProfilingRequests(ID) ON DELETE CASCADE,
    command_id uuid NOT NULL,
    hostname text NOT NULL,
    status ProfilingRequestStatus DEFAULT 'in_progress',
    started_at timestamp DEFAULT CURRENT_TIMESTAMP,
    completed_at timestamp,
    execution_time integer, -- Execution time in seconds
    error_message text,
    results_path text -- S3 path or local path to results
);

-- Indexes for better performance
CREATE INDEX IF NOT EXISTS idx_host_heartbeats_hostname ON HostHeartbeats(hostname);
CREATE INDEX IF NOT EXISTS idx_host_heartbeats_service_name ON HostHeartbeats(service_name);
CREATE INDEX IF NOT EXISTS idx_host_heartbeats_status ON HostHeartbeats(status);
CREATE INDEX IF NOT EXISTS idx_host_heartbeats_timestamp ON HostHeartbeats(heartbeat_timestamp);

CREATE INDEX IF NOT EXISTS idx_profiling_commands_hostname ON ProfilingCommands(hostname);
CREATE INDEX IF NOT EXISTS idx_profiling_commands_service_name ON ProfilingCommands(service_name);
CREATE INDEX IF NOT EXISTS idx_profiling_commands_status ON ProfilingCommands(status);
CREATE INDEX IF NOT EXISTS idx_profiling_commands_command_id ON ProfilingCommands(command_id);
CREATE INDEX IF NOT EXISTS idx_profiling_commands_created_at ON ProfilingCommands(created_at);

CREATE INDEX IF NOT EXISTS idx_profiling_executions_request_id ON ProfilingExecutions(profiling_request_id);
CREATE INDEX IF NOT EXISTS idx_profiling_executions_command_id ON ProfilingExecutions(command_id);
CREATE INDEX IF NOT EXISTS idx_profiling_executions_hostname ON ProfilingExecutions(hostname);

-- Trigger to automatically update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply triggers
DROP TRIGGER IF EXISTS update_profiling_requests_updated_at ON ProfilingRequests;
CREATE TRIGGER update_profiling_requests_updated_at
    BEFORE UPDATE ON ProfilingRequests
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_host_heartbeats_updated_at ON HostHeartbeats;
CREATE TRIGGER update_host_heartbeats_updated_at
    BEFORE UPDATE ON HostHeartbeats
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
