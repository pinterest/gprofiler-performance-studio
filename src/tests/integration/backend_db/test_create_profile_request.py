#!/usr/bin/env python3
"""
Integration test for profile request creation and database verification.

This module contains integration tests that validate the complete flow from API request
to database storage, including:
1. Making API calls to create profile requests
2. Verifying database entries in PostgreSQL tables
3. Testing the full end-to-end flow from API to database
4. Validating data consistency between API responses and database state
"""

import pytest
import requests
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional
import psycopg2
import psycopg2.extras


@pytest.fixture(scope="session")
def postgres_connection(pytestconfig):
    """Create a PostgreSQL connection for database verification."""
    # Get PostgreSQL configuration from pytest config
    postgres_user = pytestconfig.getoption("--postgres-user", default="postgres")
    postgres_password = pytestconfig.getoption(
        "--postgres-password", default="password"
    )
    postgres_host = pytestconfig.getoption("--postgres-host", default="localhost")
    postgres_port = pytestconfig.getoption("--postgres-port", default=54321)
    postgres_db = pytestconfig.getoption("--postgres-db", default="gprofiler")

    # Create connection
    conn = psycopg2.connect(
        host=postgres_host,
        port=postgres_port,
        user=postgres_user,
        password=postgres_password,
        database=postgres_db,
    )
    conn.autocommit = True

    yield conn

    # Cleanup
    conn.close()


@pytest.fixture(scope="session")
def test_service_name() -> str:
    """Generate a unique service name for testing."""
    return f"test-service-{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="session")
def test_hostname() -> str:
    """Generate a unique hostname for testing."""
    return f"test-host-{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="session")
def profile_request_url(backend_base_url) -> str:
    """Get the full URL for the profile request endpoint."""
    return f"{backend_base_url}/api/metrics/profile_request"


@pytest.fixture(scope="session")
def heartbeat_url(backend_base_url) -> str:
    """Get the full URL for the heartbeat endpoint."""
    return f"{backend_base_url}/api/metrics/heartbeat"


@pytest.fixture(scope="session")
def db_setup_and_teardown(postgres_connection, test_service_name, test_hostname):
    print("\nCleaning up test data before tests...")
    cleanup_test_data(postgres_connection, test_service_name, test_hostname)

    yield  # This is where the tests will run

    print("\nCleaning up test data after tests...")
    cleanup_test_data(postgres_connection, test_service_name, test_hostname)


@pytest.fixture(scope="session")
def valid_heartbeat_data(test_hostname: str, test_service_name: str) -> Dict[str, Any]:
    """Provide valid heartbeat data for testing."""
    return {
        "hostname": test_hostname,
        "ip_address": "127.0.0.1",
        "service_name": test_service_name,
        "status": "active",
        "timestamp": datetime.now().isoformat(),
        "last_command_id": None,
    }


@pytest.fixture(scope="session")
def valid_start_request_data_single_host_stop_level_host(
    test_service_name: str, test_hostname: str
) -> Dict[str, Any]:
    """Provide valid start request data for testing."""
    return {
        "service_name": test_service_name,
        "request_type": "start",
        "duration": 60,
        "frequency": 11,
        "profiling_mode": "cpu",
        "target_hosts": {
            test_hostname: None,
        },
        "stop_level": "host",
        "additional_args": {"test": True, "environment": "integration_test"},
    }


@pytest.fixture(scope="session")
def valid_stop_request_data_single_host_stop_level_host(
    test_service_name: str, test_hostname: str
) -> Dict[str, Any]:
    """Provide valid stop request data for testing."""
    return {
        "service_name": test_service_name,
        "request_type": "stop",
        "target_hosts": {
            test_hostname: None,
        },
        "stop_level": "host",
    }


@pytest.fixture(scope="session")
def valid_start_request_data_single_host_stop_level_process_single_process(
    test_service_name: str, test_hostname: str
) -> Dict[str, Any]:
    """Provide valid start request data for testing."""
    return {
        "service_name": test_service_name,
        "request_type": "start",
        "duration": 60,
        "frequency": 11,
        "profiling_mode": "cpu",
        "target_hosts": {
            test_hostname: [1234],
        },  # Single PID for process-level profiling
        "stop_level": "process",
        "additional_args": {"test": True, "environment": "integration_test"},
    }


@pytest.fixture(scope="session")
def valid_stop_request_data_single_host_stop_level_process_single_process(
    test_service_name: str, test_hostname: str
) -> Dict[str, Any]:
    """Provide valid stop request data for testing."""
    return {
        "service_name": test_service_name,
        "request_type": "stop",
        "target_hosts": {
            test_hostname: [1234],  # Single PID for process-level stop
        },
        "stop_level": "process",
    }


@pytest.fixture(scope="session")
def valid_start_request_data_single_host_stop_level_process_multi_process(
    test_service_name: str, test_hostname: str
) -> Dict[str, Any]:
    """Provide valid start request data for testing."""
    return {
        "service_name": test_service_name,
        "request_type": "start",
        "duration": 60,
        "frequency": 11,
        "profiling_mode": "cpu",
        "target_hosts": {
            test_hostname: [1234, 5678],  # Multiple PIDs for process-level profiling
        },
        "stop_level": "process",
        "additional_args": {"test": True, "environment": "integration_test"},
    }


@pytest.fixture(scope="session")
def valid_stop_request_data_single_host_stop_level_process_multi_process(
    test_service_name: str, test_hostname: str
) -> Dict[str, Any]:
    """Provide valid stop request data for testing."""
    return {
        "service_name": test_service_name,
        "request_type": "stop",
        "target_hosts": {
            test_hostname: [1234, 5678],  # Multiple PIDs for process-level stop
        },
        "stop_level": "process",
    }


