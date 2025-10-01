# gProfiler Performance Studio - Heartbeat-Based Profiling Control

This document describes the heartbeat-based profiling control system that allows dynamic start/stop of profiling sessions through API commands with hierarchical targeting support.

## Overview

The heartbeat system enables remote control of gProfiler agents through a simple yet robust mechanism with support for hierarchical profiling:

1. **Agents send periodic heartbeats** to the Performance Studio backend
2. **Backend responds with profiling commands** (start/stop) when available
3. **Agents execute commands with built-in idempotency** to prevent duplicate execution
4. **Commands are tracked and logged** for audit and debugging
5. **Hierarchical targeting** enables service-level profiling with plans for K8s namespace, pod, and container-level control

## Architecture

```
┌─────────────────┐    heartbeat     ┌──────────────────────┐
│   gProfiler     │ ──────────────► │  Performance Studio  │
│    Agent        │                 │      Backend         │
│                 │ ◄────────────── │                      │
└─────────────────┘   commands      └──────────────────────┘
        │                                       │
        │                                       │
        ▼                                       ▼
┌─────────────────┐                 ┌──────────────────────┐
│  Profile Data   │                 │   PostgreSQL DB      │
│  (S3/Local)     │                 │   - Host Heartbeats  │
└─────────────────┘                 │   - Hierarchy Cmds   │
                                    │   - Profiling Cmds   │
                                    │   - Profiling Reqs   │
                                    └──────────────────────┘
```

### Hierarchical Command Flow

```
API Request (Service) → ProfilingHierarchyCommand → ProfilingCommand(s) → Agent(s)
       │                          │                         │                │
       │                          │                         │                │
       ▼                          ▼                         ▼                ▼
  Service-level              Hierarchy table          Host-specific     Individual
    request                 (service_name,             commands           agents
                           container_name,           (hostname,
                           pod_name,                 service_name)
                           namespace)
```

## Database Schema

### Core Tables

1. **HostHeartbeats** - Track agent status and last seen information
2. **ProfilingRequests** - Store individual profiling requests from API calls
3. **ProfilingHierarchyCommands** - Store hierarchical profiling commands targeting services, K8s namespaces, pods, or containers
4. **ProfilingCommands** - Commands sent to specific agents (merged from hierarchy commands)
5. **ProfilingExecutions** - Execution history for audit trail

### Hierarchical Profiling Architecture

The new hierarchical system introduces a two-tier command structure:

- **ProfilingHierarchyCommands**: Higher-level commands that target entire services, K8s namespaces, pods, or containers
- **ProfilingCommands**: Host-specific commands generated from hierarchy commands for individual agents

### Key Features
- **Simple DDL** with essential indexes only
- **No stored procedures** - all logic in application code
- **No triggers** - timestamps handled by application
- **Consistent naming** with `idx_` prefix for all indexes
- **Hierarchical targeting** supporting service-level, namespace-level, pod-level, and container-level profiling

## API Endpoints

### 1. Create Profiling Request
```http
POST /api/metrics/profile_request
Content-Type: application/json

{
  "service_name": "my-service",
  "request_type": "start",
  "duration": 60,
  "frequency": 11,
  "profiling_mode": "cpu",
  "target_hosts": {
    "host1": [1234, 5678],
    "host2": null
  },
  "stop_level": "process",
  "continuous": false,
  "additional_args": {}
}
```

**Response:**
```json
{
  "success": true,
  "message": "Start profiling request submitted successfully",
  "request_id": "12345678-1234-1234-1234-123456789abc",
  "command_ids": ["87654321-4321-4321-4321-cba987654321"],
  "hierarchy_command_ids": ["11111111-2222-3333-4444-555555555555"],
  "estimated_completion_time": "2025-01-15T10:30:00Z"
}
```

