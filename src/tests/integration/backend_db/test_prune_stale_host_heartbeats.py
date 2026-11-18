#!/usr/bin/env python3
"""
Integration tests for the ``prune_stale_host_heartbeats`` procedure
(scripts/setup/postgres/migrations/add_prune_stale_data.sql).

The procedure measures staleness against the newest beat in ``HostHeartbeats``, so each
test runs it against a private copy of ``HostHeartbeats`` and ``ProfilingCommands``, placed
first on the search_path, holding only the rows the test creates. Needs only Postgres with
the schema loaded (no backend):

    cd src && python -m pytest tests/integration/backend_db/test_prune_stale_host_heartbeats.py -v
"""

import uuid
from typing import Optional, Set

import pytest


@pytest.fixture
def hosts_db(postgres_connection):
    schema = f"prune_hosts_test_{uuid.uuid4().hex[:12]}"
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


def _add_host(conn, hostname: str, last_beat_ago: str, command_service: Optional[str] = None) -> None:
    """A host of service 'svc', plus a profiling command for its hostname under command_service if given."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO HostHeartbeats (hostname, ip_address, service_name, updated_at) "
            "VALUES (%s, '10.0.0.1', 'svc', now() - %s::interval)",
            (hostname, last_beat_ago),
        )
        if command_service is not None:
            cur.execute(
                "INSERT INTO ProfilingCommands (command_id, hostname, service_name, command_type, request_ids) "
                "VALUES (%s, %s, %s, 'start', ARRAY[%s::uuid])",
                (str(uuid.uuid4()), hostname, command_service, str(uuid.uuid4())),
            )


def _prune(conn, stale_after: str = "1 day", **kwargs: int) -> None:
    args = ", ".join(f"{name} => {int(value)}" for name, value in kwargs.items())
    with conn.cursor() as cur:
        cur.execute(f"CALL prune_stale_host_heartbeats(%s::interval{', ' + args if args else ''})", (stale_after,))


def _hosts(conn) -> Set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT hostname FROM HostHeartbeats")
        return {hostname for (hostname,) in cur.fetchall()}


def test_prunes_only_hosts_without_a_beat_for_stale_after_and_without_a_command(hosts_db):
    """A host row goes once its host is idle past the window, unless that host still has a command."""
    _add_host(hosts_db, "beating", "0 minutes")
    _add_host(hosts_db, "quiet", "20 hours")
    _add_host(hosts_db, "gone", "3 days")
    _add_host(hosts_db, "gone-with-command", "3 days", command_service="svc")
    _add_host(hosts_db, "gone-command-under-other-service", "3 days", command_service="other-svc")

    _prune(hosts_db)

    assert _hosts(hosts_db) == {"beating", "quiet", "gone-with-command"}


def test_staleness_is_measured_from_the_newest_beat(hosts_db):
    """When beats stop everywhere (ingest outage) only hosts already stale before the last beat are pruned."""
    _add_host(hosts_db, "last-to-beat", "3 days")
    _add_host(hosts_db, "shortly-before", "3 days 12 hours")
    _add_host(hosts_db, "long-gone", "5 days")

    _prune(hosts_db)

    assert _hosts(hosts_db) == {"last-to-beat", "shortly-before"}


def test_caps_hosts_per_call_and_drains_the_rest_on_the_next_call(hosts_db):
    _add_host(hosts_db, "beating", "0 minutes")
    for name in ("gone-1", "gone-2", "gone-3"):
        _add_host(hosts_db, name, "2 days")

    _prune(hosts_db, batch_rows=2, max_batches=1)
    assert len(_hosts(hosts_db)) == 2

    _prune(hosts_db, batch_rows=2, max_batches=1)
    assert _hosts(hosts_db) == {"beating"}
