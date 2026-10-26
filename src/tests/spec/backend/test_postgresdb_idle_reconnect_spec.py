#!/usr/bin/env python3
"""
Spec tests for ``PostgresDB``'s proactive idle reconnect in
``gprofiler_dev.postgres.postgresdb``.

The database closes sessions idle longer than ``idle_session_timeout`` (5 min on our
roles). psycopg2 only notices on the next round trip, so before this change the first
statement on an idle connection failed, slept a backoff, logged ``ERROR Trying to
reconnect`` and reconnected -- and in ``transaction()`` (no retry) the whole
transaction failed. This locks down:

* a connection unused for longer than ``PG_IDLE_RECONNECT_SEC`` is replaced *before*
  use, in both ``execute()`` and ``transaction()``, with no error log and no backoff
* a connection used within the threshold is kept; ``_last_used`` advances on each use
* ``PG_IDLE_RECONNECT_SEC <= 0`` disables the refresh and the old retry path still
  recovers (now naming the cause in the error line)

Loaded directly by path with stub ``psycopg2`` / ``gprofiler_dev.config`` modules so
no database and no boto3 are needed.

    cd src && python -m pytest tests/spec/backend/test_postgresdb_idle_reconnect_spec.py -v
"""

import importlib.util
import logging
import sys
import types
from pathlib import Path

import pytest

_SRC_ROOT = Path(__file__).resolve().parents[3]
_PG_PATH = _SRC_ROOT / "gprofiler-dev" / "gprofiler_dev" / "postgres" / "postgresdb.py"


class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, args=None):
        self._conn.statements.append(sql)
        if self._conn.server_closed:
            raise self._conn.psycopg2.OperationalError("SSL connection has been closed unexpectedly")

    def fetchone(self):
        return (1,)

    def fetchall(self):
        return [(1,)]

    def close(self):
        pass


class _FakeConn:
    def __init__(self, psycopg2_mod):
        self.psycopg2 = psycopg2_mod
        self.closed = 0  # what psycopg2 reports: only closes it has observed
        self.server_closed = False  # the server dropped us for idleness; psycopg2 does not know yet
        self.statements = []
        self.commits = 0
        self.closed_by_app = False

    def cursor(self, cursor_factory=None):
        return _FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        self.closed_by_app = True
        self.closed = 1