**Request Parameters:**
- `service_name` (required): Target service name
- `request_type` (required): Either "start" or "stop"
- `duration` (optional): Profiling duration in seconds (default: 60)
- `frequency` (optional): Profiling frequency in Hz (default: 11)
- `profiling_mode` (optional): "cpu", "allocation", or "none" (default: "cpu")
- `target_hosts` (optional): Dictionary mapping hostnames to PIDs (null for all processes)
- `stop_level` (optional): "process" or "host" (default: "process")
- `continuous` (optional): Whether profiling should run continuously (default: false)
- `additional_args` (optional): Additional profiler arguments

**Response Fields:**
- `success`: Whether the request was accepted
- `message`: Human-readable status message
- `request_id`: Unique identifier for this specific request
- `command_ids`: List of host-specific command IDs generated
- `hierarchy_command_ids`: List of service-level hierarchy command IDs
- `estimated_completion_time`: When profiling is expected to complete

### 2. Agent Heartbeat
```http
POST /api/metrics/heartbeat
Content-Type: application/json

{
  "hostname": "host1",
  "ip_address": "10.0.1.100",
  "service_name": "my-service",
  "last_command_id": "previous-command-uuid",
  "status": "active"
}
```

**Response (with command):**
```json
{
  "success": true,
  "message": "Heartbeat received. New profiling command available.",
  "command_id": "new-command-uuid",
  "profiling_command": {
    "command_type": "start",
    "combined_config": {
      "duration": 60,
      "frequency": 11,
      "profiling_mode": "cpu",
      "pids": "1234,5678"
    }
  }
}
```

### 3. Command Completion
```http
POST /api/metrics/command_completion
Content-Type: application/json

{
  "command_id": "command-uuid",
  "hostname": "host1",
  "status": "completed",
  "execution_time": 65,
  "results_path": "/path/to/results"
}
```

## Agent Integration

### Heartbeat Configuration
```bash
python3 gprofiler/main.py \
  --enable-heartbeat-server \
  --api-server "https://perf-studio.example.com" \
  --heartbeat-interval 30 \
  --service-name "my-service" \
  --token "api-token"
```

### Heartbeat Flow
1. **Agent sends heartbeat** every 30 seconds (configurable)
2. **Backend checks for pending commands** for this hostname/service
3. **If command available**, backend responds with command details
4. **Agent executes command** and reports completion
5. **Idempotency ensured** by tracking `last_command_id`

## Command Types

### START Commands
- Create new profiling sessions
- Merge multiple requests for same host
- Include combined configuration (duration, frequency, PIDs)

### STOP Commands
- **Process-level**: Stop specific PIDs
- **Host-level**: Stop entire profiling session
- Automatic conversion when only one PID remains

## Hierarchical Profiling System

The new hierarchical profiling system introduces a two-tier architecture that enables profiling at different organizational levels:

### Hierarchy Levels

1. **Service Level** (Currently Implemented)
   - Target entire services by name
   - Commands automatically distributed to all hosts in the service
   - Example: Profile all instances of "web-service"

2. **Kubernetes Namespace Level** (Future Implementation)
   - Target all pods within a specific namespace
   - Commands distributed to all pods in the namespace
   - Example: Profile all pods in "production" namespace

3. **Pod Level** (Future Implementation)
   - Target specific Kubernetes pods
   - Commands sent to containers within the pod
   - Example: Profile specific pods like "web-pod-12345"

4. **Container Level** (Future Implementation)
   - Target specific containers within pods
   - Fine-grained control over containerized applications
   - Example: Profile specific containers like "nginx-container"

### Command Flow

```
API Request → ProfilingHierarchyCommand → ProfilingCommand(s) → Agent(s)
     │                    │                        │               │
     │                    │                        │               │
     ▼                    ▼                        ▼               ▼
Service-level      Hierarchy table         Host-specific    Individual
  request           (service_name,           commands         agents
                   container_name,           (hostname,
                   pod_name,                 service_name)
                   namespace)
```

### Hierarchy Command Structure

The `ProfilingHierarchyCommands` table supports the following targeting options:

- **service_name**: Target all hosts in a service
- **namespace**: Target all pods in a K8s namespace (future)
- **pod_name**: Target specific pods (future)
- **container_name**: Target specific containers (future)