def get_profiling_request_from_db(conn, request_id: str) -> Optional[Dict[str, Any]]:
    """Get a profiling request from the database by request_id."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        cursor.execute(
            """
            SELECT * FROM ProfilingRequests
            WHERE request_id = %s
            """,
            (request_id,),
        )
        result = cursor.fetchone()
        return dict(result) if result else None


def get_profiling_commands_from_db(
    conn, service_name: str, hostname: str = None, command_ids: list[str] = None
) -> List[Dict[str, Any]]:
    """Get profiling commands from the database by service and optionally hostname."""
    if command_ids:
        formatted_command_ids = [f"'{command_id}'" for command_id in command_ids]
        formatted_command_ids = f"({','.join(formatted_command_ids)})"

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        cursor.execute(
            f"""
            SELECT
                *
            FROM
                ProfilingCommands
            WHERE
                TRUE
                AND service_name = '{service_name}'
                AND {f"hostname = '{hostname}'" if hostname else "TRUE"}
                AND {f"command_id IN {formatted_command_ids}" if command_ids else "TRUE"}
            ORDER BY
                created_at DESC
            """,
        )

        results = cursor.fetchall()
        return [dict(result) for result in results]


def get_host_heartbeats_from_db(
    conn, hostname: str, service_name: str = None
) -> List[Dict[str, Any]]:
    """Get host heartbeats from the database by hostname and optionally service."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        if service_name:
            cursor.execute(
                """
                SELECT * FROM HostHeartbeats
                WHERE hostname = %s AND service_name = %s
                ORDER BY heartbeat_timestamp DESC
                """,
                (hostname, service_name),
            )
        else:
            cursor.execute(
                """
                SELECT * FROM HostHeartbeats
                WHERE hostname = %s
                ORDER BY heartbeat_timestamp DESC
                """,
                (hostname,),
            )
        results = cursor.fetchall()
        return [dict(result) for result in results]


def cleanup_test_data(conn, service_name: str, hostname: str = None):
    """Clean up test data from the database."""
    with conn.cursor() as cursor:
        # Clean up in reverse dependency order
        if hostname:
            cursor.execute(
                "DELETE FROM HostHeartbeats WHERE hostname = %s", (hostname,)
            )
            cursor.execute(
                "DELETE FROM ProfilingCommands WHERE hostname = %s", (hostname,)
            )
            cursor.execute(
                "DELETE FROM ProfilingExecutions WHERE hostname = %s", (hostname,)
            )

        cursor.execute(
            "DELETE FROM ProfilingRequests WHERE service_name = %s", (service_name,)
        )
        cursor.execute(
            "DELETE FROM ProfilingCommands WHERE service_name = %s", (service_name,)
        )


