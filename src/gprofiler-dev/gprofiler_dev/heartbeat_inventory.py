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
"""Change detection for heartbeat workload inventory.

Every agent heartbeat (every 30 s per host) may carry the host's full container /
process inventory. Almost all of those payloads are identical to the previous beat,
yet without change detection each one costs a full diff against the inventory
tables -- work proportional to fleet size, not to how much actually changed.

This module provides the two pieces that make inventory work proportional to change:

* :func:`inventory_digest` -- a stable fingerprint (sha256 hex) of an inventory
  payload. The server stores the digest of the inventory it last synced on the host's
  ``HostHeartbeats.inventory_digest`` row; a beat whose digest equals the stored one
  needs no inventory work at all. The agent computes the same function so it can
  omit the inventory from the payload when it has not changed (it then sends only
  the digest).

* :class:`SyncBudget` -- a per-process token bucket bounding how many full inventory
  syncs this worker performs per second. When the whole fleet differs at once (first
  rollout of this feature, a restore from backup, a large reschedule wave) the sync
  load is paced instead of arriving as a herd: a host over budget still gets its
  freshness update, keeps its old/NULL digest, and simply syncs on a later beat.

The canonical form is deliberately simple and language-neutral so the agent (Python,
``gprofiler/metadata/heartbeat_metadata.py``) can implement it byte-for-byte:

    [ [container_id, container_name, runtime, namespace, pod_name, workload_name,
       workload_kind, [[pid, process_name], ...]], ... ]

sorted by container_id, processes sorted by pid, JSON-encoded with ``separators=(",", ":")``,
``ensure_ascii=False`` and ``sort_keys`` irrelevant (arrays only), UTF-8, sha256.
Entries the server would not store are excluded *before* hashing (containers without a
``container_id``, duplicate container ids, non-integer pids, duplicate pids), so the
digest describes exactly what ends up in the tables. Missing string fields and ``None``
are both encoded as JSON ``null``. ``process_name`` is normalized to ``None`` when empty
on both sides.

This module is pure stdlib at import time so specs can load it by path.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any, Iterable, List, Mapping, Optional

DIGEST_VERSION = "1"
"""Bump when the canonical form changes. A mismatch between agent and server versions
only costs efficiency (the server keeps asking for full inventory), never correctness,
because the server always re-derives the digest from the payload it actually syncs."""


def _opt_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    return text if text != "" else None


def canonical_inventory(containers: Iterable[Mapping[str, Any]]) -> List[List[Any]]:
    """Return the canonical (sorted, filtered, positional) form of an inventory payload.

    Mirrors the filtering the inventory sync applies, so hashing this is equivalent to
    hashing what the database ends up holding.
    """
    by_cid: dict = {}
    for container in containers or []:
        if not isinstance(container, Mapping):
            continue
        cid = _opt_str(container.get("container_id"))
        if cid is None or cid in by_cid:
            continue
        procs: dict = {}
        for process in container.get("processes") or []:
            if not isinstance(process, Mapping):
                continue
            raw_pid = process.get("pid")
            if raw_pid is None or not str(raw_pid).isdigit():
                continue
            pid = int(raw_pid)
            if pid in procs:
                continue
            procs[pid] = _opt_str(process.get("process_name"))
        by_cid[cid] = [
            cid,
            _opt_str(container.get("container_name")),
            _opt_str(container.get("runtime")),
            _opt_str(container.get("namespace")),
            _opt_str(container.get("pod_name")),
            _opt_str(container.get("workload_name")),
            _opt_str(container.get("workload_kind")),
            [[pid, procs[pid]] for pid in sorted(procs)],
        ]
    return [by_cid[cid] for cid in sorted(by_cid)]


def inventory_digest(containers: Optional[Iterable[Mapping[str, Any]]]) -> Optional[str]:
    """sha256 hex digest of the canonical inventory, or ``None`` if inventory was not reported.

    ``None`` (field absent) means "the agent did not send inventory this beat" and must
    not be confused with ``[]`` (the agent reports that it has no containers), which is
    a real, hashable inventory.
    """
    if containers is None:
        return None
    payload = json.dumps(canonical_inventory(containers), separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class SyncBudget:
    """Token bucket: at most ``rate`` full inventory syncs per second (per process).

    ``rate <= 0`` disables the budget (every sync is allowed). Capacity (burst) is one
    second's worth of tokens, minimum 1, so a quiet period cannot bank a large burst.
    """

    def __init__(self, rate: float, now: Optional[Any] = None) -> None:
        self._now = now or time.monotonic
        self.rate = float(rate)
        self._capacity = max(1.0, self.rate)
        self._tokens = self._capacity
        self._last = self._now()
        self._lock = threading.Lock()

    @property
    def unlimited(self) -> bool:
        return self.rate <= 0

    def _refill(self) -> None:
        now = self._now()
        elapsed = now - self._last
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self.rate)
            self._last = now

    def acquire_up_to(self, wanted: int) -> int:
        """Take up to ``wanted`` tokens; return how many were granted (0..wanted)."""
        if wanted <= 0:
            return 0
        if self.unlimited:
            return wanted
        with self._lock:
            self._refill()
            granted = min(wanted, int(self._tokens))
            self._tokens -= granted
            return granted

    def try_acquire(self) -> bool:
        return self.acquire_up_to(1) == 1


_budget: Optional[SyncBudget] = None
_budget_lock = threading.Lock()


def get_sync_budget() -> SyncBudget:
    """Process-wide budget sized from ``GPROFILER_INVENTORY_SYNC_PER_SEC`` (lazy, so this
    module stays importable without the config package)."""
    global _budget
    if _budget is None:
        with _budget_lock:
            if _budget is None:
                from gprofiler_dev import config

                _budget = SyncBudget(config.INVENTORY_SYNC_PER_SEC)
    return _budget
