#!/bin/sh

#
# Copyright (C) 2023 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

# Removes the rows that would otherwise accumulate forever with host churn: departed
# hosts' inventory, profiling commands and HostHeartbeats rows, old profiling executions
# and old agent processes (see migrations/add_prune_stale_data.sql). cron invokes this
# every 10 minutes. Each step is its own CALL, so a failing step does not stop the rest.

# cron jobs do not inherit the container env; load the DB connection vars the
# container start-up persisted (see periodic_tasks/Dockerfile).
if [ -f /tmp/cron.env ]; then
    while IFS='=' read -r _k _v; do
        case "$_k" in
            PGHOST|PGPORT|PGUSER|PGPASSWORD|PGDATABASE) export "$_k=$_v" ;;
            GPROFILER_INVENTORY_PRUNE_AFTER|GPROFILER_PROFILING_COMMANDS_PRUNE_AFTER) export "$_k=$_v" ;;
            GPROFILER_HOST_HEARTBEATS_PRUNE_AFTER|GPROFILER_PROFILING_EXECUTIONS_PRUNE_AFTER) export "$_k=$_v" ;;
            GPROFILER_PROFILER_PROCESSES_PRUNE_AFTER) export "$_k=$_v" ;;
        esac
    done < /tmp/cron.env
fi

# prune <procedure> <retention env var> <default retention>: CALL the procedure with the
# retention from the env var, or skip it if that is "off". The retention is interpolated
# into the CALL literal, so it is restricted to a simple "<n> <unit>" form (allowlist).
prune() {
    _after=$(printenv "$2")
    _after=${_after:-$3}
    case "$_after" in
        [Oo][Ff][Ff]) echo "$1 skipped ($2=off)"; return ;;
    esac
    if ! printf '%s' "$_after" | grep -Eqi '^[0-9]+ (minute|minutes|hour|hours|day|days)$'; then
        echo "invalid $2='$_after', using '$3'"
        _after=$3
    fi
    echo "$1 started ($(date -u +%FT%TZ)) prune_after='$_after'"
    psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -c "CALL $1(INTERVAL '$_after')"
}

# Keep the inventory well above GPROFILER_WORKLOAD_FRESH_INTERVAL: a pruned host that
# comes back re-sends its full inventory.
prune prune_stale_host_inventory GPROFILER_INVENTORY_PRUNE_AFTER "1 hour"
# A host that comes back after its command is pruned is treated like a new host, so an
# explicit per-host start or stop is lost; keep it long.
prune prune_stale_profiling_commands GPROFILER_PROFILING_COMMANDS_PRUNE_AFTER "7 days"
# After the commands: a host row is kept while its host still has a command.
prune prune_stale_host_heartbeats GPROFILER_HOST_HEARTBEATS_PRUNE_AFTER "1 day"
prune prune_old_profiling_executions GPROFILER_PROFILING_EXECUTIONS_PRUNE_AFTER "30 days"
prune prune_stale_profiler_processes GPROFILER_PROFILER_PROCESSES_PRUNE_AFTER "90 days"
