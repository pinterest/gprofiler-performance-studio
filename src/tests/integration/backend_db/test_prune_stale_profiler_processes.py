#!/usr/bin/env python3
"""
Integration tests for the ``prune_stale_profiler_processes`` procedure
(scripts/setup/postgres/migrations/add_prune_stale_data.sql).

The procedure measures staleness against the newest ``last_seen`` in ``ProfilerProcesses``,
so each test runs it against a private copy of the table, placed first on the search_path,
holding only the rows the test creates. Needs only Postgres with the schema loaded (no
backend):

    cd src && python -m pytest tests/integration/backend_db/test_prune_stale_profiler_processes.py -v
"""

import itertools
import uuid
from typing import Set

import pytest

_pids = itertools.count(1)


@pytest.fixture
def processes_db(postgres_connection):
    schema = f"prune_proc_test_{uuid.uuid4().hex[:12]}"
    with postgres_connection.cursor() as cur:
        cur.execute(f"CREATE SCHEMA {schema}")
        cur.execute(f"CREATE TABLE {schema}.ProfilerProcesses (LIKE public.ProfilerProcesses INCLUDING ALL)")
        cur.execute(f"SET search_path = {schema}, public")
    try:
        yield postgres_connection
    finally:
        with postgres_connection.cursor() as cur:
            cur.execute("RESET search_path")
            cur.execute(f"DROP SCHEMA {schema} CASCADE")


def _add_process(conn, hostname: str, last_seen_ago: str, ran_for: str) -> None:
    """An agent process that registered (ts) ran_for before it was last seen."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ProfilerProcesses (instance_run, profiler_version, profiler_run_environment, pid, "
            "spawn_local_time, public_ip, private_ip, hostname, service, spawn_uptime, run_arguments, ts, last_seen) "
            "VALUES (1, 1, 1, %s, now(), '', '10.0.0.1', %s, 1, 0, '{}', "
            "now() - %s::interval - %s::interval, now() - %s::interval)",
            (next(_pids), hostname, last_seen_ago, ran_for, last_seen_ago),
        )


def _prune(conn, stale_after: str = "90 days", **kwargs: int) -> None:
    args = ", ".join(f"{name} => {int(value)}" for name, value in kwargs.items())
    with conn.cursor() as cur:
        cur.execute(f"CALL prune_stale_profiler_processes(%s::interval{', ' + args if args else ''})", (stale_after,))


def _processes(conn) -> Set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT hostname FROM ProfilerProcesses")
        return {hostname for (hostname,) in cur.fetchall()}


def test_prunes_processes_not_seen_for_stale_after_or_for_7_days_if_they_ran_under_2_minutes(processes_db):
    _add_process(processes_db, "running", last_seen_ago="0 minutes", ran_for="30 days")
    _add_process(processes_db, "ended-60-days-ago", last_seen_ago="60 days", ran_for="1 day")
    _add_process(processes_db, "ended-100-days-ago", last_seen_ago="100 days", ran_for="1 day")
    _add_process(processes_db, "1-minute-3-days-ago", last_seen_ago="3 days", ran_for="1 minute")
    _add_process(processes_db, "1-minute-10-days-ago", last_seen_ago="10 days", ran_for="1 minute")
    _add_process(processes_db, "3-minutes-10-days-ago", last_seen_ago="10 days", ran_for="3 minutes")

    _prune(processes_db)

    assert _processes(processes_db) == {
        "running",
        "ended-60-days-ago",
        "1-minute-3-days-ago",
        "3-minutes-10-days-ago",
    }


def test_staleness_is_measured_from_the_newest_upload(processes_db):
    """When uploads stop everywhere (ingest outage) only processes already stale before the last one are pruned."""
    _add_process(processes_db, "last-seen", last_seen_ago="100 days", ran_for="1 day")
    _add_process(processes_db, "50-days-before", last_seen_ago="150 days", ran_for="1 day")
    _add_process(processes_db, "100-days-before", last_seen_ago="200 days", ran_for="1 day")
    _add_process(processes_db, "1-minute-5-days-before", last_seen_ago="105 days", ran_for="1 minute")
    _add_process(processes_db, "1-minute-10-days-before", last_seen_ago="110 days", ran_for="1 minute")

    _prune(processes_db)

    assert _processes(processes_db) == {"last-seen", "50-days-before", "1-minute-5-days-before"}


def test_caps_processes_per_call_and_drains_the_rest_on_the_next_call(processes_db):
    _add_process(processes_db, "running", last_seen_ago="0 minutes", ran_for="1 day")
    for name in ("ended-1", "ended-2", "ended-3"):
        _add_process(processes_db, name, last_seen_ago="100 days", ran_for="1 day")

    _prune(processes_db, batch_rows=2, max_batches=1)
    assert len(_processes(processes_db)) == 2

    _prune(processes_db, batch_rows=2, max_batches=1)
    assert _processes(processes_db) == {"running"}
