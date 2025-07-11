# Heartbeat Profiling System Implementation

This document describes the implementation of the heartbeat-based profiling control system where a central backend (Performance Studio) issues profiling commands to agents (gprofiler) via a heartbeat protocol.

## System Overview

The system implements:

1. **Command-driven profiling**: Agents only start/stop profiling when instructed by the backend
2. **Idempotent commands**: Each command has a unique `command_id` that agents track for idempotency
3. **Heartbeat protocol**: Agents periodically poll the backend to receive new commands
4. **Process and host-level control**: Support for stopping specific processes or entire hosts

## Backend Implementation (Performance Studio)

### Database Schema

**Tables added:**
- `ProfilingRequests` - Enhanced with `command_type` and `stop_level` fields
- `HostHeartbeats` - Tracks host heartbeat information
- `ProfilingCommands` - Stores commands to be sent to hosts
- `ProfilingExecutions` - Detailed execution tracking

**Database functions:**
- `upsert_host_heartbeat()` - Updates host heartbeat info
- `get_pending_profiling_request()` - Gets pending requests for a host
- `mark_profiling_request_assigned()` - Marks request as assigned to a host

### API Endpoints

#### POST `/profile_request`
Creates profiling requests with support for:
- **Start commands**: Create new profiling sessions
- **Stop commands**: Process-level or host-level stops
- **Command merging**: Multiple requests for same host are combined

**Request model:**
```json
{
  "service_name": "string",
  "command_type": "start|stop",
  "duration": 60,
  "frequency": 11,
  "profiling_mode": "cpu|allocation|none",
  "target_hostnames": ["host1", "host2"],
  "pids": [1234, 5678],
  "stop_level": "process|host",
  "additional_args": {}
}
```

#### POST `/heartbeat`
Handles agent heartbeats and returns pending commands:

**Request model:**
```json
{
  "ip_address": "192.168.1.1",
  "hostname": "web-01",
  "service_name": "web-service",
  "last_command_id": "uuid-of-last-executed-command",
  "status": "active|idle|error"
}
```

**Response model:**
```json
{
  "success": true,
  "message": "Heartbeat received. New profiling command available.",
  "profiling_command": {
    "command_type": "start|stop",
    "combined_config": {
      "duration": 60,
      "frequency": 11,
      "profiling_mode": "cpu",
      "pids": [1234, 5678]
    }
  },
  "command_id": "uuid-of-new-command"
}
```

#### POST `/command_completion`
Receives command execution status from agents:

**Request model:**
```json
{
  "command_id": "uuid",
  "hostname": "web-01",
  "status": "completed|failed",
  "execution_time": 65,
  "error_message": "Optional error message",
  "results_path": "s3://bucket/path/to/results"
}
```

### DBManager Methods

**New methods added:**
- `save_profiling_request()` - Enhanced with command_type/stop_level support
- `create_or_update_profiling_command()` - Creates commands for hosts
- `create_stop_command_for_host()` - Host-level stop commands
- `handle_process_level_stop()` - Process-level stop logic with PID management
- `get_pending_profiling_command()` - Gets pending commands for heartbeat
- `mark_profiling_command_sent()` - Marks command as sent to agent
- `update_profiling_command_status()` - Updates command execution status

## Agent Implementation (gprofiler)

The agent implementation includes:

1. **Heartbeat loop**: Periodic polling of `/heartbeat` endpoint
2. **Command processing**: Parse and execute start/stop commands
3. **Idempotency**: Track `last_command_id` to avoid duplicate execution
4. **Status reporting**: Report command completion via `/command_completion`

### Agent Logic Flow

```python
# Heartbeat loop (every 30 seconds)
while True:
    response = post_heartbeat(hostname, service, last_command_id)
    
    if response.command_id and response.command_id != last_command_id:
        # New command received
        if response.profiling_command.command_type == "start":
            stop_current_profiler()  # Stop any running profiler
            start_new_profiler(response.profiling_command.combined_config)
        elif response.profiling_command.command_type == "stop":
            stop_current_profiler()
            # Don't start a new one
        
        # Update tracking
        last_command_id = response.command_id
        
        # Report completion
        post_command_completion(response.command_id, "completed")
    
    sleep(30)
```

## Stop Command Logic

### Process-level Stop
When stopping specific PIDs:
1. **Multiple PIDs in command**: Remove specified PIDs, continue with remaining PIDs
2. **Single PID in command**: Convert to host-level stop (stop entire session)

### Host-level Stop
Stops the entire profiling session for the host.

## Key Features

1. **Idempotency**: Agents only execute commands with new `command_id` values
2. **Command merging**: Multiple start requests for the same host are combined
3. **Graceful degradation**: Heartbeat failures don't crash the system
4. **Audit trail**: Full tracking of requests, commands, and executions
5. **Flexible targeting**: Support for specific hostnames, PIDs, or service-wide commands

## Files Modified/Created

### Backend (Performance Studio)
- `src/gprofiler/backend/routers/metrics_routes.py` - API endpoints
- `src/gprofiler-dev/gprofiler_dev/postgres/db_manager.py` - Database methods
- `scripts/setup/postgres/add_heartbeat_system_tables.sql` - Database schema

### Agent (gprofiler)
- `gprofiler/main.py` - Agent heartbeat and command processing logic

### Documentation
- `HEARTBEAT_SYSTEM_README.md` - This documentation file

## Testing

The system includes test scripts for validation:
- `test_heartbeat_system.py` - Backend API testing
- `run_heartbeat_agent.py` - Agent simulation

## Database Setup

To set up the required database tables and functions:

```sql
-- Run the schema migration
\i scripts/setup/postgres/add_profiling_tables.sql
\i scripts/setup/postgres/add_heartbeat_system_tables.sql
```

This creates all necessary tables, indexes, functions, and triggers for the heartbeat profiling system.
