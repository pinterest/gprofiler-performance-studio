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
# summaries) and atomically swaps it in. cron invokes this once per minute (~60s
# cadence). The procedure self-guards with an advisory lock, so an in-progress
# build is never queued behind -- a concurrent invocation simply skips.

# cron jobs do not inherit the container env; load the DB connection vars the
# container start-up persisted (see periodic_tasks/Dockerfile).
if [ -f /tmp/cron.env ]; then
    while IFS='=' read -r _k _v; do
        case "$_k" in
            PGHOST|PGPORT|PGUSER|PGPASSWORD|PGDATABASE|GPROFILER_WORKLOAD_FRESH_INTERVAL) export "$_k=$_v" ;;
        esac
    done < /tmp/cron.env
fi

# Freshness window for "active" hosts; keep in sync with the webapp's
# GPROFILER_WORKLOAD_FRESH_INTERVAL so the store and live views agree. Restrict to a
# simple "<n> <unit>" form (allowlist) since it is interpolated into the CALL literal.
FRESH_INTERVAL="${GPROFILER_WORKLOAD_FRESH_INTERVAL:-15 minutes}"
if ! printf '%s' "$FRESH_INTERVAL" | grep -Eqi '^[0-9]+ (second|seconds|minute|minutes|hour|hours)$'; then
    echo "invalid GPROFILER_WORKLOAD_FRESH_INTERVAL='$FRESH_INTERVAL', using '15 minutes'"
    FRESH_INTERVAL="15 minutes"
fi

echo "workload snapshot refresh started ($(date -u +%FT%TZ)) fresh_interval='$FRESH_INTERVAL'"
psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" \
    -c "CALL refresh_workload_snapshot(INTERVAL '$FRESH_INTERVAL')"
