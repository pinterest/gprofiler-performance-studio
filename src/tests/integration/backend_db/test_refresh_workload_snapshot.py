#!/usr/bin/env python3
"""
Integration tests for the ``refresh_workload_snapshot`` procedure
(scripts/setup/postgres/migrations/single_pass_workload_snapshot_build.sql).

Each test builds the snapshot from a private copy of the inventory and snapshot tables,
placed first on the search_path, holding only the hosts the test creates. Expected rows
are worked out by hand from the inventory below. Needs only Postgres with the schema
loaded (no backend):

    cd src && python -m pytest tests/integration/backend_db/test_refresh_workload_snapshot.py -v
"""

import json
import uuid

import pytest

# hostname: (service, beat age, command as (type, status, combined_config) or None)
HOSTS = {
    "a1": ("svc-a", "10 seconds", ("start", "completed", {"x": 1})),
    "a2": ("svc-a", "30 seconds", None),
    "a3": ("svc-a", "50 seconds", None),  # reports no containers
    "a-gone": ("svc-a", "1 hour", ("start", "completed", None)),  # not fresh
    "b1": ("svc-b", "5 seconds", ("start", "pending", {"y": 2})),
    "b2": ("svc-b", "20 seconds", ("stop", "completed", None)),
}

# (hostname, namespace, pod, container name, workload name, workload kind, [(pid, process name)]),
# inserted in this order
CONTAINERS = [
    ("a1", "ns1", "pod-x", "app", "wl-x", "Deployment", [(100, "java"), (101, "envoy")]),
    ("a1", "ns1", "pod-x", "sidecar", "wl-x", "Deployment", [(102, "envoy")]),
    ("a1", "ns1", "pod-x", "app", "wl-x", "Deployment", []),  # same name again, e.g. a restarted container
    ("a1", "ns1", "pod-s", "db", "wl-s", "StatefulSet", [(200, "postgres")]),
    ("a1", None, "pod-n", "app", None, None, [(300, "java")]),  # a pod without a namespace
    ("a2", "ns1", "pod-s", "db", "wl-s", "StatefulSet", [(200, "postgres")]),  # pod-s spans a1 and a2
    ("a2", "ns1", "pod-s", "backup", "wl-s", "StatefulSet", [(201, "pg_dump")]),
    ("a2", "ns2", "pod-y", "app", None, None, [(100, "java")]),  # a1's (100, java) again, on another host
    ("a2", "ns2", "pod-z", "app", None, None, [(600, "sh")]),
    ("a2", "ns2", "pod-z", "helper", None, None, [(600, "sh")]),  # pid 600 listed by two containers
    ("a2", "ns2", "pod-x", "app", None, None, []),  # pod-x's name in another namespace
    ("a-gone", "ns1", "pod-old", "app", None, None, [(700, "java")]),
    ("b1", None, None, "web", None, None, [(100, "java")]),
    ("b1", None, None, "worker", None, None, [(400, "python")]),
    ("b2", None, None, "web", None, None, [(500, "java")]),
]

# scope, sort_seq, row_id, service, namespace, pod, host / namespace / pod / container / process counts, any_active
EXPECTED_ROWS = [
    ("namespace", 1, "svc-a|ns1", "svc-a", "ns1", None, 2, 1, 2, 4, 5, True),
    ("namespace", 2, "svc-a|ns2", "svc-a", "ns2", None, 1, 1, 3, 2, 2, None),
    ("pod", 1, "svc-a|ns1|pod-s", "svc-a", "ns1", "pod-s", 2, 1, 1, 2, 2, True),
    ("pod", 2, "svc-a|ns1|pod-x", "svc-a", "ns1", "pod-x", 1, 1, 1, 2, 3, True),
    ("pod", 3, "svc-a|ns2|pod-x", "svc-a", "ns2", "pod-x", 1, 1, 1, 1, 0, None),
    ("pod", 4, "svc-a|ns2|pod-y", "svc-a", "ns2", "pod-y", 1, 1, 1, 1, 1, None),
    ("pod", 5, "svc-a|ns2|pod-z", "svc-a", "ns2", "pod-z", 1, 1, 1, 2, 1, None),
    ("pod", 6, "svc-a||pod-n", "svc-a", None, "pod-n", 1, 0, 1, 1, 1, True),
    ("service", 1, "svc-a", "svc-a", None, None, 3, 2, 5, 5, 7, True),
    ("service", 2, "svc-b", "svc-b", None, None, 2, 0, 0, 2, 3, True),
]

