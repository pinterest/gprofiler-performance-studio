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
from typing import Optional

from gprofiler_dev import config
from gprofiler_dev.postgres.postgresdb import PostgresDB

_DB_INSTANCE: Optional[PostgresDB] = None
_DB_LOCK = threading.RLock()


def get_postgres_db() -> PostgresDB:
    # One bounded connection pool per process, shared across all worker threads.
    # (Per-thread connections were removed: unbounded, one per thread, they stormed
    # the DB on deploy.)
    global _DB_INSTANCE

    if _DB_INSTANCE is None:
        with _DB_LOCK:
            if _DB_INSTANCE is None:
                _DB_INSTANCE = PostgresDB()

    return _DB_INSTANCE


class ParameterIsMissing(Exception):
    pass