### Current Implementation Status

- ✅ **Service-level profiling**: Fully implemented
- 🚧 **Namespace-level profiling**: Planned for future releases
- 🚧 **Pod-level profiling**: Planned for future releases
- 🚧 **Container-level profiling**: Planned for future releases

## Data Flow Example

### 1. Create Service-Level Profiling Request
```bash
curl -X POST http://localhost:8000/api/metrics/profile_request \
  -H "Content-Type: application/json" \
  -d '{
    "service_name": "web-service",
    "request_type": "start",
    "duration": 120,
    "profiling_mode": "cpu",
    "continuous": false
  }'
```

**Response:**
```json
{
  "success": true,
  "message": "Start profiling request submitted successfully",
  "request_id": "req-12345678-1234-1234-1234-123456789abc",
  "command_ids": ["cmd-87654321-4321-4321-4321-cba987654321"],
  "hierarchy_command_ids": ["hier-11111111-2222-3333-4444-555555555555"],
  "estimated_completion_time": "2025-01-15T10:32:00Z"
}
```

### 2. Agent Heartbeat
```bash
# Agent automatically sends:
curl -X POST http://localhost:8000/api/metrics/heartbeat \
  -H "Content-Type: application/json" \
  -d '{
    "hostname": "web-01",
    "ip_address": "10.0.1.10",
    "service_name": "web-service",
    "status": "active"
  }'
```

### 3. Agent Receives Command
```json
{
  "success": true,
  "command_id": "cmd-12345",
  "profiling_command": {
    "command_type": "start",
    "combined_config": {
      "duration": 120,
      "frequency": 11,
      "profiling_mode": "cpu"
    }
  }
}
```

### 4. Agent Reports Completion
```bash
curl -X POST http://localhost:8000/api/metrics/command_completion \
  -H "Content-Type: application/json" \
  -d '{
    "command_id": "cmd-12345",
    "hostname": "web-01",
    "status": "completed",
    "execution_time": 122
  }'
```

### Future Hierarchical Profiling Examples

The following examples show planned functionality for future releases:

#### Kubernetes Namespace Profiling (Planned)
```bash
curl -X POST http://localhost:8000/api/metrics/profile_request \
  -H "Content-Type: application/json" \
  -d '{
    "namespace": "production",
    "request_type": "start",
    "duration": 300,
    "profiling_mode": "cpu"
  }'
```

#### Pod-Level Profiling (Planned)
```bash
curl -X POST http://localhost:8000/api/metrics/profile_request \
  -H "Content-Type: application/json" \
  -d '{
    "pod_name": "web-pod-12345",
    "namespace": "production",
    "request_type": "start",
    "duration": 180,
    "profiling_mode": "allocation"
  }'
```

#### Container-Level Profiling (Planned)
```bash
curl -X POST http://localhost:8000/api/metrics/profile_request \
  -H "Content-Type: application/json" \
  -d '{
    "container_name": "nginx-container",
    "pod_name": "web-pod-12345",
    "namespace": "production",
    "request_type": "start",
    "duration": 120,
    "profiling_mode": "cpu"
  }'
```

## Testing

### 1. Test Heartbeat System
```bash
cd gprofiler-performance-studio
python3 test_heartbeat_system.py
```

This script:
- Simulates agent heartbeat behavior
- Creates test profiling requests
- Verifies command delivery and idempotency
- Tests both start and stop commands

### 2. Run Test Agent
```bash
python3 run_heartbeat_agent.py
```

This script:
- Starts a real gProfiler agent in heartbeat mode
- Connects to the Performance Studio backend
- Receives and executes actual profiling commands

## Configuration

### Backend Configuration
```yaml
# Backend settings
database:
  host: localhost
  port: 5432
  database: gprofiler
  
heartbeat:
  max_age_minutes: 10  # Consider hosts offline after 10 minutes
  cleanup_interval: 300  # Clean up old records every 5 minutes
```