# row_id: the group's latest-beating host and its lowest container row in the group, as
# (l_hostname, l_namespace, l_pod_name, l_container_name, l_workload_name, l_workload_kind)
EXPECTED_PICKS = {
    "svc-a|ns1": ("a1", "ns1", "pod-x", "app", "wl-x", "Deployment"),
    "svc-a|ns2": ("a2", "ns2", "pod-y", "app", None, None),
    "svc-a|ns1|pod-s": ("a1", "ns1", "pod-s", "db", "wl-s", "StatefulSet"),
    "svc-a|ns1|pod-x": ("a1", "ns1", "pod-x", "app", "wl-x", "Deployment"),
    "svc-a|ns2|pod-x": ("a2", "ns2", "pod-x", "app", None, None),
    "svc-a|ns2|pod-y": ("a2", "ns2", "pod-y", "app", None, None),
    "svc-a|ns2|pod-z": ("a2", "ns2", "pod-z", "app", None, None),
    "svc-a||pod-n": ("a1", None, "pod-n", "app", None, None),
    "svc-a": ("a1", "ns1", "pod-x", "app", "wl-x", "Deployment"),
    "svc-b": ("b1", None, None, "web", None, None),
}

SNAPSHOT_TABLES = (
    "workload_snapshot_meta",
    "workload_scope_summary_0",
    "workload_scope_summary_1",
    "workload_tab_counts_0",
    "workload_tab_counts_1",
)


@pytest.fixture
def snapshot_db(postgres_connection):
    schema = f"snapshot_test_{uuid.uuid4().hex[:12]}"
    with postgres_connection.cursor() as cur:
        cur.execute(f"CREATE SCHEMA {schema}")
        cur.execute(f"CREATE TABLE {schema}.HostHeartbeats (LIKE public.HostHeartbeats INCLUDING ALL)")
        cur.execute(f"CREATE TABLE {schema}.ProfilingCommands (LIKE public.ProfilingCommands INCLUDING ALL)")
        cur.execute(
            f"CREATE TABLE {schema}.HeartbeatContainers (LIKE public.HeartbeatContainers INCLUDING ALL, "
            f"FOREIGN KEY (host_id) REFERENCES {schema}.HostHeartbeats (id) ON DELETE CASCADE)"
        )
        cur.execute(
            f"CREATE TABLE {schema}.HeartbeatProcesses (LIKE public.HeartbeatProcesses INCLUDING ALL, "
            f"FOREIGN KEY (container_row_id) REFERENCES {schema}.HeartbeatContainers (id) ON DELETE CASCADE)"
        )
        for table in SNAPSHOT_TABLES:
            cur.execute(f"CREATE TABLE {schema}.{table} (LIKE public.{table} INCLUDING ALL)")
        cur.execute(f"INSERT INTO {schema}.workload_snapshot_meta (id, active_generation) VALUES (1, 0)")
        cur.execute(f"CREATE VIEW {schema}.workload_scope_summary AS SELECT * FROM {schema}.workload_scope_summary_0")
        cur.execute(f"CREATE VIEW {schema}.workload_tab_counts AS SELECT * FROM {schema}.workload_tab_counts_0")
        cur.execute(f"SET search_path = {schema}, public")
    try:
        yield postgres_connection
    finally:
        with postgres_connection.cursor() as cur:
            cur.execute("RESET search_path")
            cur.execute(f"DROP SCHEMA {schema} CASCADE")


