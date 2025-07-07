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

-- Migration script to add profiling request and command tables
-- This approach separates individual requests from combined commands sent to hosts

-- Create enum for profiling modes (if not exists)
DO $$ BEGIN
    CREATE TYPE ProfilingMode AS ENUM ('cpu', 'allocation', 'none');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- Create enum for profiling request status
DO $$ BEGIN
    CREATE TYPE ProfilingRequestStatus AS ENUM ('pending', 'processing', 'completed', 'failed', 'cancelled');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- Create enum for profiling command status
DO $$ BEGIN
    CREATE TYPE ProfilingCommandStatus AS ENUM ('pending', 'sent', 'completed', 'failed');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- Create enum for host status
DO $$ BEGIN
    CREATE TYPE HostStatus AS ENUM ('active', 'idle', 'error', 'offline');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- Table for storing individual profiling requests
CREATE TABLE IF NOT EXISTS ProfilingRequests (
    ID bigserial PRIMARY KEY,
    request_id uuid UNIQUE NOT NULL DEFAULT gen_random_uuid(),
    service_name text NOT NULL,
    duration integer DEFAULT 60 CHECK (duration > 0),
    frequency integer DEFAULT 11 CHECK (frequency > 0),
    profiling_mode ProfilingMode DEFAULT 'cpu',
    target_hostnames text[], -- Array of target hostnames
    pids integer[], -- Array of target PIDs
    additional_args jsonb, -- Store additional arguments as JSON
    status ProfilingRequestStatus DEFAULT 'pending',
    created_at timestamp DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp DEFAULT CURRENT_TIMESTAMP,
    completed_at timestamp,
    estimated_completion_time timestamp,
    error_message text
);

-- Table for storing combined profiling commands sent to hosts
CREATE TABLE IF NOT EXISTS ProfilingCommands (
    ID bigserial PRIMARY KEY,
    command_id uuid UNIQUE NOT NULL DEFAULT gen_random_uuid(),
    hostname text NOT NULL,
    service_name text NOT NULL,
    combined_config jsonb NOT NULL, -- Combined configuration from multiple requests
    request_ids uuid[] NOT NULL, -- Array of request IDs that make up this command
    status ProfilingCommandStatus DEFAULT 'pending',
    created_at timestamp DEFAULT CURRENT_TIMESTAMP,
    sent_at timestamp,
    completed_at timestamp,
    execution_time integer, -- seconds
    error_message text,
    results_path text -- S3 path or local path to results
);

-- Table for tracking host heartbeats
CREATE TABLE IF NOT EXISTS HostHeartbeats (
    ID bigserial PRIMARY KEY,
    hostname text NOT NULL,
    ip_address inet NOT NULL,
    service_name text NOT NULL,
    status HostStatus DEFAULT 'active',
    last_command_id uuid, -- Last command ID executed by this host
    last_heartbeat timestamp DEFAULT CURRENT_TIMESTAMP,
    created_at timestamp DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp DEFAULT CURRENT_TIMESTAMP,
    
    -- Unique constraint on hostname + service_name combination
    UNIQUE(hostname, service_name)
);

-- Indexes for better query performance

-- ProfilingRequests indexes
CREATE INDEX IF NOT EXISTS idx_profiling_requests_service_name ON ProfilingRequests(service_name);
CREATE INDEX IF NOT EXISTS idx_profiling_requests_status ON ProfilingRequests(status);
CREATE INDEX IF NOT EXISTS idx_profiling_requests_target_hostnames ON ProfilingRequests USING GIN(target_hostnames);
CREATE INDEX IF NOT EXISTS idx_profiling_requests_created_at ON ProfilingRequests(created_at);

-- ProfilingCommands indexes
CREATE INDEX IF NOT EXISTS idx_profiling_commands_hostname ON ProfilingCommands(hostname);
CREATE INDEX IF NOT EXISTS idx_profiling_commands_service_name ON ProfilingCommands(service_name);
CREATE INDEX IF NOT EXISTS idx_profiling_commands_status ON ProfilingCommands(status);
CREATE INDEX IF NOT EXISTS idx_profiling_commands_created_at ON ProfilingCommands(created_at);
CREATE INDEX IF NOT EXISTS idx_profiling_commands_request_ids ON ProfilingCommands USING GIN(request_ids);

-- HostHeartbeats indexes
CREATE INDEX IF NOT EXISTS idx_host_heartbeats_hostname ON HostHeartbeats(hostname);
CREATE INDEX IF NOT EXISTS idx_host_heartbeats_service_name ON HostHeartbeats(service_name);
CREATE INDEX IF NOT EXISTS idx_host_heartbeats_status ON HostHeartbeats(status);
CREATE INDEX IF NOT EXISTS idx_host_heartbeats_last_heartbeat ON HostHeartbeats(last_heartbeat);
CREATE INDEX IF NOT EXISTS idx_host_heartbeats_last_command_id ON HostHeartbeats(last_command_id);

-- Function to automatically update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Triggers to automatically update updated_at
DROP TRIGGER IF EXISTS update_profiling_requests_updated_at ON ProfilingRequests;
DROP TRIGGER IF EXISTS update_host_heartbeats_updated_at ON HostHeartbeats;

CREATE TRIGGER update_profiling_requests_updated_at BEFORE UPDATE ON ProfilingRequests FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_host_heartbeats_updated_at BEFORE UPDATE ON HostHeartbeats FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();


