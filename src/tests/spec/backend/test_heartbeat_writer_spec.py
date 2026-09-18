#!/usr/bin/env python3
"""
Spec tests for the async heartbeat writer in ``gprofiler_dev.heartbeat_writer``.

The writer buffers heartbeat host/inventory writes off the request thread and a
background thread flushes them in coalesced batches. This locks down:

* coalescing: repeated beats for the same (hostname, service_name) collapse to the
  latest payload, and each flush becomes one batch write
* bounded buffer: new hosts are dropped when the buffer is full (a dropped host
  re-sends next beat), but updates to already-buffered hosts are always accepted
* flush drains and clears the buffer; a failing DB drops the batch without raising
  or leaving the buffer stuck

Loaded directly by path (like the updater-queue spec) so the ``gprofiler_dev``
package __init__ (boto3/DAL) is not imported. The writer module is pure stdlib at
import time (config is imported lazily only in the factory).

    cd src && python -m pytest tests/spec/backend/test_heartbeat_writer_spec.py -v
"""

import importlib.util
from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parents[3]
_HW_PATH = _SRC_ROOT / "gprofiler-dev" / "gprofiler_dev" / "heartbeat_writer.py"

_spec = importlib.util.spec_from_file_location("heartbeat_writer_under_test", _HW_PATH)
heartbeat_writer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(heartbeat_writer)


class _StubDB:
    def __init__(self, fail=False):
        self.batches = []
        self.fail = fail

    def bulk_upsert_host_heartbeats(self, payloads):
        if self.fail:
            raise RuntimeError("db down")
        self.batches.append(list(payloads))
        return len(payloads)


def _payload(host, service="svc", **extra):
    p = {"hostname": host, "service_name": service, "ip_address": "1.2.3.4"}
    p.update(extra)
    return p


def _writer(db, max_hosts=1000):
    # Large flush interval so the background daemon never fires during the test; we
    # drive flush_once() explicitly.
    return heartbeat_writer.HeartbeatWriter(db, flush_interval=3600, max_hosts=max_hosts)


class TestCoalescing:
    def test_same_host_collapses_to_latest(self):
        db = _StubDB()
        w = _writer(db)
        w.submit(_payload("h1", agent_version="1.0"))
        w.submit(_payload("h1", agent_version="2.0"))
        written = w.flush_once()
        assert written == 1
        assert len(db.batches) == 1
        assert len(db.batches[0]) == 1
        assert db.batches[0][0]["agent_version"] == "2.0"

    def test_distinct_hosts_all_written(self):
        db = _StubDB()
        w = _writer(db)
        w.submit(_payload("h1"))
        w.submit(_payload("h2"))
        w.submit(_payload("h1", service="other"))  # different service => different key
        written = w.flush_once()
        assert written == 3
        keys = {(p["hostname"], p["service_name"]) for p in db.batches[0]}
        assert keys == {("h1", "svc"), ("h2", "svc"), ("h1", "other")}


class TestBounding:
    def test_new_hosts_dropped_when_full(self):
        db = _StubDB()
        w = _writer(db, max_hosts=2)
        w.submit(_payload("h1"))
        w.submit(_payload("h2"))
        w.submit(_payload("h3"))  # buffer full -> dropped
        written = w.flush_once()
        assert written == 2
        hosts = {p["hostname"] for p in db.batches[0]}
        assert hosts == {"h1", "h2"}

    def test_update_to_buffered_host_accepted_when_full(self):
        db = _StubDB()
        w = _writer(db, max_hosts=2)
        w.submit(_payload("h1", agent_version="1.0"))
        w.submit(_payload("h2"))
        w.submit(_payload("h1", agent_version="9.0"))  # update existing key, not a new host
        w.flush_once()
        h1 = next(p for p in db.batches[0] if p["hostname"] == "h1")
        assert h1["agent_version"] == "9.0"


class TestFlush:
    def test_flush_clears_buffer(self):
        db = _StubDB()
        w = _writer(db)
        w.submit(_payload("h1"))
        assert w.flush_once() == 1
        assert w.flush_once() == 0  # nothing left
        assert len(db.batches) == 1

    def test_empty_flush_is_noop(self):
        db = _StubDB()
        w = _writer(db)
        assert w.flush_once() == 0
        assert db.batches == []

    def test_failing_db_drops_batch_without_raising(self):
        db = _StubDB(fail=True)
        w = _writer(db)
        w.submit(_payload("h1"))
        # Must not raise; the batch is dropped and the buffer is left empty so the
        # writer thread keeps running and the host re-sends next beat.
        assert w.flush_once() == 0
        assert w.flush_once() == 0
