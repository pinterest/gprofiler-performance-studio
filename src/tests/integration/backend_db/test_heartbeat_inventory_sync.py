#!/usr/bin/env python3
"""
Integration tests for the normalized heartbeat workload inventory sync.

These validate the diff/upsert behavior of ``DBManager._sync_host_inventory`` end
to end: a heartbeat is POSTed to the live backend and the resulting rows in
``HeartbeatContainers`` / ``HeartbeatProcesses`` are inspected directly in
PostgreSQL. Heartbeats are written synchronously, so rows can be inspected as soon
as the POST returns.

The central regression this guards is write amplification at fleet scale: an
unchanged inventory must NOT rewrite rows on every heartbeat. We prove that by
asserting both the primary key ``id`` and the physical row version ``ctid`` are
unchanged across identical beats (a delete-then-insert or an unconditional
``DO UPDATE`` would change both), while a real change to a single process rewrites
only that row and leaves its siblings physically untouched.

Run just this file (needs the live stack + Postgres, see deploy/Makefile.e2e):

    cd src && python -m pytest tests/integration/backend_db/test_heartbeat_inventory_sync.py -v
"""

import uuid
from typing import Any, Dict, List

import psycopg2
import psycopg2.extras
import pytest
import requests


@pytest.fixture(scope="session")
def postgres_connection(pytestconfig):
    """Create a PostgreSQL connection for direct row inspection."""
    conn = psycopg2.connect(
        host=pytestconfig.getoption("--postgres-host", default="localhost"),
        port=pytestconfig.getoption("--postgres-port", default=5432),
        user=pytestconfig.getoption("--postgres-user", default="performance_studio"),
        password=pytestconfig.getoption("--postgres-password", default="performance_studio_password"),
        database=pytestconfig.getoption("--postgres-db", default="performance_studio_db"),
    )
    conn.autocommit = True
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def heartbeat_url(backend_base_url) -> str:
    return f"{backend_base_url}/api/metrics/heartbeat"


def _container(container_id: str, name: str, processes: List[Dict[str, Any]], **overrides: Any) -> Dict[str, Any]:
    payload = {
        "container_id": container_id,
        "container_name": name,
        "runtime": "containerd",
        "namespace": "obs",
        "pod_name": "pod-a",
        "workload_name": "checkout",
        "workload_kind": "k8s",
        "processes": processes,
    }
    payload.update(overrides)
    return payload


_OMIT = object()


def _send(heartbeat_url: str, credentials: Dict[str, str], hostname: str, service: str,
          containers: Any = _OMIT, inventory_digest: Any = _OMIT) -> Dict[str, Any]:
    """POST one heartbeat. ``containers`` / ``inventory_digest`` left at the default are
    omitted from the body entirely (an older agent, or a digest-only beat)."""
    body = {
        "ip_address": "127.0.0.1",
        "hostname": hostname,
        "service_name": service,
        "status": "active",
    }
    if containers is not _OMIT:
        body["containers"] = containers
    if inventory_digest is not _OMIT:
        body["inventory_digest"] = inventory_digest
    response = requests.post(heartbeat_url, headers=credentials, json=body, timeout=10, verify=False)
    assert response.status_code == 200, f"Heartbeat failed: {response.status_code}: {response.text}"
    return response.json()


def _stored_digest(conn, hostname: str, service: str):
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT inventory_digest FROM HostHeartbeats WHERE hostname = %s AND service_name = %s",
            (hostname, service),
        )
        row = cursor.fetchone()
        return row[0] if row else None