def _load(monkeypatch, idle_reconnect_sec=240.0):
    psycopg2 = types.ModuleType("psycopg2")
    psycopg2.OperationalError = type("OperationalError", (Exception,), {})
    psycopg2.InterfaceError = type("InterfaceError", (Exception,), {})
    connections = []

    def connect(**_kwargs):
        conn = _FakeConn(psycopg2)
        connections.append(conn)
        return conn

    psycopg2.connect = connect
    extras = types.ModuleType("psycopg2.extras")
    extras.RealDictCursor = object
    extras.execute_values = lambda cur, sql, args: cur.execute(sql, args)
    psycopg2.extras = extras
    psycopg2.pool = types.ModuleType("psycopg2.pool")

    config = types.ModuleType("gprofiler_dev.config")
    config.PG_DB_NAME = "db"
    config.PG_USER = "u"
    config.PG_HOST = "h"
    config.PG_PORT = 5432
    config.PG_PASSWORD = "p"
    config.PG_CONNECT_TIMEOUT = 1
    config.PG_MAX_RETRIES = 3
    config.PG_RETRY_BACKOFF_BASE = 0.0
    config.PG_RETRY_BACKOFF_CAP = 0.0
    config.PG_RETRY_BACKOFF_JITTER = 0.0
    config.PG_IDLE_RECONNECT_SEC = idle_reconnect_sec
    gprofiler_dev = types.ModuleType("gprofiler_dev")
    gprofiler_dev.config = config

    for name, mod in (
        ("psycopg2", psycopg2),
        ("psycopg2.extras", extras),
        ("psycopg2.pool", psycopg2.pool),
        ("gprofiler_dev", gprofiler_dev),
        ("gprofiler_dev.config", config),
    ):
        monkeypatch.setitem(sys.modules, name, mod)

    spec = importlib.util.spec_from_file_location("postgresdb_under_test", _PG_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    clock = {"now": 1000.0}
    monkeypatch.setattr(module.PostgresDB, "_now", staticmethod(lambda: clock["now"]))
    sleeps = []
    monkeypatch.setattr(module.time, "sleep", lambda s: sleeps.append(s))
    return module, connections, clock, sleeps


def _errors(caplog):
    return [r for r in caplog.records if r.levelno >= logging.ERROR]


class TestExecute:
    def test_connection_used_within_threshold_is_kept(self, monkeypatch):
        module, conns, clock, _ = _load(monkeypatch)
        db = module.PostgresDB()
        db.execute("SELECT 1")
        clock["now"] += 239
        db.execute("SELECT 1")
        assert len(conns) == 1
        assert conns[0].commits == 2

    def test_idle_connection_is_replaced_before_use_quietly(self, monkeypatch, caplog):
        module, conns, clock, sleeps = _load(monkeypatch)
        db = module.PostgresDB()
        db.execute("SELECT 1")
        # Server drops the idle session; psycopg2 has not noticed (closed == 0).
        conns[0].server_closed = True
        clock["now"] += 241
        with caplog.at_level(logging.DEBUG):
            assert db.execute("SELECT 1") == 1
        assert len(conns) == 2, "a fresh connection was opened before the statement ran"
        assert conns[0].closed_by_app, "the stale socket is closed, not left for the GC"
        assert conns[1].statements == ["SELECT 1"] and conns[1].commits == 1
        assert not conns[0].statements[1:], "nothing was sent on the dead connection"
        assert sleeps == [], "no retry backoff on the request thread"
        assert _errors(caplog) == []
        assert "Trying to reconnect" not in caplog.text
        assert "reconnecting before use" in caplog.text

    def test_last_used_advances_with_each_use(self, monkeypatch):
        module, conns, clock, _ = _load(monkeypatch)
        db = module.PostgresDB()
        clock["now"] += 200
        db.execute("SELECT 1")  # resets the idle clock
        clock["now"] += 230  # 430 s since connect, 230 s since last use
        db.execute("SELECT 1")
        assert len(conns) == 1
        clock["now"] += 241
        db.execute("SELECT 1")
        assert len(conns) == 2

    def test_disabled_falls_back_to_retry_path_and_names_the_cause(self, monkeypatch, caplog):
        module, conns, clock, sleeps = _load(monkeypatch, idle_reconnect_sec=0)
        db = module.PostgresDB()
        db.execute("SELECT 1")
        conns[0].server_closed = True
        clock["now"] += 3600
        with caplog.at_level(logging.DEBUG):
            assert db.execute("SELECT 1") == 1
        assert len(conns) == 2
        assert len(sleeps) == 1, "the old path pays a backoff"
        errors = _errors(caplog)
        assert len(errors) == 1
        assert (
            "Trying to reconnect #0 after OperationalError: SSL connection has been closed unexpectedly"
            in errors[0].getMessage()
        )


class TestTransaction:
    def test_idle_connection_is_replaced_before_the_transaction(self, monkeypatch, caplog):
        module, conns, clock, sleeps = _load(monkeypatch)
        db = module.PostgresDB()
        with db.transaction() as cur:
            cur.execute("INSERT 1")
        conns[0].server_closed = True
        clock["now"] += 241
        with caplog.at_level(logging.DEBUG):
            with db.transaction() as cur:
                cur.execute("INSERT 2")
        assert len(conns) == 2
        assert conns[1].statements == ["INSERT 2"] and conns[1].commits == 1
        assert conns[0].statements == ["INSERT 1"], "nothing was sent on the dead connection"
        assert _errors(caplog) == []
        assert sleeps == []

    def test_transaction_use_resets_the_idle_clock_for_execute(self, monkeypatch):
        module, conns, clock, _ = _load(monkeypatch)
        db = module.PostgresDB()
        clock["now"] += 200
        with db.transaction() as cur:
            cur.execute("INSERT 1")
        clock["now"] += 200  # 400 s since connect, 200 s since the transaction committed
        db.execute("SELECT 1")
        assert len(conns) == 1

    def test_without_refresh_a_dead_connection_fails_the_transaction(self, monkeypatch):
        # Documents the failure mode the refresh exists for: transaction() has no retry.
        module, conns, clock, _ = _load(monkeypatch, idle_reconnect_sec=0)
        db = module.PostgresDB()
        conns[0].server_closed = True
        clock["now"] += 3600
        with pytest.raises(module.psycopg2.OperationalError):
            with db.transaction() as cur:
                cur.execute("INSERT 1")
