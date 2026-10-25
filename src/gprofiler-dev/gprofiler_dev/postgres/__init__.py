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

import threading

from gprofiler_dev.postgres.postgresdb import PostgresDB

THREAD_LOCAL = threading.local()


def get_postgres_db() -> PostgresDB:
    """One Postgres connection per thread, created lazily and reused for the thread's life.

    A psycopg2 connection is not safe to share between threads, and the earlier
    process-wide singleton behind a lock serialized every DB call in the process. Per-thread
    connections mean the number of connections a process holds equals its thread count, so
    size the webapp's threadpool (GPROFILER_WEBAPP_THREAD_POOL_SIZE) with the database's
    connection limit in mind; nothing else needs tuning.
    """
    if getattr(THREAD_LOCAL, "postgres_conn", None) is None:
        THREAD_LOCAL.postgres_conn = PostgresDB()
    return THREAD_LOCAL.postgres_conn


class ParameterIsMissing(Exception):
    pass
