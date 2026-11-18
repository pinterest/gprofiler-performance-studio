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

# Removes the ProfilingCommands rows of hosts that stopped heartbeating, which would
# otherwise accumulate forever with host churn. cron invokes this every hour.

# cron jobs do not inherit the container env; load the DB connection vars the
# container start-up persisted (see periodic_tasks/Dockerfile).
if [ -f /tmp/cron.env ]; then
    while IFS='=' read -r _k _v; do
        case "$_k" in
            PGHOST|PGPORT|PGUSER|PGPASSWORD|PGDATABASE|GPROFILER_PROFILING_COMMANDS_PRUNE_AFTER) export "$_k=$_v" ;;
        esac
    done < /tmp/cron.env
fi

# How long a host must go without a beat (and its command without being re-issued) before
# the command is pruned. A host that comes back after that is treated like a new host, so
# an explicit per-host start or stop is lost; keep it long. Restrict to a simple
# "<n> <unit>" form (allowlist) since it is interpolated into the CALL literal.
PRUNE_AFTER="${GPROFILER_PROFILING_COMMANDS_PRUNE_AFTER:-7 days}"
if ! printf '%s' "$PRUNE_AFTER" | grep -Eqi '^[0-9]+ (minute|minutes|hour|hours|day|days)$'; then
    echo "invalid GPROFILER_PROFILING_COMMANDS_PRUNE_AFTER='$PRUNE_AFTER', using '7 days'"
    PRUNE_AFTER="7 days"
fi

echo "stale profiling commands prune started ($(date -u +%FT%TZ)) prune_after='$PRUNE_AFTER'"
psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" \
    -c "CALL prune_stale_profiling_commands(INTERVAL '$PRUNE_AFTER')"