def _containers(conn, hostname: str, service: str) -> List[Dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        cursor.execute(
            """
            SELECT hc.id, hc.ctid::text AS ctid, hc.container_id, hc.container_name
            FROM HeartbeatContainers hc
            JOIN HostHeartbeats h ON h.id = hc.host_id
            WHERE h.hostname = %s AND h.service_name = %s
            ORDER BY hc.container_id
            """,
            (hostname, service),
        )
        return [dict(row) for row in cursor.fetchall()]


def _processes(conn, hostname: str, service: str) -> List[Dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        cursor.execute(
            """
            SELECT hp.id, hp.ctid::text AS ctid, hp.pid, hp.process_name, hc.container_id
            FROM HeartbeatProcesses hp
            JOIN HeartbeatContainers hc ON hc.id = hp.container_row_id
            JOIN HostHeartbeats h ON h.id = hc.host_id
            WHERE h.hostname = %s AND h.service_name = %s
            ORDER BY hc.container_id, hp.pid
            """,
            (hostname, service),
        )
        return [dict(row) for row in cursor.fetchall()]


def _cleanup(conn, hostname: str) -> None:
    with conn.cursor() as cursor:
        # HeartbeatContainers/Processes cascade from HostHeartbeats.
        cursor.execute("DELETE FROM HostHeartbeats WHERE hostname = %s", (hostname,))


@pytest.fixture
def host_service(postgres_connection):
    hostname = f"inv-host-{uuid.uuid4().hex[:8]}"
    service = f"inv-svc-{uuid.uuid4().hex[:8]}"
    _cleanup(postgres_connection, hostname)
    yield hostname, service
    _cleanup(postgres_connection, hostname)


def test_non_containerized_host_stores_no_rows(heartbeat_url, credentials, postgres_connection, host_service):
    """A host with no containerized workload (empty list) stores zero inventory rows."""
    hostname, service = host_service
    _send(heartbeat_url, credentials, hostname, service, containers=[])

    assert _containers(postgres_connection, hostname, service) == []
    assert _processes(postgres_connection, hostname, service) == []


def test_stable_inventory_is_not_rewritten(heartbeat_url, credentials, postgres_connection, host_service):
    """Repeated identical heartbeats must not rewrite rows (stable id AND ctid)."""
    hostname, service = host_service
    containers = [
        _container("cid-a", "app", [{"pid": 11, "process_name": "java"}, {"pid": 22, "process_name": "python"}]),
        _container("cid-b", "sidecar", [{"pid": 33, "process_name": "envoy"}]),
    ]

    _send(heartbeat_url, credentials, hostname, service, containers)
    containers_first = _containers(postgres_connection, hostname, service)
    processes_first = _processes(postgres_connection, hostname, service)
    assert [c["container_id"] for c in containers_first] == ["cid-a", "cid-b"]
    assert [(p["container_id"], p["pid"]) for p in processes_first] == [
        ("cid-a", 11), ("cid-a", 22), ("cid-b", 33),
    ]

    # Second identical beat: nothing should be physically rewritten.
    _send(heartbeat_url, credentials, hostname, service, containers)
    containers_second = _containers(postgres_connection, hostname, service)
    processes_second = _processes(postgres_connection, hostname, service)

    assert {(c["id"], c["ctid"]) for c in containers_second} == {(c["id"], c["ctid"]) for c in containers_first}
    assert {(p["id"], p["ctid"]) for p in processes_second} == {(p["id"], p["ctid"]) for p in processes_first}


def test_process_change_is_isolated(heartbeat_url, credentials, postgres_connection, host_service):
    """Changing one process rewrites only that row; unchanged siblings keep their ctid."""
    hostname, service = host_service
    containers = [
        _container("cid-a", "app", [{"pid": 11, "process_name": "java"}, {"pid": 22, "process_name": "python"}]),
    ]
    _send(heartbeat_url, credentials, hostname, service, containers)
    before = {(p["pid"]): p for p in _processes(postgres_connection, hostname, service)}

    # Rename only pid 22.
    containers[0]["processes"] = [
        {"pid": 11, "process_name": "java"},
        {"pid": 22, "process_name": "python3"},
    ]
    _send(heartbeat_url, credentials, hostname, service, containers)
    after = {(p["pid"]): p for p in _processes(postgres_connection, hostname, service)}

    # Unchanged sibling: same physical row.
    assert after[11]["ctid"] == before[11]["ctid"]
    # Changed row: same logical row (id) but rewritten (new ctid) and new name.
    assert after[22]["id"] == before[22]["id"]
    assert after[22]["ctid"] != before[22]["ctid"]
    assert after[22]["process_name"] == "python3"


def test_removed_container_is_pruned(heartbeat_url, credentials, postgres_connection, host_service):
    """A container that stops being reported is deleted along with its processes."""
    hostname, service = host_service
    _send(
        heartbeat_url, credentials, hostname, service,
        containers=[
            _container("cid-a", "app", [{"pid": 11, "process_name": "java"}]),
            _container("cid-b", "sidecar", [{"pid": 33, "process_name": "envoy"}]),
        ],
    )
    assert {c["container_id"] for c in _containers(postgres_connection, hostname, service)} == {"cid-a", "cid-b"}

    # Drop cid-b.
    _send(
        heartbeat_url, credentials, hostname, service,
        containers=[_container("cid-a", "app", [{"pid": 11, "process_name": "java"}])],
    )
    assert {c["container_id"] for c in _containers(postgres_connection, hostname, service)} == {"cid-a"}
    assert {p["pid"] for p in _processes(postgres_connection, hostname, service)} == {11}


def test_null_container_id_is_skipped(heartbeat_url, credentials, postgres_connection, host_service):
    """Entries without a container_id are not stored (containerized-only model)."""
    hostname, service = host_service
    _send(
        heartbeat_url, credentials, hostname, service,
        containers=[
            _container("cid-a", "app", [{"pid": 11, "process_name": "java"}]),
            _container(None, "bare", [{"pid": 99, "process_name": "systemd"}]),
        ],
    )

    stored = _containers(postgres_connection, hostname, service)
    assert [c["container_id"] for c in stored] == ["cid-a"]
    assert {p["pid"] for p in _processes(postgres_connection, hostname, service)} == {11}


# --- change-detected inventory: digest-only beats and need_inventory ----------------------
#
# The agent may send ``inventory_digest`` on every beat and ``containers`` only when the
# server asks (``need_inventory: true``) or the digest changed. The server answers from the
# digest *committed* on the host row, so the agent only stops sending once the server really
# holds matching inventory. Older agents send neither digest nor answer and keep working.


def _digest(containers: List[Dict[str, Any]]) -> str:
    # Same canonical form as gprofiler_dev.heartbeat_inventory (imported by path so this
    # file stays runnable from the tests image, which does not install gprofiler_dev).
    import importlib.util
    import pathlib

    module_path = pathlib.Path(__file__).resolve().parents[3] / "gprofiler-dev" / "gprofiler_dev" / "heartbeat_inventory.py"
    spec = importlib.util.spec_from_file_location("heartbeat_inventory", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.inventory_digest(containers)


def test_older_agent_gets_no_need_inventory_answer(heartbeat_url, credentials, postgres_connection, host_service):
    """No ``inventory_digest`` in the beat -> ``need_inventory`` is null and inventory is synced as before."""
    hostname, service = host_service
    containers = [_container("cid-a", "app", [{"pid": 11, "process_name": "java"}])]

    response = _send(heartbeat_url, credentials, hostname, service, containers=containers)
    assert response.get("need_inventory") is None
    assert _stored_digest(postgres_connection, hostname, service) == _digest(containers)

    # A beat with no containers field at all leaves the stored inventory untouched.
    response = _send(heartbeat_url, credentials, hostname, service)
    assert response.get("need_inventory") is None
    assert {p["pid"] for p in _processes(postgres_connection, hostname, service)} == {11}


def test_digest_only_beats_once_inventory_is_held(heartbeat_url, credentials, postgres_connection, host_service):
    """need_inventory is False as soon as the inventory is committed; digest-only beats then cost no inventory writes."""
    hostname, service = host_service
    containers = [_container("cid-a", "app", [{"pid": 11, "process_name": "java"}, {"pid": 22, "process_name": "python"}])]
    digest = _digest(containers)

    # The write is synchronous, so the very first full beat is confirmed in its own reply.
    response = _send(heartbeat_url, credentials, hostname, service, containers=containers, inventory_digest=digest)
    assert response["need_inventory"] is False
    assert _stored_digest(postgres_connection, hostname, service) == digest
    before = {(p["id"], p["ctid"]) for p in _processes(postgres_connection, hostname, service)}

    for _ in range(3):
        response = _send(heartbeat_url, credentials, hostname, service, inventory_digest=digest)
        assert response["need_inventory"] is False
    assert {(p["id"], p["ctid"]) for p in _processes(postgres_connection, hostname, service)} == before
    assert _stored_digest(postgres_connection, hostname, service) == digest


def test_changed_digest_is_asked_for_and_then_synced(heartbeat_url, credentials, postgres_connection, host_service):
    """A digest-only beat with a new digest is answered need_inventory=True and changes nothing until the inventory arrives."""
    hostname, service = host_service
    old = [_container("cid-a", "app", [{"pid": 11, "process_name": "java"}])]
    new = [_container("cid-a", "app", [{"pid": 11, "process_name": "java"}]), _container("cid-b", "sidecar", [{"pid": 33, "process_name": "envoy"}])]

    _send(heartbeat_url, credentials, hostname, service, containers=old, inventory_digest=_digest(old))

    response = _send(heartbeat_url, credentials, hostname, service, inventory_digest=_digest(new))
    assert response["need_inventory"] is True
    assert _stored_digest(postgres_connection, hostname, service) == _digest(old)  # a claim alone is never stored
    assert {c["container_id"] for c in _containers(postgres_connection, hostname, service)} == {"cid-a"}

    response = _send(heartbeat_url, credentials, hostname, service, containers=new, inventory_digest=_digest(new))
    assert response["need_inventory"] is False
    assert _stored_digest(postgres_connection, hostname, service) == _digest(new)
    assert {c["container_id"] for c in _containers(postgres_connection, hostname, service)} == {"cid-a", "cid-b"}
    assert _send(heartbeat_url, credentials, hostname, service, inventory_digest=_digest(new))["need_inventory"] is False


def test_agent_digest_disagreement_keeps_asking(heartbeat_url, credentials, postgres_connection, host_service):
    """If the agent's digest never matches the server's for the same containers, the server stores its own and keeps asking."""
    hostname, service = host_service
    containers = [_container("cid-a", "app", [{"pid": 11, "process_name": "java"}])]

    response = _send(heartbeat_url, credentials, hostname, service, containers=containers, inventory_digest="not-the-canonical-digest")
    assert response["need_inventory"] is True
    assert _stored_digest(postgres_connection, hostname, service) == _digest(containers)
    response = _send(heartbeat_url, credentials, hostname, service, inventory_digest="not-the-canonical-digest")
    assert response["need_inventory"] is True  # safe degradation: same traffic as an older agent, never silent data loss


def _process_counts(conn, hostname: str, service: str) -> Dict[str, tuple]:
    """container_id -> (stored process_count, actual HeartbeatProcesses rows)."""
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT hc.container_id, hc.process_count,
                   (SELECT COUNT(*) FROM HeartbeatProcesses hp WHERE hp.container_row_id = hc.id)
            FROM HeartbeatContainers hc JOIN HostHeartbeats h ON h.id = hc.host_id
            WHERE h.hostname = %s AND h.service_name = %s
            """,
            (hostname, service),
        )
        return {cid: (stored, actual) for cid, stored, actual in cursor.fetchall()}


def test_container_process_count_follows_sync(heartbeat_url, credentials, postgres_connection, host_service):
    """HeartbeatContainers.process_count always equals the process rows stored for the container (AT-S26)."""
    hostname, service = host_service
    _send(heartbeat_url, credentials, hostname, service, containers=[
        _container("cid-a", "app", [{"pid": 11, "process_name": "java"}, {"pid": 22, "process_name": "python"}]),
        _container("cid-b", "sidecar", [{"pid": 33, "process_name": "envoy"}, {"pid": 33, "process_name": "dup-pid"}]),
        _container("cid-c", "idle", []),
    ])
    counts = _process_counts(postgres_connection, hostname, service)
    assert counts == {"cid-a": (2, 2), "cid-b": (1, 1), "cid-c": (0, 0)}, counts

    # Grow one container, shrink another: the counter tracks the stored rows after the same beat.
    _send(heartbeat_url, credentials, hostname, service, containers=[
        _container("cid-a", "app", [{"pid": 11, "process_name": "java"}]),
        _container("cid-b", "sidecar", [{"pid": 33, "process_name": "envoy"}, {"pid": 34, "process_name": "envoy"}, {"pid": 35, "process_name": "sh"}]),
        _container("cid-c", "idle", []),
    ])
    counts = _process_counts(postgres_connection, hostname, service)
    assert counts == {"cid-a": (1, 1), "cid-b": (3, 3), "cid-c": (0, 0)}, counts


def test_process_counts_are_totals_and_agree_across_paths(heartbeat_url, credentials, postgres_connection, backend_base_url):
    """Service-level process_count counts every process, and the precomputed store agrees with the live path (AT-S27)."""
    service = f"inv-svc-{uuid.uuid4().hex[:8]}"
    hosts = [f"inv-host-{uuid.uuid4().hex[:8]}" for _ in range(2)]
    try:
        # Both hosts run the identical (pid, name) pairs: a DISTINCT (pid, name) count would report 2, not 4.
        same = [{"pid": 11, "process_name": "java"}, {"pid": 22, "process_name": "sh"}]
        for hostname in hosts:
            _send(heartbeat_url, credentials, hostname, service, containers=[_container("cid-a", "app", same)])

        status_url = f"{backend_base_url}/api/metrics/profiling/workload_status"
        live = requests.get(status_url, params={"scope": "service", "service_name": service, "exact_match": "true"},
                            headers=credentials, timeout=30, verify=False).json()
        assert live["tabCounts"]["process"] == 4, live["tabCounts"]
        assert [r["processCount"] for r in live["rows"]] == [4], live["rows"]

        with postgres_connection.cursor() as cursor:
            cursor.execute("CALL refresh_workload_snapshot('2 minutes')")
        precomputed = requests.get(status_url, params={"scope": "service"}, headers=credentials, timeout=30, verify=False).json()
        ours = [r for r in precomputed["rows"] if r["serviceName"] == service]
        assert [r["processCount"] for r in ours] == [4], ours
        assert precomputed["tabCounts"]["process"] >= 4
    finally:
        for hostname in hosts:
            _cleanup(postgres_connection, hostname)
