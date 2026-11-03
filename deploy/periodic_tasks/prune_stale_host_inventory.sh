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

# Removes the workload inventory (HeartbeatContainers / HeartbeatProcesses rows) of
# hosts that stopped heartbeating, which refresh_workload_snapshot would otherwise keep
# scanning on every build. cron invokes this every 10 minutes.

# cron jobs do not inherit the container env; load the DB connection vars the
# container start-up persisted (see periodic_tasks/Dockerfile).
if [ -f /tmp/cron.env ]; then
    while IFS='=' read -r _k _v; do
        case "$_k" in
            PGHOST|PGPORT|PGUSER|PGPASSWORD|PGDATABASE|GPROFILER_INVENTORY_PRUNE_AFTER) export "$_k=$_v" ;;
        esac
    done < /tmp/cron.env
fi

# How long a host must go without a beat before its inventory is pruned. Keep it well
# above GPROFILER_WORKLOAD_FRESH_INTERVAL: a pruned host that comes back re-sends its
# full inventory. Restrict to a simple "<n> <unit>" form (allowlist) since it is
# interpolated into the CALL literal.
PRUNE_AFTER="${GPROFILER_INVENTORY_PRUNE_AFTER:-1 hour}"
if ! printf '%s' "$PRUNE_AFTER" | grep -Eqi '^[0-9]+ (minute|minutes|hour|hours|day|days)$'; then
    echo "invalid GPROFILER_INVENTORY_PRUNE_AFTER='$PRUNE_AFTER', using '1 hour'"
    PRUNE_AFTER="1 hour"
fi

echo "stale host inventory prune started ($(date -u +%FT%TZ)) prune_after='$PRUNE_AFTER'"
psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" \
    -c "CALL prune_stale_host_inventory(INTERVAL '$PRUNE_AFTER')"