def _add_inventory(conn):
    """Inserts HOSTS and CONTAINERS; returns each host's (command type, command status, combined_config,
    ip_address, agent_version, run_mode, heartbeat_timestamp) as the snapshot should read them."""
    host_ids, host_fields = {}, {}
    with conn.cursor() as cur:
        for i, (hostname, (service, beat_age, command)) in enumerate(HOSTS.items(), start=1):
            run_mode = "k8s" if service == "svc-a" else "standalone_executable"
            cur.execute(
                "INSERT INTO HostHeartbeats (hostname, ip_address, service_name, agent_version, run_mode, "
                "heartbeat_timestamp) VALUES (%s, %s, %s, %s, %s, now() - %s::interval) "
                "RETURNING id, host(ip_address), agent_version, run_mode, heartbeat_timestamp",
                (hostname, f"10.0.0.{i}", service, f"v{i}", run_mode, beat_age),
            )
            host_ids[hostname], *fields = cur.fetchone()
            host_fields[hostname] = (*(command or ("N/A", None, None)), *fields)
            if command:
                command_type, status, config = command
                config = json.dumps(config) if config else None
                cur.execute(
                    "INSERT INTO ProfilingCommands (command_id, hostname, service_name, command_type, request_ids, "
                    "combined_config, status) VALUES (%s, %s, %s, %s, ARRAY[%s]::uuid[], %s, %s)",
                    (str(uuid.uuid4()), hostname, service, command_type, str(uuid.uuid4()), config, status),
                )
        for c, (hostname, namespace, pod, name, workload_name, workload_kind, processes) in enumerate(CONTAINERS):
            cur.execute(
                "INSERT INTO HeartbeatContainers (host_id, container_id, container_name, namespace, pod_name, "
                "workload_name, workload_kind) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (host_ids[hostname], f"container-{c}", name, namespace, pod, workload_name, workload_kind),
            )
            container_row_id = cur.fetchone()[0]
            for pid, process_name in processes:
                cur.execute(
                    "INSERT INTO HeartbeatProcesses (container_row_id, pid, process_name) VALUES (%s, %s, %s)",
                    (container_row_id, pid, process_name),
                )
    return host_fields


def _refresh(conn, fresh_interval: str = "10 minutes") -> None:
    with conn.cursor() as cur:
        cur.execute("CALL refresh_workload_snapshot(%s::interval)", (fresh_interval,))


def _rows(conn, sql: str):
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


def test_builds_service_namespace_and_pod_rows_from_fresh_hosts(snapshot_db):
    host_fields = _add_inventory(snapshot_db)
    with snapshot_db.cursor() as cur:  # leftovers of an earlier build in the generation about to be written
        cur.execute("INSERT INTO workload_scope_summary_1 (scope, sort_seq, row_id) VALUES ('service', 1, 'old')")
        cur.execute("INSERT INTO workload_tab_counts_1 (scope, count) VALUES ('service', 99)")

    _refresh(snapshot_db)

    assert _rows(snapshot_db, "SELECT active_generation FROM workload_snapshot_meta") == [(1,)]
    rows = _rows(
        snapshot_db,
        "SELECT scope, sort_seq, row_id, service_name, namespace, pod_name, host_count, namespace_count, "
        "pod_count, container_count, process_count, any_active FROM workload_scope_summary ORDER BY scope, sort_seq",
    )
    assert rows == EXPECTED_ROWS
    picks = _rows(
        snapshot_db,
        "SELECT row_id, l_hostname, l_namespace, l_pod_name, l_container_name, l_workload_name, l_workload_kind "
        "FROM workload_scope_summary",
    )
    assert {row_id: tuple(pick) for row_id, *pick in picks} == EXPECTED_PICKS
    assert dict(_rows(snapshot_db, "SELECT scope, count FROM workload_tab_counts")) == {
        "service": 2,
        "namespace": 2,
        "pod": 6,
        "host": 5,
        "active_hosts": 5,
        "container": 13,  # distinct (host, namespace, pod, container name)
        "process": 12,  # distinct (host, pid)
    }
    # The other host columns are the picked host's; per-process columns stay empty at these scopes.
    for l_hostname, *fields in _rows(
        snapshot_db,
        "SELECT l_hostname, l_command_type, l_command_status, l_combined_config, l_ip_address, l_agent_version, "
        "l_run_mode, l_heartbeat_timestamp, hostname, container_name, pids, l_process_name, l_pid, "
        "l_profiling_status FROM workload_scope_summary",
    ):
        assert tuple(fields) == (*host_fields[l_hostname], None, None, None, None, None, None)
