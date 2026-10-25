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

import os
import re

REDIRECT_DOMAIN = os.getenv("REDIRECT_DOMAIN")


AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_SESSION_TOKEN = os.getenv("AWS_SESSION_TOKEN")
SESSION_POOL_TIMEOUT = int(os.getenv("SESSION_POOL_TIMEOUT", "600"))

PG_DB_NAME = os.getenv("GPROFILER_POSTGRES_DB_NAME", "mydb")
PG_HOST = os.getenv("GPROFILER_POSTGRES_HOST", "localhost")
PG_USER = os.getenv("GPROFILER_POSTGRES_USERNAME")
PG_PORT = os.getenv("GPROFILER_POSTGRES_PORT", 5432)
PG_PASSWORD = os.getenv("GPROFILER_POSTGRES_PASSWORD")
PG_CONNECT_TIMEOUT = int(os.getenv("GPROFILER_POSTGRES_CONNECT_TIMEOUT", 3))
# Each thread gets its own Postgres connection (see gprofiler_dev.postgres.get_postgres_db).
# Connections per process therefore equal the thread count; in the webapp that is
# GPROFILER_WEBAPP_THREAD_POOL_SIZE, which is the knob to size the DB connection count with.

# Inventory change detection (see gprofiler_dev/heartbeat_inventory.py). A beat whose inventory
# digest equals the one stored on the host row does no inventory work. When it differs, the
# full sync is admitted by a per-process token bucket of this many syncs per second (fleet
# total = this x worker processes). Over budget, the host keeps its freshness update and its
# old digest, and syncs on a later beat -- this paces the one-time load on first rollout and
# any fleet-wide inventory change instead of letting it arrive as a herd. <= 0 disables the
# budget (every differing beat syncs immediately; fine for dev / small fleets).
INVENTORY_SYNC_PER_SEC = float(os.getenv("GPROFILER_INVENTORY_SYNC_PER_SEC", "2"))

INSTANCE_RUNS_LRU_CACHE_LIMIT = os.getenv("INSTANCE_RUNS_LRU_CACHE_LIMIT", 1000)
PROFILER_PROCESSES_LRU_CACHE_LIMIT = os.getenv("PROFILER_PROCESSES_LRU_CACHE_LIMIT", 1000)


BUCKET_NAME = os.getenv("BUCKET_NAME", "gprofiler")
BASE_DIRECTORY = "products"
# Optional prefix prepended to every S3 key; empty = no prefix (default)
S3_PATH_PREFIX = os.getenv("S3_PATH_PREFIX", "").strip("/")
# Optional: Custom S3 endpoint for local testing (e.g., LocalStack) or S3-compatible services
# In production, leave unset to use default AWS S3 endpoints
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL")

ACTIVE_HOST_HEARTBEAT_MAX_DELTA_HOURS = int(os.getenv("ACTIVE_HOST_HEARTBEAT_MAX_DELTA_HOURS", 24))


def _validated_sql_interval(value: str, default: str) -> str:
    # This value is interpolated directly into SQL interval literals, so restrict it to a
    # simple "<n> <unit>" form (allowlist) to keep it injection-safe.
    candidate = (value or "").strip()
    if re.fullmatch(r"\d+\s+(second|seconds|minute|minutes|hour|hours)", candidate, re.IGNORECASE):
        return candidate
    return default


# How recent a host's last heartbeat must be to count as "fresh"/active in the live workload
# views, target resolution and the precompute store build. Heartbeats are written
# synchronously every 30 s, so four missed beats means the host (or its pod) is gone; a
# longer window keeps dead pods selectable and resolves stale pids. (The earlier 15-minute
# default only existed to hide the refresh lag of the buffered writer, which is gone.)
# Keep deploy/periodic_tasks/refresh_workload_snapshot.sh in step with this default.
WORKLOAD_FRESH_INTERVAL = _validated_sql_interval(
    os.getenv("GPROFILER_WORKLOAD_FRESH_INTERVAL", "2 minutes"), "2 minutes"
)
