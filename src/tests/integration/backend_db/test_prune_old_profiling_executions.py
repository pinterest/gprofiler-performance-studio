#!/usr/bin/env python3
"""
Integration tests for the ``prune_old_profiling_executions`` procedure
(scripts/setup/postgres/migrations/add_prune_stale_data.sql).

Each test runs the procedure against a private copy of ``ProfilingExecutions`` and
``ProfilingCommands``, placed first on the search_path, holding only the rows the test
creates. Needs only Postgres with the schema loaded (no backend):

    cd src && python -m pytest tests/integration/backend_db/test_prune_old_profiling_executions.py -v
"""

import uuid
from typing import Optional, Set

import pytest


@pytest.fixture
def executions_db(postgres_connection):
    schema = f"prune_exec_test_{uuid.uuid4().hex[:12]}"
    with postgres_connection.cursor() as cur:
        cur.execute(f"CREATE SCHEMA {schema}")
        cur.execute(f"CREATE TABLE {schema}.ProfilingExecutions (LIKE public.ProfilingExecutions INCLUDING ALL)")
        cur.execute(f"CREATE TABLE {schema}.ProfilingCommands (LIKE public.ProfilingCommands INCLUDING ALL)")
        cur.execute(f"SET search_path = {schema}, public")
    try:
        yield postgres_connection
    finally:
        with postgres_connection.cursor() as cur:
            cur.execute("RESET search_path")
            cur.execute(f"DROP SCHEMA {schema} CASCADE")


def _add_execution(
    conn,
    hostname: str,
    created_ago: str,
    started_ago: Optional[str] = None,
    completed_ago: Optional[str] = None,
    command_id: Optional[str] = None,
) -> str:
    """An execution on hostname of command_id (a new command if None); returns the command id."""
    command_id = command_id or str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ProfilingExecutions (command_id, hostname, profiling_request_id, status, created_at, "
            "started_at, completed_at) VALUES (%s, %s, %s, 'completed', now() - %s::interval, "
            "now() - %s::interval, now() - %s::interval)",
            (command_id, hostname, str(uuid.uuid4()), created_ago, started_ago, completed_ago),
        )
    return command_id


def _make_current(conn, hostname: str, command_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ProfilingCommands (command_id, hostname, service_name, command_type, request_ids) "
            "VALUES (%s, %s, 'svc', 'start', ARRAY[%s::uuid])",
            (command_id, hostname, str(uuid.uuid4())),
        )


def _prune(conn, older_than: str = "30 days", **kwargs: int) -> None:
    args = ", ".join(f"{name} => {int(value)}" for name, value in kwargs.items())
    with conn.cursor() as cur:
        cur.execute(f"CALL prune_old_profiling_executions(%s::interval{', ' + args if args else ''})", (older_than,))


def _executions(conn) -> Set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT hostname FROM ProfilingExecutions")
        return {hostname for (hostname,) in cur.fetchall()}


def test_prunes_executions_untouched_for_older_than_except_those_of_current_commands(executions_db):
    """Age is the latest of created, re-assigned (started_at) and completed; a host's current command keeps its
    execution, but only on that host."""
    _add_execution(executions_db, "recent", created_ago="1 day")
    _add_execution(executions_db, "old", created_ago="40 days", started_ago="40 days", completed_ago="40 days")
    current = _add_execution(executions_db, "old-current", created_ago="40 days", started_ago="40 days")
    _make_current(executions_db, "old-current", current)
    _add_execution(executions_db, "old-same-command-not-current-here", created_ago="40 days", command_id=current)
    _add_execution(executions_db, "old-reassigned-recently", created_ago="40 days", started_ago="2 days")
    _add_execution(
        executions_db, "old-completed-recently", created_ago="40 days", started_ago="40 days", completed_ago="2 days"
    )

    _prune(executions_db)

    assert _executions(executions_db) == {
        "recent",
        "old-current",
        "old-reassigned-recently",
        "old-completed-recently",
    }


def test_caps_executions_per_call_and_drains_the_rest_on_the_next_call(executions_db):
    _add_execution(executions_db, "recent", created_ago="1 day")
    for name in ("old-1", "old-2", "old-3"):
        _add_execution(executions_db, name, created_ago="40 days")

    _prune(executions_db, batch_rows=2, max_batches=1)
    assert len(_executions(executions_db)) == 2

    _prune(executions_db, batch_rows=2, max_batches=1)
    assert _executions(executions_db) == {"recent"}
