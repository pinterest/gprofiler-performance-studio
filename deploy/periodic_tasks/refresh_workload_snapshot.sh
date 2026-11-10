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

# Rebuilds the precomputed workload_status Layer 2 (tab counts + coarse-scope
# summaries) and atomically swaps it in. cron invokes this every minute; it builds only
# on minutes that are a multiple of GPROFILER_WORKLOAD_SNAPSHOT_REFRESH_MINUTES and
# exits silently otherwise. The procedure self-guards with an advisory lock, so an
# in-progress build is never queued behind -- a concurrent invocation simply skips.

# cron jobs do not inherit the container env; load the DB connection vars the
# container start-up persisted (see periodic_tasks/Dockerfile).
if [ -f /tmp/cron.env ]; then
    while IFS='=' read -r _k _v; do
        case "$_k" in
            PGHOST|PGPORT|PGUSER|PGPASSWORD|PGDATABASE|GPROFILER_WORKLOAD_FRESH_INTERVAL|GPROFILER_WORKLOAD_SNAPSHOT_REFRESH_MINUTES) export "$_k=$_v" ;;
        esac
    done < /tmp/cron.env
fi

# Minutes between builds. The build's duration grows with the fleet's inventory; keep it
# well inside this interval (workload_snapshot_meta.build_duration_ms) and raise the
# interval as the fleet grows.
REFRESH_MINUTES="${GPROFILER_WORKLOAD_SNAPSHOT_REFRESH_MINUTES:-1}"
if ! printf '%s' "$REFRESH_MINUTES" | grep -Eq '^([1-9]|[1-5][0-9]|60)$'; then
    echo "invalid GPROFILER_WORKLOAD_SNAPSHOT_REFRESH_MINUTES='$REFRESH_MINUTES', using 1"
    REFRESH_MINUTES=1
fi
if [ $(( $(date -u +%s) / 60 % REFRESH_MINUTES )) -ne 0 ]; then
    exit 0
fi

# Freshness window for "active" hosts; keep in sync with the webapp's
# GPROFILER_WORKLOAD_FRESH_INTERVAL so the store and live views agree. Restrict to a
# simple "<n> <unit>" form (allowlist) since it is interpolated into the CALL literal.
FRESH_INTERVAL="${GPROFILER_WORKLOAD_FRESH_INTERVAL:-15 minutes}"
if ! printf '%s' "$FRESH_INTERVAL" | grep -Eqi '^[0-9]+ (second|seconds|minute|minutes|hour|hours)$'; then
    echo "invalid GPROFILER_WORKLOAD_FRESH_INTERVAL='$FRESH_INTERVAL', using '15 minutes'"
    FRESH_INTERVAL="15 minutes"
fi

echo "workload snapshot refresh started ($(date -u +%FT%TZ)) fresh_interval='$FRESH_INTERVAL' refresh_minutes=$REFRESH_MINUTES"
psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" \
    -c "CALL refresh_workload_snapshot(INTERVAL '$FRESH_INTERVAL')"
