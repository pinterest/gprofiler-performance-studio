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

from __future__ import annotations

import threading
import time
from logging import getLogger
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

if TYPE_CHECKING:
    from gprofiler_dev.postgres.db_manager import DBManager

logger = getLogger(__name__)


class HeartbeatWriter:
    """Buffer heartbeat host/inventory writes off the request thread and flush them in batches.

    The buffer is keyed by (hostname, service_name) and keeps only the LATEST payload per host,
    so repeated beats within a flush window collapse to one write and the working set is bounded
    to the number of active hosts hitting this process. A single daemon thread flushes the batch
    in one transaction, turning ~N per-request write transactions into one commit per interval.

    Dropping on a full buffer is safe: heartbeats are periodic, so a dropped host simply re-sends
    on its next beat. Command delivery is unaffected because it runs synchronously on the request
    path, not through this writer.
    """

    def __init__(self, db: "DBManager", flush_interval: float, max_hosts: int) -> None:
        self._db = db
        self._flush_interval = max(0.1, flush_interval)
        self._max_hosts = max(1, max_hosts)
        self._lock = threading.Lock()
        self._buffer: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._dropped = 0
        self._flush_thread = threading.Thread(target=self._run, name="heartbeat-writer", daemon=True)
        self._flush_thread.start()

    def submit(self, payload: Dict[str, Any]) -> None:
        """Enqueue a heartbeat payload for async write (latest-wins per host)."""
        key = (payload["hostname"], payload["service_name"])
        with self._lock:
            # Always accept an update to a host already buffered; only bound the count of
            # distinct new hosts so the buffer can't grow without limit under a slow DB.
            if key in self._buffer or len(self._buffer) < self._max_hosts:
                self._buffer[key] = payload
            else:
                self._dropped += 1
                if self._dropped % 1000 == 1:
                    logger.warning(
                        "heartbeat buffer full (max_hosts=%s), dropping host writes (total dropped=%s)",
                        self._max_hosts,
                        self._dropped,
                    )

    def flush_once(self) -> int:
        """Drain the current buffer and write it in one batch. Returns hosts written."""
        with self._lock:
            if not self._buffer:
                return 0
            batch = list(self._buffer.values())
            self._buffer = {}
        started = time.time()
        try:
            self._db.bulk_upsert_host_heartbeats(batch)
        except Exception:
            # Drop the batch (hosts re-send next beat); the next flush is the natural retry.
            logger.exception("heartbeat flush failed for %s hosts, dropping batch", len(batch))
            return 0
        logger.debug("heartbeat flush: %s hosts in %.3fs", len(batch), time.time() - started)
        return len(batch)

    def _run(self) -> None:
        while True:
            time.sleep(self._flush_interval)
            try:
                self.flush_once()
            except Exception:
                logger.exception("heartbeat writer flush loop error")


_writer: Optional[HeartbeatWriter] = None
_writer_lock = threading.Lock()


def get_heartbeat_writer(db: "DBManager") -> HeartbeatWriter:
    """Process-wide singleton heartbeat writer (starts the flush thread on first call)."""
    global _writer
    if _writer is None:
        with _writer_lock:
            if _writer is None:
                from gprofiler_dev import config

                _writer = HeartbeatWriter(
                    db,
                    flush_interval=config.HEARTBEAT_FLUSH_INTERVAL_SEC,
                    max_hosts=config.HEARTBEAT_BUFFER_MAX_HOSTS,
                )
    return _writer