### Agent Configuration
```bash
# Required parameters
--enable-heartbeat-server          # Enable heartbeat mode
--api-server URL                   # Performance Studio backend URL
--service-name NAME                # Service identifier
--heartbeat-interval SECONDS       # Heartbeat frequency (default: 30)

# Optional parameters
--token TOKEN                      # Authentication token
--server-host URL                  # Profile upload server (can be same as api-server)
--no-verify                        # Skip SSL verification (testing only)
```

## Monitoring and Debugging

### Database Queries
```sql
-- Check active hosts
SELECT hostname, service_name, status, heartbeat_timestamp 
FROM HostHeartbeats 
WHERE status = 'active' AND heartbeat_timestamp > NOW() - INTERVAL '10 minutes';

-- Check pending hierarchy commands
SELECT service_name, container_name, pod_name, namespace, command_type, created_at 
FROM ProfilingHierarchyCommands 
ORDER BY created_at DESC;

-- Check pending host-specific commands
SELECT hostname, service_name, command_type, status, created_at 
FROM ProfilingCommands 
WHERE status = 'pending';

-- Check command execution history
SELECT pe.hostname, pr.request_type, pe.status, pe.execution_time
FROM ProfilingExecutions pe
JOIN ProfilingRequests pr ON pe.profiling_request_id = pr.request_id
ORDER BY pe.created_at DESC;

-- Monitor service-level profiling activity
SELECT 
    phc.service_name,
    phc.command_type,
    COUNT(pc.hostname) as target_hosts,
    phc.created_at
FROM ProfilingHierarchyCommands phc
LEFT JOIN ProfilingCommands pc ON phc.command_id = ANY(string_to_array(pc.combined_config->>'hierarchy_command_ids', ',')::uuid[])
GROUP BY phc.service_name, phc.command_type, phc.created_at
ORDER BY phc.created_at DESC;
```
JOIN ProfilingRequests pr ON pe.profiling_request_id = pr.ID
ORDER BY pe.created_at DESC;
```

### Log Monitoring
```bash
# Backend logs
tail -f /var/log/gprofiler-studio/backend.log | grep -E "(heartbeat|command)"

# Agent logs  
tail -f /tmp/gprofiler-heartbeat.log | grep -E "(heartbeat|command)"
```

## Troubleshooting

### Common Issues

1. **Agents not receiving commands**
   - Check heartbeat connectivity to backend
   - Verify service_name matches between request and agent
   - Check agent authentication (token)

2. **Commands executing multiple times**
   - Verify agent is tracking `last_command_id` correctly
   - Check for agent restarts that reset command tracking

3. **Commands not being created**
   - Verify `target_hostnames` includes the agent's hostname
   - Check database constraints and foreign key relationships

### Debug Commands
```bash
# Test backend connectivity
curl -v http://localhost:8000/api/metrics/heartbeat \
  -H "Content-Type: application/json" \
  -d '{"hostname":"test","ip_address":"127.0.0.1","service_name":"test","status":"active"}'

# Check database state
psql -d gprofiler -c "SELECT * FROM HostHeartbeats ORDER BY heartbeat_timestamp DESC LIMIT 5;"
```

## Security Considerations

1. **Authentication**: Use API tokens for agent authentication
2. **Network**: Secure communication with HTTPS/TLS
3. **Authorization**: Validate service permissions before creating commands
4. **Rate Limiting**: Implement rate limits on heartbeat endpoints
5. **Input Validation**: Sanitize all input parameters

## Performance Considerations

1. **Database Indexes**: Essential indexes are created for all lookup patterns
2. **Heartbeat Frequency**: Balance between responsiveness and load (default: 30s)
3. **Command Cleanup**: Implement periodic cleanup of old commands/executions
4. **Connection Pooling**: Use connection pooling for database access

## Future Enhancements

1. **Agent Discovery**: Automatic service registration
2. **Command Queuing**: Support for command queues per host
3. **Conditional Commands**: Commands based on host metrics or state
4. **Command Templates**: Predefined command templates for common scenarios
5. **Real-time Dashboard**: Web UI for monitoring active agents and commands
