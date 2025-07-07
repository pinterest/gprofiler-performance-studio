# Profiling Database Schema

This document explains the database schema for the profiling request and command system.

## Overview

The system uses a two-table approach to separate individual profiling requests from the actual commands sent to hosts:

1. **ProfilingRequests** - Individual requests that can target multiple hosts
2. **ProfilingCommands** - Combined/batched commands sent to specific hosts
3. **HostHeartbeats** - Track host status and last executed commands

## Why Two Tables?

**Problem**: When you have multiple profiling requests for the same host at different times (e.g., profile PID 1, then profile PID 2), you want to combine them into a single command to avoid running separate profiling sessions.

**Solution**: 
- Store individual requests in `ProfilingRequests`
- Combine multiple requests targeting the same host into `ProfilingCommands` 
- Send the combined command to the host via heartbeat response

## Table Descriptions

### ProfilingRequests
Stores individual profiling requests as submitted via the API.

**Key fields:**
- `request_id` - Unique identifier for tracking
- `target_hostnames` - Array of hostnames to target (NULL = all hosts for service)
- `pids` - Array of process IDs to profile (NULL = all processes)
- `status` - pending, processing, completed, failed, cancelled

### ProfilingCommands
Stores combined commands ready to be sent to hosts.

**Key fields:**
- `command_id` - Unique identifier sent to host
- `hostname` - Specific host this command targets
- `combined_config` - Merged configuration from multiple requests
- `request_ids` - Array of ProfilingRequests IDs that were combined
- `status` - pending, sent, completed, failed

### HostHeartbeats
Tracks host status and last executed command for idempotency.

**Key fields:**
- `hostname` + `service_name` - Unique combination
- `last_command_id` - Last command executed (for idempotency)
- `status` - active, idle, error, offline

## Workflow

1. **Submit Request**: API creates entry in `ProfilingRequests`
2. **Create Commands**: System combines requests targeting same host into `ProfilingCommands`
3. **Heartbeat**: Host checks in, gets pending command if available
4. **Execute**: Host runs profiling and reports completion
5. **Update**: Both command and related requests marked as completed

## Benefits

- **Efficiency**: Multiple requests combined into single profiling session
- **Idempotency**: Hosts won't re-execute completed commands
- **Tracking**: Clear audit trail from request to execution
- **Scalability**: Works with multiple hosts and services
