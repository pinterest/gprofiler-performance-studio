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
POSTGRES_CONN_PER_THREAD = os.getenv("GPROFILER_POSTGRES_CONN_PER_THREAD", "FALSE").upper() == "TRUE"
# Raised from 3s: a 3s connect timeout trips on a brief latency blip and kicks off a reconnect
# wave (the storm). 10s tolerates a transient hiccup while still failing well under the gunicorn
# worker timeout (300s). Pair with the bounded backoff/retry below and hot-path fail-fast rather
# than relying on this value alone.
PG_CONNECT_TIMEOUT = int(os.getenv("GPROFILER_POSTGRES_CONNECT_TIMEOUT", 10))
# Retry/backoff for PostgresDB.execute() on transient connection errors.
# During a connection storm, a fixed sleep + reconnect-per-retry across every thread
# reconnects in lockstep and amplifies the storm. We cap the attempts and use bounded
# exponential backoff with jitter so retry waves de-synchronize instead of piling up.
PG_MAX_RETRIES = int(os.getenv("GPROFILER_POSTGRES_MAX_RETRIES", 3))
# Base backoff in seconds; wait ~= min(base * 2**attempt, cap) + random(0, jitter).
PG_RETRY_BACKOFF_BASE = float(os.getenv("GPROFILER_POSTGRES_RETRY_BACKOFF_BASE", "0.2"))
PG_RETRY_BACKOFF_CAP = float(os.getenv("GPROFILER_POSTGRES_RETRY_BACKOFF_CAP", "5.0"))
PG_RETRY_BACKOFF_JITTER = float(os.getenv("GPROFILER_POSTGRES_RETRY_BACKOFF_JITTER", "0.5"))
# Bounded per-process connection pool: max live connections and how long a caller waits
# for a free one before erroring. Total DB connections ~= replicas * workers * pool size.
POSTGRES_POOL_SIZE = int(os.getenv("GPROFILER_POSTGRES_POOL_SIZE", 10))
POSTGRES_POOL_ACQUIRE_TIMEOUT = int(os.getenv("GPROFILER_POSTGRES_POOL_ACQUIRE_TIMEOUT", 10))

# Async heartbeat writes: buffer the host + inventory writes off the request thread and flush
# them in coalesced batches, so a heartbeat POST returns after only the (fast) command read.
HEARTBEAT_ASYNC_WRITES = os.getenv("GPROFILER_HEARTBEAT_ASYNC_WRITES", "TRUE").upper() == "TRUE"
HEARTBEAT_FLUSH_INTERVAL_SEC = float(os.getenv("GPROFILER_HEARTBEAT_FLUSH_INTERVAL_SEC", "1.0"))
HEARTBEAT_BUFFER_MAX_HOSTS = int(os.getenv("GPROFILER_HEARTBEAT_BUFFER_MAX_HOSTS", "20000"))

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
# views and the precompute store build. Loosened from 2m: at full-fleet scale the async writer
# spreads each host's timestamp refresh over ~15m, so a tighter window undercounts the fleet.
WORKLOAD_FRESH_INTERVAL = _validated_sql_interval(
    os.getenv("GPROFILER_WORKLOAD_FRESH_INTERVAL", "15 minutes"), "15 minutes"
)
