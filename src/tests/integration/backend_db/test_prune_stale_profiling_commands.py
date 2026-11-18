#!/usr/bin/env python3
"""
Integration tests for the ``prune_stale_profiling_commands`` procedure
(scripts/setup/postgres/migrations/add_prune_stale_data.sql).

The procedure measures staleness against the newest beat in ``HostHeartbeats``, so each
test runs it against a private copy of ``HostHeartbeats`` and ``ProfilingCommands``, placed
first on the search_path, holding only the rows the test creates. Needs only Postgres with
the schema loaded (no backend):

    cd src && python -m pytest tests/integration/backend_db/test_prune_stale_profiling_commands.py -v
"""

import uuid
from typing import Optional, Set

import pytest


@pytest.fixture
def commands_db(postgres_connection):
    schema = f"prune_cmd_test_{uuid.uuid4().hex[:12]}"
    with postgres_connection.cursor() as cur:
        cur.execute(f"CREATE SCHEMA {schema}")
        cur.execute(f"CREATE TABLE {schema}.HostHeartbeats (LIKE public.HostHeartbeats INCLUDING ALL)")
        cur.execute(f"CREATE TABLE {schema}.ProfilingCommands (LIKE public.ProfilingCommands INCLUDING ALL)")
        cur.execute(f"SET search_path = {schema}, public")
    try:
        yield postgres_connection
    finally:
        with postgres_connection.cursor() as cur:
            cur.execute("RESET search_path")
            cur.execute(f"DROP SCHEMA {schema} CASCADE")


def _add(conn, hostname: str, last_beat_ago: Optional[str], issued_ago: str) -> None:
    """A host (no HostHeartbeats row when last_beat_ago is None) and its current command."""
    with conn.cursor() as cur:
        if last_beat_ago is not None:
            cur.execute(
                "INSERT INTO HostHeartbeats (hostname, ip_address, service_name, updated_at) "
                "VALUES (%s, '10.0.0.1', 'svc', now() - %s::interval)",
                (hostname, last_beat_ago),
            )
        cur.execute(
            "INSERT INTO ProfilingCommands (command_id, hostname, service_name, command_type, request_ids, status, "
            "created_at) VALUES (%s, %s, 'svc', 'start', ARRAY[%s::uuid], 'completed', now() - %s::interval)",
            (str(uuid.uuid4()), hostname, str(uuid.uuid4()), issued_ago),
        )


def _prune(conn, stale_after: str = "7 days", **kwargs: int) -> None:
    args = ", ".join(f"{name} => {int(value)}" for name, value in kwargs.items())
    with conn.cursor() as cur:
        cur.execute(f"CALL prune_stale_profiling_commands(%s::interval{', ' + args if args else ''})", (stale_after,))


def _commands(conn) -> Set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT hostname FROM ProfilingCommands")
        return {hostname for (hostname,) in cur.fetchall()}


def test_prunes_only_old_commands_of_hosts_without_a_beat_for_stale_after(commands_db):
    """A command goes once its host is idle past the window and it was not issued within it."""
    _add(commands_db, "beating", "0 minutes", issued_ago="30 days")
    _add(commands_db, "quiet", "3 days", issued_ago="10 days")
    _add(commands_db, "gone", "10 days", issued_ago="10 days")
    _add(commands_db, "gone-reissued", "10 days", issued_ago="1 day")
    _add(commands_db, "never-beat", None, issued_ago="10 days")
    _add(commands_db, "not-registered-yet", None, issued_ago="1 hour")

    _prune(commands_db)

    assert _commands(commands_db) == {"beating", "quiet", "gone-reissued", "not-registered-yet"}


def test_staleness_is_measured_from_the_newest_beat(commands_db):
    """When beats stop everywhere (ingest outage) only commands already stale before the last beat are pruned."""
    _add(commands_db, "last-to-beat", "10 days", issued_ago="10 days")
    _add(commands_db, "shortly-before", "12 days", issued_ago="12 days")
    _add(commands_db, "long-gone", "20 days", issued_ago="20 days")

    _prune(commands_db)

    assert _commands(commands_db) == {"last-to-beat", "shortly-before"}


def test_caps_commands_per_call_and_drains_the_rest_on_the_next_call(commands_db):
    _add(commands_db, "beating", "0 minutes", issued_ago="0 minutes")
    for name in ("gone-1", "gone-2", "gone-3"):
        _add(commands_db, name, "10 days", issued_ago="10 days")

    _prune(commands_db, batch_rows=2, max_batches=1)
    assert len(_commands(commands_db)) == 2

    _prune(commands_db, batch_rows=2, max_batches=1)
    assert _commands(commands_db) == {"beating"}