def send_heartbeat_and_verify(
    heartbeat_url: str,
    heartbeat_data: Dict[str, Any],
    credentials: Dict[str, Any],
    postgres_connection,
    expected_command_present: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Send a heartbeat request and verify the response and database entry.

    Returns the received command if any, None otherwise.
    """
    # Send heartbeat request
    response = requests.post(
        heartbeat_url,
        headers=credentials,
        json=heartbeat_data,
        timeout=10,
        verify=False,
    )

    # Verify heartbeat response
    assert (
        response.status_code == 200
    ), f"Heartbeat failed: {response.status_code}: {response.text}"
    result = response.json()
    assert "message" in result, "Heartbeat response should contain 'message' field"

    # Verify heartbeat was stored in database
    db_heartbeats = get_host_heartbeats_from_db(
        postgres_connection, heartbeat_data["hostname"], heartbeat_data["service_name"]
    )
    assert len(db_heartbeats) >= 1, "No heartbeat entries found in database"

    # Verify most recent heartbeat matches our data
    latest_heartbeat = db_heartbeats[0]
    assert latest_heartbeat["hostname"] == heartbeat_data["hostname"]
    assert latest_heartbeat["service_name"] == heartbeat_data["service_name"]
    assert latest_heartbeat["ip_address"] == heartbeat_data["ip_address"]
    assert latest_heartbeat["status"] == heartbeat_data["status"]

    # Check for command in response
    received_command = None
    profiling_command = result.get("profiling_command", None)
    command_id = result.get("command_id", None)
    if profiling_command and command_id:
        assert (
            expected_command_present
        ), "Received unexpected command in heartbeat response"
        received_command = {
            "command_id": result["command_id"],
            "profiling_command": result["profiling_command"],
        }
        print(
            f"📋 Received command via heartbeat: {result['profiling_command'].get('command_type', 'unknown')} (ID: {result['command_id']})"
        )
    else:
        assert (
            not expected_command_present
        ), "Expected command in heartbeat response but none received"
        print("📭 No commands received in heartbeat response")

    print(
        f"✅ Heartbeat successfully sent and verified in database for {heartbeat_data['hostname']}"
    )
    return received_command


@pytest.mark.usefixtures("db_setup_and_teardown")
class TestProfileRequestIntegration:
    """Integration tests for profile request creation and database verification."""

    @pytest.mark.order(1)
    def test_create_start_profile_request_for_single_host_stop_level_host(
        self,
        profile_request_url: str,
        heartbeat_url: str,
        valid_start_request_data_single_host_stop_level_host: Dict[str, Any],
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
        postgres_connection,
        test_service_name: str,
        test_hostname: str,
    ):
        """Test creating a start profile request, sending heartbeat, and verify database entries."""

        # Step 1: Make API call to create profile request
        response = requests.post(
            profile_request_url,
            headers=credentials,
            json=valid_start_request_data_single_host_stop_level_host,
            timeout=10,
            verify=False,
        )

        # Verify API response
        assert (
            response.status_code == 200
        ), f"Expected 200, got {response.status_code}: {response.text}"
        result = response.json()

        # Validate response structure
        assert "success" in result
        assert "message" in result
        assert "request_id" in result
        assert "command_ids" in result
        assert result["success"] is True

        request_id = result["request_id"]
        command_ids = result["command_ids"]

        # Verify request_id is a valid UUID
        assert request_id
        assert len(command_ids) >= 1

        # Step 2: Verify ProfilingRequests table entry
        db_request = get_profiling_request_from_db(postgres_connection, request_id)
        assert db_request is not None, f"Request {request_id} not found in database"

        # Verify request data matches what was sent
        assert (
            db_request["service_name"]
            == valid_start_request_data_single_host_stop_level_host["service_name"]
        )
        assert (
            db_request["request_type"]
            == valid_start_request_data_single_host_stop_level_host["request_type"]
        )
        assert (
            db_request["duration"]
            == valid_start_request_data_single_host_stop_level_host["duration"]
        )
        assert (
            db_request["frequency"]
            == valid_start_request_data_single_host_stop_level_host["frequency"]
        )
        assert (
            db_request["profiling_mode"]
            == valid_start_request_data_single_host_stop_level_host["profiling_mode"]
        )
        assert db_request["target_hostnames"] == list(
            valid_start_request_data_single_host_stop_level_host["target_hosts"].keys()
        )
        assert db_request["status"] == "pending"

        # Verify additional_args JSON
        stored_additional_args = db_request["additional_args"]
        if stored_additional_args:
            assert (
                stored_additional_args
                == valid_start_request_data_single_host_stop_level_host[
                    "additional_args"
                ]
            )

        # Verify timestamps
        assert db_request["created_at"] is not None
        assert db_request["updated_at"] is not None

        # Step 3: Verify ProfilingCommands table entries
        db_commands = get_profiling_commands_from_db(
            postgres_connection,
            test_service_name,
            test_hostname,
            command_ids=command_ids,
        )
        assert len(db_commands) >= 1, "No commands found in database"

        # Verify at least one command matches our request
        command_found = False
        for db_command in db_commands:
            if db_command["command_id"] in command_ids:
                command_found = True
                assert db_command["hostname"] == test_hostname
                assert db_command["service_name"] == test_service_name
                assert db_command["command_type"] == "start"
                assert db_command["status"] == "pending"

                # Verify request_ids contains our request
                request_ids_in_command = db_command["request_ids"]
                assert request_id in request_ids_in_command

                # Verify combined_config contains expected fields
                combined_config = db_command["combined_config"]
                assert combined_config is not None
                assert (
                    combined_config.get("duration")
                    == valid_start_request_data_single_host_stop_level_host["duration"]
                )
                assert (
                    combined_config.get("frequency")
                    == valid_start_request_data_single_host_stop_level_host["frequency"]
                )
                assert (
                    combined_config.get("profiling_mode")
                    == valid_start_request_data_single_host_stop_level_host[
                        "profiling_mode"
                    ]
                )

                break

        assert command_found, f"Command with ID in {command_ids} not found in database"

        # Step 4: Send heartbeat and verify command delivery
        print("🔄 Sending heartbeat to retrieve commands...")
        received_command = send_heartbeat_and_verify(
            heartbeat_url,
            valid_heartbeat_data,
            credentials,
            postgres_connection,
            expected_command_present=True,
        )

        # Step 5: Verify the received command matches our created command
        assert (
            received_command is not None
        ), "Expected to receive a command via heartbeat"
        assert (
            received_command["command_id"] in command_ids
        ), f"Received command ID {received_command['command_id']} not in expected IDs {command_ids}"

        # Verify command content
        profiling_command = received_command["profiling_command"]
        assert profiling_command["command_type"] == "start"
        assert (
            profiling_command["combined_config"]["duration"]
            == valid_start_request_data_single_host_stop_level_host["duration"]
        )
        assert (
            profiling_command["combined_config"]["frequency"]
            == valid_start_request_data_single_host_stop_level_host["frequency"]
        )
        assert (
            profiling_command["combined_config"]["profiling_mode"]
            == valid_start_request_data_single_host_stop_level_host["profiling_mode"]
        )

        # Step 6: Send another heartbeat with last_command_id to simulate acknowledgment
        print("🔄 Sending acknowledgment heartbeat...")
        heartbeat_with_ack = valid_heartbeat_data.copy()
        heartbeat_with_ack["last_command_id"] = received_command["command_id"]

        ack_command = send_heartbeat_and_verify(
            heartbeat_url,
            heartbeat_with_ack,
            credentials,
            postgres_connection,
            expected_command_present=False,  # Should not receive the same command again
        )

        # Should not receive the same command again
        assert (
            ack_command is None
        ), "Should not receive the same command after acknowledgment"

        print(
            f"✅ End-to-end integration test passed: Request {request_id} created, command delivered via heartbeat, and acknowledged"
        )

    @pytest.mark.order(2)
    def test_create_stop_profile_request_for_single_host_stop_level_host(
        self,
        profile_request_url: str,
        heartbeat_url: str,
        valid_stop_request_data_single_host_stop_level_host: Dict[str, Any],
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
        postgres_connection,
        test_service_name: str,
        test_hostname: str,
    ):
        """Test creating a stop profile request, sending heartbeat, and verify database entries."""

        # Step 1: Make API call to create stop profile request
        response = requests.post(
            profile_request_url,
            headers=credentials,
            json=valid_stop_request_data_single_host_stop_level_host,
            timeout=10,
            verify=False,
        )

        # Verify API response
        assert (
            response.status_code == 200
        ), f"Expected 200, got {response.status_code}: {response.text}"
        result = response.json()

        # Validate response structure
        assert "success" in result
        assert "message" in result
        assert "request_id" in result
        assert "command_ids" in result
        assert result["success"] is True

        request_id = result["request_id"]
        command_ids = result["command_ids"]

        # Step 2: Verify ProfilingRequests table entry
        db_request = get_profiling_request_from_db(postgres_connection, request_id)
        assert db_request is not None, f"Request {request_id} not found in database"

        # Verify request data matches what was sent
        assert (
            db_request["service_name"]
            == valid_stop_request_data_single_host_stop_level_host["service_name"]
        )
        assert (
            db_request["request_type"]
            == valid_stop_request_data_single_host_stop_level_host["request_type"]
        )
        assert db_request["target_hostnames"] == list(
            valid_stop_request_data_single_host_stop_level_host["target_hosts"].keys()
        )
        assert db_request["status"] == "pending"

        # Step 3: Verify ProfilingCommands table entries for stop command
        db_commands = get_profiling_commands_from_db(
            postgres_connection, test_service_name, test_hostname
        )

        # For stop commands, there should be at least one command
        if len(command_ids) > 0:
            assert len(db_commands) >= 1, "No stop commands found in database"

            # Verify stop command properties
            stop_command_found = False
            for db_command in db_commands:
                if db_command["command_id"] in command_ids:
                    stop_command_found = True
                    assert db_command["hostname"] == test_hostname
                    assert db_command["service_name"] == test_service_name
                    assert db_command["command_type"] == "stop"
                    assert db_command["status"] == "pending"
                    break

            assert (
                stop_command_found
            ), f"Stop command with ID in {command_ids} not found in database"

        # Step 4: Send heartbeat and verify stop command delivery
        print("🔄 Sending heartbeat to retrieve stop commands...")
        received_command = send_heartbeat_and_verify(
            heartbeat_url,
            valid_heartbeat_data,
            credentials,
            postgres_connection,
            expected_command_present=True,
        )

        # Step 5: Verify the received command matches our created stop command
        assert (
            received_command is not None
        ), "Expected to receive a stop command via heartbeat"
        assert (
            received_command["command_id"] in command_ids
        ), f"Received command ID {received_command['command_id']} not in expected IDs {command_ids}"

        # Verify stop command content
        profiling_command = received_command["profiling_command"]
        assert profiling_command["command_type"] == "stop"
        assert (
            profiling_command["combined_config"]["stop_level"]
            == valid_stop_request_data_single_host_stop_level_host["stop_level"]
        )

        # Step 6: Send acknowledgment heartbeat
        print("🔄 Sending acknowledgment heartbeat for stop command...")
        heartbeat_with_ack = valid_heartbeat_data.copy()
        heartbeat_with_ack["last_command_id"] = received_command["command_id"]

        ack_command = send_heartbeat_and_verify(
            heartbeat_url,
            heartbeat_with_ack,
            credentials,
            postgres_connection,
            expected_command_present=False,
        )

        assert (
            ack_command is None
        ), "Should not receive the same stop command after acknowledgment"

        print(
            f"✅ End-to-end stop integration test passed: Stop request {request_id} created, command delivered via heartbeat, and acknowledged"
        )

    @pytest.mark.order(3)
    def test_create_start_profile_request_for_single_host_stop_level_process_single_process(
        self,
        profile_request_url: str,
        heartbeat_url: str,
        valid_start_request_data_single_host_stop_level_process_single_process: Dict[
            str, Any
        ],
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
        postgres_connection,
        test_service_name: str,
        test_hostname: str,
    ):
        """Test creating a start profile request with single process PID, sending heartbeat, and verify database entries."""

        # Step 1: Make API call to create profile request
        response = requests.post(
            profile_request_url,
            headers=credentials,
            json=valid_start_request_data_single_host_stop_level_process_single_process,
            timeout=10,
            verify=False,
        )

        # Verify API response
        assert (
            response.status_code == 200
        ), f"Expected 200, got {response.status_code}: {response.text}"
        result = response.json()

        # Validate response structure
        assert "success" in result
        assert "message" in result
        assert "request_id" in result
        assert "command_ids" in result
        assert result["success"] is True

        request_id = result["request_id"]
        command_ids = result["command_ids"]

        # Verify request_id is a valid UUID
        assert request_id
        assert len(command_ids) >= 1

        # Step 2: Verify ProfilingRequests table entry
        db_request = get_profiling_request_from_db(postgres_connection, request_id)
        assert db_request is not None, f"Request {request_id} not found in database"

        # Verify request data matches what was sent
        assert (
            db_request["service_name"]
            == valid_start_request_data_single_host_stop_level_process_single_process[
                "service_name"
            ]
        )
        assert (
            db_request["request_type"]
            == valid_start_request_data_single_host_stop_level_process_single_process[
                "request_type"
            ]
        )
        assert (
            db_request["duration"]
            == valid_start_request_data_single_host_stop_level_process_single_process[
                "duration"
            ]
        )
        assert (
            db_request["frequency"]
            == valid_start_request_data_single_host_stop_level_process_single_process[
                "frequency"
            ]
        )
        assert (
            db_request["profiling_mode"]
            == valid_start_request_data_single_host_stop_level_process_single_process[
                "profiling_mode"
            ]
        )
        assert db_request["target_hostnames"] == list(
            valid_start_request_data_single_host_stop_level_process_single_process[
                "target_hosts"
            ].keys()
        )
        assert (
            db_request["stop_level"]
            == valid_start_request_data_single_host_stop_level_process_single_process[
                "stop_level"
            ]
        )
        assert db_request["status"] == "pending"

        # Verify additional_args JSON
        stored_additional_args = db_request["additional_args"]
        if stored_additional_args:
            assert (
                stored_additional_args
                == valid_start_request_data_single_host_stop_level_process_single_process[
                    "additional_args"
                ]
            )

        # Verify timestamps
        assert db_request["created_at"] is not None
        assert db_request["updated_at"] is not None

        # Step 3: Verify ProfilingCommands table entries
        db_commands = get_profiling_commands_from_db(
            postgres_connection,
            test_service_name,
            test_hostname,
            command_ids=command_ids,
        )
        assert len(db_commands) >= 1, "No commands found in database"

        # Verify at least one command matches our request
        command_found = False
        for db_command in db_commands:
            if db_command["command_id"] in command_ids:
                command_found = True
                assert db_command["hostname"] == test_hostname
                assert db_command["service_name"] == test_service_name
                assert db_command["command_type"] == "start"
                assert db_command["status"] == "pending"

                # Verify request_ids contains our request
                request_ids_in_command = db_command["request_ids"]
                assert request_id in request_ids_in_command

                # Verify combined_config contains expected fields including PIDs
                combined_config = db_command["combined_config"]
                assert combined_config is not None
                assert (
                    combined_config.get("duration")
                    == valid_start_request_data_single_host_stop_level_process_single_process[
                        "duration"
                    ]
                )
                assert (
                    combined_config.get("frequency")
                    == valid_start_request_data_single_host_stop_level_process_single_process[
                        "frequency"
                    ]
                )
                assert (
                    combined_config.get("profiling_mode")
                    == valid_start_request_data_single_host_stop_level_process_single_process[
                        "profiling_mode"
                    ]
                )
                assert (
                    combined_config.get("pids")
                    == valid_start_request_data_single_host_stop_level_process_single_process[
                        "target_hosts"
                    ][
                        test_hostname
                    ]
                )

                break

        assert command_found, f"Command with ID in {command_ids} not found in database"

        # Step 4: Send heartbeat and verify command delivery
        print("🔄 Sending heartbeat to retrieve process-level start commands...")
        received_command = send_heartbeat_and_verify(
            heartbeat_url,
            valid_heartbeat_data,
            credentials,
            postgres_connection,
            expected_command_present=True,
        )

        # Step 5: Verify the received command matches our created command
        assert (
            received_command is not None
        ), "Expected to receive a command via heartbeat"
        assert (
            received_command["command_id"] in command_ids
        ), f"Received command ID {received_command['command_id']} not in expected IDs {command_ids}"

        # Verify command content including process-level details
        profiling_command = received_command["profiling_command"]
        assert profiling_command["command_type"] == "start"
        assert (
            profiling_command["combined_config"]["duration"]
            == valid_start_request_data_single_host_stop_level_process_single_process[
                "duration"
            ]
        )
        assert (
            profiling_command["combined_config"]["frequency"]
            == valid_start_request_data_single_host_stop_level_process_single_process[
                "frequency"
            ]
        )
        assert (
            profiling_command["combined_config"]["profiling_mode"]
            == valid_start_request_data_single_host_stop_level_process_single_process[
                "profiling_mode"
            ]
        )
        assert (
            profiling_command["combined_config"]["pids"]
            == valid_start_request_data_single_host_stop_level_process_single_process[
                "target_hosts"
            ][test_hostname]
        )

        # Step 6: Send another heartbeat with last_command_id to simulate acknowledgment
        print("🔄 Sending acknowledgment heartbeat for process-level start command...")
        heartbeat_with_ack = valid_heartbeat_data.copy()
        heartbeat_with_ack["last_command_id"] = received_command["command_id"]

        ack_command = send_heartbeat_and_verify(
            heartbeat_url,
            heartbeat_with_ack,
            credentials,
            postgres_connection,
            expected_command_present=False,  # Should not receive the same command again
        )

        # Should not receive the same command again
        assert (
            ack_command is None
        ), "Should not receive the same command after acknowledgment"

        print(
            f"✅ End-to-end process-level start integration test passed: Request {request_id} with PID {valid_start_request_data_single_host_stop_level_process_single_process['target_hosts'][test_hostname]} created, command delivered via heartbeat, and acknowledged"
        )

    @pytest.mark.order(4)
    def test_create_stop_profile_request_for_single_host_stop_level_process_single_process(
        self,
        profile_request_url: str,
        heartbeat_url: str,
        valid_stop_request_data_single_host_stop_level_process_single_process: Dict[
            str, Any
        ],
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
        postgres_connection,
        test_service_name: str,
        test_hostname: str,
    ):
        """Test creating a stop profile request with single process PID, sending heartbeat, and verify database entries."""

        # Step 1: Make API call to create stop profile request
        response = requests.post(
            profile_request_url,
            headers=credentials,
            json=valid_stop_request_data_single_host_stop_level_process_single_process,
            timeout=10,
            verify=False,
        )

        # Verify API response
        assert (
            response.status_code == 200
        ), f"Expected 200, got {response.status_code}: {response.text}"
        result = response.json()

        # Validate response structure
        assert "success" in result
        assert "message" in result
        assert "request_id" in result
        assert "command_ids" in result
        assert result["success"] is True

        request_id = result["request_id"]
        command_ids = result["command_ids"]

        # Step 2: Verify ProfilingRequests table entry
        db_request = get_profiling_request_from_db(postgres_connection, request_id)
        assert db_request is not None, f"Request {request_id} not found in database"

        # Verify request data matches what was sent
        assert (
            db_request["service_name"]
            == valid_stop_request_data_single_host_stop_level_process_single_process[
                "service_name"
            ]
        )
        assert (
            db_request["request_type"]
            == valid_stop_request_data_single_host_stop_level_process_single_process[
                "request_type"
            ]
        )
        assert db_request["target_hostnames"] == list(
            valid_stop_request_data_single_host_stop_level_process_single_process[
                "target_hosts"
            ].keys()
        )

        assert (
            db_request["stop_level"]
            == valid_stop_request_data_single_host_stop_level_process_single_process[
                "stop_level"
            ]
        )
        assert db_request["status"] == "pending"

        # Step 3: Verify ProfilingCommands table entries for stop command
        db_commands = get_profiling_commands_from_db(
            postgres_connection, test_service_name, test_hostname
        )

        # For stop commands, there should be at least one command
        if len(command_ids) > 0:
            assert len(db_commands) >= 1, "No stop commands found in database"

            # Verify stop command properties
            stop_command_found = False
            for db_command in db_commands:
                if db_command["command_id"] in command_ids:
                    stop_command_found = True
                    assert db_command["hostname"] == test_hostname
                    assert db_command["service_name"] == test_service_name
                    assert db_command["command_type"] == "stop"
                    assert db_command["status"] == "pending"

                    # Verify process-level stop command details
                    combined_config = db_command["combined_config"]
                    assert combined_config is not None
                    assert (
                        combined_config.get("pids")
                        == valid_stop_request_data_single_host_stop_level_process_single_process[
                            "target_hosts"
                        ][
                            test_hostname
                        ]
                    )
                    assert (
                        combined_config.get("stop_level")
                        == valid_stop_request_data_single_host_stop_level_process_single_process[
                            "stop_level"
                        ]
                    )
                    break

            assert (
                stop_command_found
            ), f"Stop command with ID in {command_ids} not found in database"

        # Step 4: Send heartbeat and verify stop command delivery
        print("🔄 Sending heartbeat to retrieve process-level stop commands...")
        received_command = send_heartbeat_and_verify(
            heartbeat_url,
            valid_heartbeat_data,
            credentials,
            postgres_connection,
            expected_command_present=True,
        )

        # Step 5: Verify the received command matches our created stop command
        assert (
            received_command is not None
        ), "Expected to receive a stop command via heartbeat"
        assert (
            received_command["command_id"] in command_ids
        ), f"Received command ID {received_command['command_id']} not in expected IDs {command_ids}"

        # Verify stop command content including process-level details
        profiling_command = received_command["profiling_command"]
        assert profiling_command["command_type"] == "stop"
        assert (
            profiling_command["combined_config"]["stop_level"]
            == valid_stop_request_data_single_host_stop_level_process_single_process[
                "stop_level"
            ]
        )
        assert (
            profiling_command["combined_config"]["pids"]
            == valid_stop_request_data_single_host_stop_level_process_single_process[
                "pids"
            ]
        )

        # Step 6: Send acknowledgment heartbeat
        print("🔄 Sending acknowledgment heartbeat for process-level stop command...")
        heartbeat_with_ack = valid_heartbeat_data.copy()
        heartbeat_with_ack["last_command_id"] = received_command["command_id"]

        ack_command = send_heartbeat_and_verify(
            heartbeat_url,
            heartbeat_with_ack,
            credentials,
            postgres_connection,
            expected_command_present=False,
        )

        assert (
            ack_command is None
        ), "Should not receive the same stop command after acknowledgment"

        print(
            f"✅ End-to-end process-level stop integration test passed: Stop request {request_id} with PID {valid_stop_request_data_single_host_stop_level_process_single_process['target_hosts'][test_hostname]} created, command delivered via heartbeat, and acknowledged"
        )

    @pytest.mark.order(5)
    def test_create_start_profile_request_for_single_host_stop_level_process_multi_process(
        self,
        profile_request_url: str,
        heartbeat_url: str,
        valid_start_request_data_single_host_stop_level_process_multi_process: Dict[
            str, Any
        ],
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
        postgres_connection,
        test_service_name: str,
        test_hostname: str,
    ):
        """Test creating a start profile request with multiple process PIDs, sending heartbeat, and verify database entries."""

        # Step 1: Make API call to create profile request
        response = requests.post(
            profile_request_url,
            headers=credentials,
            json=valid_start_request_data_single_host_stop_level_process_multi_process,
            timeout=10,
            verify=False,
        )

        # Verify API response
        assert (
            response.status_code == 200
        ), f"Expected 200, got {response.status_code}: {response.text}"
        result = response.json()

        # Validate response structure
        assert "success" in result
        assert "message" in result
        assert "request_id" in result
        assert "command_ids" in result
        assert result["success"] is True

        request_id = result["request_id"]
        command_ids = result["command_ids"]

        # Verify request_id is a valid UUID
        assert request_id
        assert len(command_ids) >= 1

        # Step 2: Verify ProfilingRequests table entry
        db_request = get_profiling_request_from_db(postgres_connection, request_id)
        assert db_request is not None, f"Request {request_id} not found in database"

        # Verify request data matches what was sent
        assert (
            db_request["service_name"]
            == valid_start_request_data_single_host_stop_level_process_multi_process[
                "service_name"
            ]
        )
        assert (
            db_request["request_type"]
            == valid_start_request_data_single_host_stop_level_process_multi_process[
                "request_type"
            ]
        )
        assert (
            db_request["duration"]
            == valid_start_request_data_single_host_stop_level_process_multi_process[
                "duration"
            ]
        )
        assert (
            db_request["frequency"]
            == valid_start_request_data_single_host_stop_level_process_multi_process[
                "frequency"
            ]
        )
        assert (
            db_request["profiling_mode"]
            == valid_start_request_data_single_host_stop_level_process_multi_process[
                "profiling_mode"
            ]
        )
        assert db_request["target_hostnames"] == list(
            valid_start_request_data_single_host_stop_level_process_multi_process[
                "target_hosts"
            ].keys()
        )
        assert (
            db_request["stop_level"]
            == valid_start_request_data_single_host_stop_level_process_multi_process[
                "stop_level"
            ]
        )
        assert db_request["status"] == "pending"

        # Verify additional_args JSON
        stored_additional_args = db_request["additional_args"]
        if stored_additional_args:
            assert (
                stored_additional_args
                == valid_start_request_data_single_host_stop_level_process_multi_process[
                    "additional_args"
                ]
            )

        # Verify timestamps
        assert db_request["created_at"] is not None
        assert db_request["updated_at"] is not None

        # Step 3: Verify ProfilingCommands table entries
        db_commands = get_profiling_commands_from_db(
            postgres_connection,
            test_service_name,
            test_hostname,
            command_ids=command_ids,
        )
        assert len(db_commands) >= 1, "No commands found in database"

        # Verify at least one command matches our request
        command_found = False
        for db_command in db_commands:
            if db_command["command_id"] in command_ids:
                command_found = True
                assert db_command["hostname"] == test_hostname
                assert db_command["service_name"] == test_service_name
                assert db_command["command_type"] == "start"
                assert db_command["status"] == "pending"

                # Verify request_ids contains our request
                request_ids_in_command = db_command["request_ids"]
                assert request_id in request_ids_in_command

                # Verify combined_config contains expected fields including multiple PIDs
                combined_config = db_command["combined_config"]
                assert combined_config is not None
                assert (
                    combined_config.get("duration")
                    == valid_start_request_data_single_host_stop_level_process_multi_process[
                        "duration"
                    ]
                )
                assert (
                    combined_config.get("frequency")
                    == valid_start_request_data_single_host_stop_level_process_multi_process[
                        "frequency"
                    ]
                )
                assert (
                    combined_config.get("profiling_mode")
                    == valid_start_request_data_single_host_stop_level_process_multi_process[
                        "profiling_mode"
                    ]
                )
                assert (
                    combined_config.get("pids")
                    == valid_start_request_data_single_host_stop_level_process_multi_process[
                        "target_hosts"
                    ][
                        test_hostname
                    ]
                )

                # Verify multiple PIDs are correctly stored
                expected_pids = valid_start_request_data_single_host_stop_level_process_multi_process[
                    "target_hosts"
                ][
                    test_hostname
                ]
                assert len(combined_config.get("pids", [])) == len(
                    expected_pids
                ), f"Expected {len(expected_pids)} PIDs, got {len(combined_config.get('pids', []))}"
                for pid in expected_pids:
                    assert pid in combined_config.get(
                        "pids", []
                    ), f"PID {pid} not found in command config"

                break

        assert command_found, f"Command with ID in {command_ids} not found in database"

        # Step 4: Send heartbeat and verify command delivery
        print("🔄 Sending heartbeat to retrieve multi-process start commands...")
        received_command = send_heartbeat_and_verify(
            heartbeat_url,
            valid_heartbeat_data,
            credentials,
            postgres_connection,
            expected_command_present=True,
        )

        # Step 5: Verify the received command matches our created command
        assert (
            received_command is not None
        ), "Expected to receive a command via heartbeat"
        assert (
            received_command["command_id"] in command_ids
        ), f"Received command ID {received_command['command_id']} not in expected IDs {command_ids}"

        # Verify command content including multi-process details
        profiling_command = received_command["profiling_command"]
        assert profiling_command["command_type"] == "start"
        assert (
            profiling_command["combined_config"]["duration"]
            == valid_start_request_data_single_host_stop_level_process_multi_process[
                "duration"
            ]
        )
        assert (
            profiling_command["combined_config"]["frequency"]
            == valid_start_request_data_single_host_stop_level_process_multi_process[
                "frequency"
            ]
        )
        assert (
            profiling_command["combined_config"]["profiling_mode"]
            == valid_start_request_data_single_host_stop_level_process_multi_process[
                "profiling_mode"
            ]
        )
        assert (
            profiling_command["combined_config"]["pids"]
            == valid_start_request_data_single_host_stop_level_process_multi_process[
                "target_hosts"
            ][test_hostname]
        )

        # Verify multiple PIDs in delivered command
        delivered_pids = profiling_command["combined_config"]["pids"]
        expected_pids = (
            valid_start_request_data_single_host_stop_level_process_multi_process[
                "target_hosts"
            ][test_hostname]
        )
        assert len(delivered_pids) == len(
            expected_pids
        ), f"Expected {len(expected_pids)} PIDs in delivered command, got {len(delivered_pids)}"
        for pid in expected_pids:
            assert pid in delivered_pids, f"PID {pid} not found in delivered command"

        # Step 6: Send another heartbeat with last_command_id to simulate acknowledgment
        print("🔄 Sending acknowledgment heartbeat for multi-process start command...")
        heartbeat_with_ack = valid_heartbeat_data.copy()
        heartbeat_with_ack["last_command_id"] = received_command["command_id"]

        ack_command = send_heartbeat_and_verify(
            heartbeat_url,
            heartbeat_with_ack,
            credentials,
            postgres_connection,
            expected_command_present=False,  # Should not receive the same command again
        )

        # Should not receive the same command again
        assert (
            ack_command is None
        ), "Should not receive the same command after acknowledgment"

        print(
            f"✅ End-to-end multi-process start integration test passed: Request {request_id} with PIDs {valid_start_request_data_single_host_stop_level_process_multi_process['target_hosts'][test_hostname]} created, command delivered via heartbeat, and acknowledged"
        )

    @pytest.mark.order(6)
    def test_create_stop_profile_request_for_single_host_stop_level_process_multi_process(
        self,
        profile_request_url: str,
        heartbeat_url: str,
        valid_start_request_data_single_host_stop_level_process_multi_process: Dict[
            str, Any
        ],
        valid_stop_request_data_single_host_stop_level_process_multi_process: Dict[
            str, Any
        ],
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
        postgres_connection,
        test_service_name: str,
        test_hostname: str,
    ):
        """Test creating a stop profile request with multiple process PIDs, sending heartbeat, and verify database entries."""

        # Step 1: Make API call to create stop profile request
        response = requests.post(
            profile_request_url,
            headers=credentials,
            json=valid_stop_request_data_single_host_stop_level_process_multi_process,
            timeout=10,
            verify=False,
        )

        # Verify API response
        assert (
            response.status_code == 200
        ), f"Expected 200, got {response.status_code}: {response.text}"
        result = response.json()

        # Validate response structure
        assert "success" in result
        assert "message" in result
        assert "request_id" in result
        assert "command_ids" in result
        assert result["success"] is True

        request_id = result["request_id"]
        command_ids = result["command_ids"]

        # Step 2: Verify ProfilingRequests table entry
        db_request = get_profiling_request_from_db(postgres_connection, request_id)
        assert db_request is not None, f"Request {request_id} not found in database"

        # Verify request data matches what was sent
        assert (
            db_request["service_name"]
            == valid_stop_request_data_single_host_stop_level_process_multi_process[
                "service_name"
            ]
        )
        assert (
            db_request["request_type"]
            == valid_stop_request_data_single_host_stop_level_process_multi_process[
                "request_type"
            ]
        )
        assert db_request["target_hostnames"] == list(
            valid_stop_request_data_single_host_stop_level_process_multi_process[
                "target_hosts"
            ].keys()
        )
        assert (
            db_request["stop_level"]
            == valid_stop_request_data_single_host_stop_level_process_multi_process[
                "stop_level"
            ]
        )
        assert db_request["status"] == "pending"

        # Verify multiple PIDs are correctly stored in database
        expected_pids = [
            pid
            for pid in valid_start_request_data_single_host_stop_level_process_multi_process[
                "target_hosts"
            ][
                test_hostname
            ]
            if pid
            not in valid_stop_request_data_single_host_stop_level_process_multi_process[
                "target_hosts"
            ][test_hostname]
        ]

        # Step 3: Verify ProfilingCommands table entries for stop command
        db_commands = get_profiling_commands_from_db(
            postgres_connection, test_service_name, test_hostname
        )

        # For stop commands, there should be at least one command
        if len(command_ids) > 0:
            assert len(db_commands) >= 1, "No stop commands found in database"

            # Verify stop command properties
            stop_command_found = False
            for db_command in db_commands:
                if db_command["command_id"] in command_ids:
                    stop_command_found = True
                    assert db_command["hostname"] == test_hostname
                    assert db_command["service_name"] == test_service_name
                    assert db_command["command_type"] == "stop"
                    assert db_command["status"] == "pending"

                    # Verify multi-process stop command details
                    combined_config = db_command["combined_config"]
                    assert combined_config is not None
                    assert (
                        combined_config.get("pids")
                        == valid_stop_request_data_single_host_stop_level_process_multi_process[
                            "target_hosts"
                        ][
                            test_hostname
                        ]
                    )
                    assert (
                        combined_config.get("stop_level")
                        == valid_stop_request_data_single_host_stop_level_process_multi_process[
                            "stop_level"
                        ]
                    )

                    # Verify multiple PIDs in command config
                    command_pids = combined_config.get("pids", [])
                    assert len(command_pids) == len(
                        expected_pids
                    ), f"Expected {len(expected_pids)} PIDs in command, got {len(command_pids)}"
                    for pid in expected_pids:
                        assert (
                            pid in command_pids
                        ), f"PID {pid} not found in command config"
                    break

            assert (
                stop_command_found
            ), f"Stop command with ID in {command_ids} not found in database"

        # Step 4: Send heartbeat and verify stop command delivery
        print("🔄 Sending heartbeat to retrieve multi-process stop commands...")
        received_command = send_heartbeat_and_verify(
            heartbeat_url,
            valid_heartbeat_data,
            credentials,
            postgres_connection,
            expected_command_present=True,
        )

        # Step 5: Verify the received command matches our created stop command
        assert (
            received_command is not None
        ), "Expected to receive a stop command via heartbeat"
        assert (
            received_command["command_id"] in command_ids
        ), f"Received command ID {received_command['command_id']} not in expected IDs {command_ids}"

        # Verify stop command content including multi-process details
        profiling_command = received_command["profiling_command"]
        assert profiling_command["command_type"] == "stop"
        assert (
            profiling_command["combined_config"]["stop_level"]
            == valid_stop_request_data_single_host_stop_level_process_multi_process[
                "stop_level"
            ]
        )
        assert (
            profiling_command["combined_config"]["pids"]
            == valid_stop_request_data_single_host_stop_level_process_multi_process[
                "target-hosts"
            ][test_hostname]
        )

        # Verify multiple PIDs in delivered stop command
        delivered_pids = profiling_command["combined_config"]["pids"]
        expected_pids = (
            valid_stop_request_data_single_host_stop_level_process_multi_process[
                "target-hosts"
            ][test_hostname]
        )
        assert len(delivered_pids) == len(
            expected_pids
        ), f"Expected {len(expected_pids)} PIDs in delivered stop command, got {len(delivered_pids)}"
        for pid in expected_pids:
            assert (
                pid in delivered_pids
            ), f"PID {pid} not found in delivered stop command"

        # Step 6: Send acknowledgment heartbeat
        print("🔄 Sending acknowledgment heartbeat for multi-process stop command...")
        heartbeat_with_ack = valid_heartbeat_data.copy()
        heartbeat_with_ack["last_command_id"] = received_command["command_id"]

        ack_command = send_heartbeat_and_verify(
            heartbeat_url,
            heartbeat_with_ack,
            credentials,
            postgres_connection,
            expected_command_present=False,
        )

        assert (
            ack_command is None
        ), "Should not receive the same stop command after acknowledgment"

        print(
            f"✅ End-to-end multi-process stop integration test passed: Stop request {request_id} with PIDs {valid_stop_request_data_single_host_stop_level_process_multi_process['target_hosts'][test_hostname]} created, command delivered via heartbeat, and acknowledged"
        )
