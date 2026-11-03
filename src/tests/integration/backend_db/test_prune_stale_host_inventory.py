#!/usr/bin/env python3
"""
Integration tests for the ``prune_stale_host_inventory`` procedure
(scripts/setup/postgres/migrations/add_prune_stale_host_inventory.sql).

The procedure measures staleness against the newest beat in ``HostHeartbeats``, so each
test runs it against a private copy of the three inventory tables, placed first on the
search_path, holding only the hosts the test creates. Needs only Postgres with the
schema loaded (no backend):

    cd src && python -m pytest tests/integration/backend_db/test_prune_stale_host_inventory.py -v
"""

import uuid
from collections import Counter
from typing import Dict, Optional, Tuple

import pytest

Inventory = Tuple[Optional[str], int, int]  # (inventory_digest, containers, processes)


@pytest.fixture
def inventory_db(postgres_connection):
    schema = f"prune_test_{uuid.uuid4().hex[:12]}"
    with postgres_connection.cursor() as cur:
        cur.execute(f"CREATE SCHEMA {schema}")
        cur.execute(f"CREATE TABLE {schema}.HostHeartbeats (LIKE public.HostHeartbeats INCLUDING ALL)")
        cur.execute(
            f"CREATE TABLE {schema}.HeartbeatContainers (LIKE public.HeartbeatContainers INCLUDING ALL, "
            f"FOREIGN KEY (host_id) REFERENCES {schema}.HostHeartbeats (id) ON DELETE CASCADE)"
        )
        cur.execute(
            f"CREATE TABLE {schema}.HeartbeatProcesses (LIKE public.HeartbeatProcesses INCLUDING ALL, "
            f"FOREIGN KEY (container_row_id) REFERENCES {schema}.HeartbeatContainers (id) ON DELETE CASCADE)"
        )
        cur.execute(f"SET search_path = {schema}, public")
    try:
        yield postgres_connection
    finally:
        with postgres_connection.cursor() as cur:
            cur.execute("RESET search_path")
            cur.execute(f"DROP SCHEMA {schema} CASCADE")


def _add_host(conn, hostname: str, last_beat_ago: str, containers: int = 2, processes: int = 3) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO HostHeartbeats (hostname, ip_address, service_name, updated_at, inventory_digest) "
            "VALUES (%s, '10.0.0.1', 'svc', now() - %s::interval, 'digest') RETURNING id",
            (hostname, last_beat_ago),
        )
        host_id = cur.fetchone()[0]
        for c in range(containers):
            cur.execute(
                "INSERT INTO HeartbeatContainers (host_id, container_id) VALUES (%s, %s) RETURNING id",
                (host_id, f"{hostname}-{c}"),
            )
            container_row_id = cur.fetchone()[0]
            for pid in range(1, processes + 1):
                cur.execute(
                    "INSERT INTO HeartbeatProcesses (container_row_id, pid) VALUES (%s, %s)",
                    (container_row_id, pid),
                )


def _prune(conn, stale_after: str = "1 hour", **kwargs: int) -> None:
    args = ", ".join(f"{name} => {int(value)}" for name, value in kwargs.items())
    with conn.cursor() as cur:
        cur.execute(f"CALL prune_stale_host_inventory(%s::interval{', ' + args if args else ''})", (stale_after,))


def _inventory(conn) -> Dict[str, Inventory]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT h.hostname, h.inventory_digest, count(DISTINCT c.id), count(p.id)
            FROM HostHeartbeats h
            LEFT JOIN HeartbeatContainers c ON c.host_id = h.id
            LEFT JOIN HeartbeatProcesses p ON p.container_row_id = c.id
            GROUP BY h.hostname, h.inventory_digest
            """
        )
        return {hostname: (digest, containers, processes) for hostname, digest, containers, processes in cur.fetchall()}


def test_prunes_only_hosts_without_a_beat_for_stale_after(inventory_db):
    """Inventory and digest go for a host idle past the window; recent hosts and empty hosts are untouched."""
    _add_host(inventory_db, "beating", "0 minutes")
    _add_host(inventory_db, "quiet", "40 minutes")
    _add_host(inventory_db, "gone", "3 hours")
    _add_host(inventory_db, "gone-no-inventory", "3 hours", containers=0)

    _prune(inventory_db)

    assert _inventory(inventory_db) == {
        "beating": ("digest", 2, 6),
        "quiet": ("digest", 2, 6),
        "gone": (None, 0, 0),  # host row kept; NULL digest makes it re-send its inventory on return
        "gone-no-inventory": ("digest", 0, 0),
    }


def test_staleness_is_measured_from_the_newest_beat(inventory_db):
    """When beats stop everywhere (ingest outage) only hosts already stale before the last beat are pruned."""
    _add_host(inventory_db, "last-to-beat", "3 hours")
    _add_host(inventory_db, "shortly-before", "3 hours 30 minutes")
    _add_host(inventory_db, "long-gone", "5 hours")

    _prune(inventory_db)

    assert _inventory(inventory_db) == {
        "last-to-beat": ("digest", 2, 6),
        "shortly-before": ("digest", 2, 6),
        "long-gone": (None, 0, 0),
    }


def test_caps_hosts_per_call_and_drains_the_rest_on_the_next_call(inventory_db):
    _add_host(inventory_db, "beating", "0 minutes")
    for name in ("gone-1", "gone-2", "gone-3"):
        _add_host(inventory_db, name, "2 hours")

    _prune(inventory_db, batch_hosts=2, max_batches=1)
    assert Counter(_inventory(inventory_db).values()) == Counter({(None, 0, 0): 2, ("digest", 2, 6): 2})

    _prune(inventory_db, batch_hosts=2, max_batches=1)
    assert _inventory(inventory_db) == {
        "beating": ("digest", 2, 6),
        "gone-1": (None, 0, 0),
        "gone-2": (None, 0, 0),
        "gone-3": (None, 0, 0),
    }
