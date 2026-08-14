#!/usr/bin/env python3
"""
Spec tests for the bounded metadata/token updater queues in
``gprofiler_dev.profiles_utils``.

The processes and tokens queues are fed by the profile-upload request path and
drained by background updater threads that batch DB writes. Before they were
bounded, a slow or failing DB stalled the updater threads while uploads kept
producing entries, growing the queues (and the API process memory) without
limit. This suite locks down the bounded behavior:

* both queues are created with a finite maxsize
* ``put_nowait_dropping`` never blocks: it drops (with a warning) when full

These tests import ``profiles_utils`` in-process (no server or database).
``profiles_utils`` is pure stdlib, so it is loaded directly by path to avoid
importing the ``gprofiler_dev`` package (whose __init__ pulls boto3 and the
rest of the DAL dependency stack).

Run just this file:

    cd src && python -m pytest tests/spec/backend/test_updater_queue_bounds_spec.py -v
"""

import importlib.util
import queue
from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parents[3]
_PU_PATH = _SRC_ROOT / "gprofiler-dev" / "gprofiler_dev" / "profiles_utils.py"

_spec = importlib.util.spec_from_file_location("profiles_utils_under_test", _PU_PATH)
profiles_utils = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(profiles_utils)


class _StubDB:
    def update_processes(self, processes):
        pass

    def update_tokens_last_seen(self, tokens):
        pass


class TestQueueBoundsSpec:
    def test_updater_queue_max_size_is_finite(self):
        assert profiles_utils.UPDATER_QUEUE_MAX_SIZE > 0

    def test_processes_queue_is_bounded(self):
        utils = profiles_utils.GprofilerMetadataUtils(_StubDB())
        assert utils.processes_queue.maxsize == profiles_utils.UPDATER_QUEUE_MAX_SIZE

    def test_tokens_queue_is_bounded(self):
        utils = profiles_utils.GprofilerUtils(_StubDB())
        assert utils.tokens_queue.maxsize == profiles_utils.UPDATER_QUEUE_MAX_SIZE


class TestPutNowaitDroppingSpec:
    def test_put_succeeds_when_not_full(self):
        q = queue.Queue(maxsize=2)
        profiles_utils.put_nowait_dropping(q, 1, "test")
        profiles_utils.put_nowait_dropping(q, 2, "test")
        assert q.qsize() == 2
        assert q.get_nowait() == 1

    def test_put_drops_without_blocking_when_full(self):
        q = queue.Queue(maxsize=1)
        profiles_utils.put_nowait_dropping(q, 1, "test")
        # Must return immediately (not block the upload request path) and
        # must not raise; the overflow item is dropped.
        profiles_utils.put_nowait_dropping(q, 2, "test")
        assert q.qsize() == 1
        assert q.get_nowait() == 1
