#!/usr/bin/env python3
"""
Unit tests for the POST /api/metrics/heartbeat endpoint.

This module contains pytest-based unit tests that validate:
1. Valid heartbeat requests with all required fields
2. Invalid requests with missing required fields
3. Invalid requests with wrong data types
4. Edge cases and boundary conditions
5. Response structure validation
6. Command delivery through heartbeat responses
"""

from datetime import datetime
from typing import Any, Dict

import pytest
import requests


@pytest.fixture
def heartbeat_url(backend_base_url) -> str:
    """Get the full URL for the heartbeat endpoint."""
    return f"{backend_base_url}/api/metrics/heartbeat"


@pytest.fixture
def valid_heartbeat_data() -> Dict[str, Any]:
    """Provide valid heartbeat data for testing."""
    return {
        "hostname": "test-host",
        "ip_address": "127.0.0.1",
        "service_name": "test-service",
        "status": "active",
        "timestamp": datetime.now().isoformat(),
        "last_command_id": None,
        "available_pids": None,
    }


@pytest.fixture
def valid_heartbeat_data_with_pids() -> Dict[str, Any]:
    """Provide valid heartbeat data with available_pids for testing."""
    return {
        "hostname": "test-host-with-pids",
        "ip_address": "127.0.0.1",
        "service_name": "test-service",
        "status": "active",
        "timestamp": datetime.now().isoformat(),
        "last_command_id": None,
        "available_pids": {
            "python": [1234, 5678],
            "java": [9999, 8888],
            "nodejs": [7777]
        },
    }


class TestHeartbeatEndpoint:
    """Test class for the heartbeat endpoint."""

    def test_valid_heartbeat_request(
        self,
        heartbeat_url: str,
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test a valid heartbeat request."""
        response = requests.post(
            heartbeat_url,
            headers=credentials,
            json=valid_heartbeat_data,
            timeout=10,
            verify=False,
        )

        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

        result = response.json()
        assert "message" in result, "Response should contain 'message' field"

        # Check for optional command fields
        if "profiling_command" in result:
            assert "command_id" in result, "Response with command should contain 'command_id' field"

    def test_heartbeat_with_last_command_id(
        self,
        heartbeat_url: str,
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test heartbeat request with last_command_id."""
        heartbeat_data = valid_heartbeat_data.copy()
        heartbeat_data["last_command_id"] = "test-command-123"

        response = requests.post(
            heartbeat_url,
            headers=credentials,
            json=heartbeat_data,
            timeout=10,
            verify=False,
        )

        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

        result = response.json()
        assert "message" in result

    def test_missing_hostname(
        self,
        heartbeat_url: str,
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test request with missing hostname field."""
        invalid_request = valid_heartbeat_data.copy()
        del invalid_request["hostname"]

        response = requests.post(
            heartbeat_url,
            headers=credentials,
            json=invalid_request,
            timeout=10,
            verify=False,
        )

        assert response.status_code in [
            400,
            422,
        ], f"Expected 400 or 422, got {response.status_code}"

    def test_missing_ip_address(
        self,
        heartbeat_url: str,
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test request with missing ip_address field."""
        invalid_request = valid_heartbeat_data.copy()
        del invalid_request["ip_address"]

        response = requests.post(
            heartbeat_url,
            headers=credentials,
            json=invalid_request,
            timeout=10,
            verify=False,
        )

        assert response.status_code in [
            400,
            422,
        ], f"Expected 400 or 422, got {response.status_code}"

    def test_missing_service_name(
        self,
        heartbeat_url: str,
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test request with missing service_name field."""
        invalid_request = valid_heartbeat_data.copy()
        del invalid_request["service_name"]

        response = requests.post(
            heartbeat_url,
            headers=credentials,
            json=invalid_request,
            timeout=10,
            verify=False,
        )

        assert response.status_code in [
            400,
            422,
        ], f"Expected 400 or 422, got {response.status_code}"

    def test_missing_status(
        self,
        heartbeat_url: str,
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test request with missing status field."""
        invalid_request = valid_heartbeat_data.copy()
        del invalid_request["status"]

        response = requests.post(
            heartbeat_url,
            headers=credentials,
            json=invalid_request,
            timeout=10,
            verify=False,
        )

        # Status might have a default value, so it could be valid
        assert response.status_code in [
            200,
            400,
            422,
        ], f"Unexpected status code: {response.status_code}"

    def test_empty_hostname(
        self,
        heartbeat_url: str,
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test request with empty hostname."""
        invalid_request = valid_heartbeat_data.copy()
        invalid_request["hostname"] = ""

        response = requests.post(
            heartbeat_url,
            headers=credentials,
            json=invalid_request,
            timeout=10,
            verify=False,
        )

        assert response.status_code in [
            400,
            422,
        ], f"Expected 400 or 422, got {response.status_code}"

    def test_empty_service_name(
        self,
        heartbeat_url: str,
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test request with empty service_name."""
        invalid_request = valid_heartbeat_data.copy()
        invalid_request["service_name"] = ""

        response = requests.post(
            heartbeat_url,
            headers=credentials,
            json=invalid_request,
            timeout=10,
            verify=False,
        )

        assert response.status_code in [
            400,
            422,
        ], f"Expected 400 or 422, got {response.status_code}"

    def test_invalid_ip_address_format(
        self,
        heartbeat_url: str,
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test request with invalid IP address format."""
        invalid_request = valid_heartbeat_data.copy()
        invalid_request["ip_address"] = "invalid.ip.address"

        response = requests.post(
            heartbeat_url,
            headers=credentials,
            json=invalid_request,
            timeout=10,
            verify=False,
        )

        # Depending on validation, this might be accepted or rejected
        assert response.status_code in [
            200,
            400,
            422,
        ], f"Unexpected status code: {response.status_code}"

    def test_invalid_status_value(
        self,
        heartbeat_url: str,
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test request with invalid status value."""
        invalid_request = valid_heartbeat_data.copy()
        invalid_request["status"] = "invalid_status"

        response = requests.post(
            heartbeat_url,
            headers=credentials,
            json=invalid_request,
            timeout=10,
            verify=False,
        )

        # Might be accepted or rejected depending on validation
        assert response.status_code in [
            200,
            400,
            422,
        ], f"Unexpected status code: {response.status_code}"

    def test_none_values(
        self,
        heartbeat_url: str,
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test request with None values for required fields."""
        invalid_request = valid_heartbeat_data.copy()
        invalid_request["hostname"] = None

        response = requests.post(
            heartbeat_url,
            headers=credentials,
            json=invalid_request,
            timeout=10,
            verify=False,
        )

        assert response.status_code in [
            400,
            422,
        ], f"Expected 400 or 422, got {response.status_code}"

    def test_invalid_timestamp_format(
        self,
        heartbeat_url: str,
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test request with invalid timestamp format."""
        invalid_request = valid_heartbeat_data.copy()
        invalid_request["timestamp"] = "invalid-timestamp"

        response = requests.post(
            heartbeat_url,
            headers=credentials,
            json=invalid_request,
            timeout=10,
            verify=False,
        )

        # Timestamp validation depends on implementation
        assert response.status_code in [
            200,
            400,
            422,
        ], f"Unexpected status code: {response.status_code}"

    def test_missing_timestamp(
        self,
        heartbeat_url: str,
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test request with missing timestamp (should use server time)."""
        heartbeat_data = valid_heartbeat_data.copy()
        del heartbeat_data["timestamp"]

        response = requests.post(
            heartbeat_url,
            headers=credentials,
            json=heartbeat_data,
            timeout=10,
            verify=False,
        )

        # Missing timestamp should be handled gracefully
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

    def test_ipv6_address(
        self,
        heartbeat_url: str,
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test request with IPv6 address."""
        ipv6_request = valid_heartbeat_data.copy()
        ipv6_request["ip_address"] = "::1"

        response = requests.post(
            heartbeat_url,
            headers=credentials,
            json=ipv6_request,
            timeout=10,
            verify=False,
        )

        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

    def test_long_hostname(
        self,
        heartbeat_url: str,
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test request with very long hostname."""
        long_hostname_request = valid_heartbeat_data.copy()
        long_hostname_request["hostname"] = "a" * 255  # Very long hostname

        response = requests.post(
            heartbeat_url,
            headers=credentials,
            json=long_hostname_request,
            timeout=10,
            verify=False,
        )

        # Should either accept or reject based on hostname length limits
        assert response.status_code in [
            200,
            400,
            422,
        ], f"Unexpected status code: {response.status_code}"

    def test_malformed_json(self, heartbeat_url: str, credentials: Dict[str, Any]):
        """Test request with malformed JSON."""
        response = requests.post(
            heartbeat_url,
            data="{'invalid': json}",  # Invalid JSON
            headers={**credentials, "Content-Type": "application/json"},
            timeout=10,
            verify=False,
        )

        assert response.status_code in [
            400,
            422,
        ], f"Expected 400 or 422, got {response.status_code}"

    def test_empty_request_body(self, heartbeat_url: str, credentials: Dict[str, Any]):
        """Test request with empty body."""
        response = requests.post(heartbeat_url, headers=credentials, json={}, timeout=10, verify=False)

        assert response.status_code in [
            400,
            422,
        ], f"Expected 400 or 422, got {response.status_code}"

    def test_additional_fields(
        self,
        heartbeat_url: str,
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test that additional fields are handled gracefully."""
        extended_request = valid_heartbeat_data.copy()
        extended_request["extra_field"] = "extra_value"
        extended_request["metadata"] = {"key": "value"}

        response = requests.post(
            heartbeat_url,
            headers=credentials,
            json=extended_request,
            timeout=10,
            verify=False,
        )

        # Additional fields should be ignored, not cause errors
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

    def test_status_variations(
        self,
        heartbeat_url: str,
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test different valid status values."""
        valid_statuses = ["active", "inactive", "error", "pending"]

        for status in valid_statuses:
            status_request = valid_heartbeat_data.copy()
            status_request["status"] = status
            status_request["hostname"] = f"test-host-{status}"  # Unique hostname

            response = requests.post(
                heartbeat_url,
                headers=credentials,
                json=status_request,
                timeout=10,
                verify=False,
            )

            assert response.status_code in [
                200,
                400,
                422,
            ], f"Failed with status: {status}, code: {response.status_code}"

    def test_multiple_heartbeats_same_host(
        self,
        heartbeat_url: str,
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test multiple heartbeats from the same host."""
        for i in range(3):
            heartbeat_data = valid_heartbeat_data.copy()
            heartbeat_data["timestamp"] = datetime.now().isoformat()

            response = requests.post(
                heartbeat_url,
                headers=credentials,
                json=heartbeat_data,
                timeout=10,
                verify=False,
            )

            assert response.status_code == 200, f"Heartbeat {i+1} failed: {response.status_code}: {response.text}"


class TestHeartbeatResponseStructure:
    """Test class for validating heartbeat response structure and content."""

    def test_response_content_type(
        self,
        heartbeat_url: str,
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test that response has correct content type."""
        response = requests.post(
            heartbeat_url,
            headers=credentials,
            json=valid_heartbeat_data,
            timeout=10,
            verify=False,
        )

        if response.status_code == 200:
            assert "application/json" in response.headers.get("content-type", "")

    def test_response_structure_consistency(
        self,
        heartbeat_url: str,
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test that multiple heartbeat requests return consistent response structure."""
        responses = []

        for i in range(3):
            heartbeat_data = valid_heartbeat_data.copy()
            heartbeat_data["hostname"] = f"test-host-{i}"

            response = requests.post(
                heartbeat_url,
                headers=credentials,
                json=heartbeat_data,
                timeout=10,
                verify=False,
            )

            if response.status_code == 200:
                responses.append(response.json())

        # All successful responses should have at least the message field
        for response_data in responses:
            assert "message" in response_data, "All responses should contain 'message' field"

    def test_command_response_structure(
        self,
        heartbeat_url: str,
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test response structure when commands are present."""
        response = requests.post(
            heartbeat_url,
            headers=credentials,
            json=valid_heartbeat_data,
            timeout=10,
            verify=False,
        )

        if response.status_code == 200:
            result = response.json()

        assert "command_id" in result, "Response should contain 'command_id' field"
        if result.get("command_id", None) is not None:
            assert isinstance(result["command_id"], str), "command_id should be a string"

        assert "profiling_command" in result, "Response should contain 'profiling_command' field"
        if result.get("profiling_command", None) is not None:
            assert isinstance(result["profiling_command"], dict), "profiling_command should be a dictionary"

            # Validate command structure
            command = result["profiling_command"]
            expected_fields = ["command_type"]  # Minimum expected fields
            for field in expected_fields:
                if field in command:
                    assert command[field] is not None, f"Command field {field} should not be None"

    def test_heartbeat_idempotency(
        self,
        heartbeat_url: str,
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test that heartbeat requests are idempotent."""
        # Send initial heartbeat
        first_response = requests.post(
            heartbeat_url,
            headers=credentials,
            json=valid_heartbeat_data,
            timeout=10,
            verify=False,
        )

        # Send identical heartbeat
        second_response = requests.post(
            heartbeat_url,
            headers=credentials,
            json=valid_heartbeat_data,
            timeout=10,
            verify=False,
        )

        assert first_response.status_code == second_response.status_code

        if first_response.status_code == 200 and second_response.status_code == 200:
            first_result = first_response.json()
            second_result = second_response.json()

            # Both should contain message field
            assert "message" in first_result
            assert "message" in second_result

    def test_heartbeat_timestamp_handling(
        self,
        heartbeat_url: str,
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test various timestamp formats."""
        timestamp_formats = [
            datetime.now().isoformat(),
            datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ]

        for timestamp in timestamp_formats:
            heartbeat_data = valid_heartbeat_data.copy()
            heartbeat_data["timestamp"] = timestamp
            heartbeat_data["hostname"] = f"test-host-{timestamp}"  # Unique hostname

            response = requests.post(
                heartbeat_url,
                headers=credentials,
                json=heartbeat_data,
                timeout=10,
                verify=False,
            )

            # Should handle various timestamp formats gracefully
            assert response.status_code in [
                200,
                400,
                422,
            ], f"Failed with timestamp format: {timestamp}"

    def test_heartbeat_response_with_available_pids(
        self,
        heartbeat_url: str,
        valid_heartbeat_data_with_pids: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test response structure when available_pids are included in request."""
        response = requests.post(
            heartbeat_url,
            headers=credentials,
            json=valid_heartbeat_data_with_pids,
            timeout=10,
            verify=False,
        )

        if response.status_code == 200:
            result = response.json()
            assert "success" in result, "Response should contain 'success' field"
            assert "message" in result, "Response should contain 'message' field"
            assert isinstance(result["success"], bool), "success should be a boolean"
            assert isinstance(result["message"], str), "message should be a string"

    def test_heartbeat_response_success_and_message_always_present(
        self,
        heartbeat_url: str,
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test that success and message fields are always present in valid responses."""
        variations = [
            {},  # No optional fields
            {"available_pids": {"python": [1234]}},  # With available_pids
            {"last_command_id": "test-command-123"},  # With last_command_id
            {"timestamp": None},  # Without timestamp
        ]

        for i, extra_fields in enumerate(variations):
            test_data = valid_heartbeat_data.copy()
            test_data["hostname"] = f"test-host-response-{i}"
            test_data.update(extra_fields)

            response = requests.post(
                heartbeat_url,
                headers=credentials,
                json=test_data,
                timeout=10,
                verify=False,
            )

            if response.status_code == 200:
                result = response.json()
                assert "success" in result, f"Missing 'success' in variation {i}: {extra_fields}"
                assert "message" in result, f"Missing 'message' in variation {i}: {extra_fields}"
                assert isinstance(result["success"], bool), f"success not boolean in variation {i}"
                assert isinstance(result["message"], str), f"message not string in variation {i}"

    def test_heartbeat_response_optional_fields_when_present(
        self,
        heartbeat_url: str,
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test that optional response fields are properly typed when present."""
        response = requests.post(
            heartbeat_url,
            headers=credentials,
            json=valid_heartbeat_data,
            timeout=10,
            verify=False,
        )

        if response.status_code == 200:
            result = response.json()

            # Check optional fields if they exist
            if "command_id" in result:
                if result["command_id"] is not None:
                    assert isinstance(result["command_id"], str), "command_id should be a string when present"

            if "profiling_command" in result:
                if result["profiling_command"] is not None:
                    assert isinstance(result["profiling_command"], dict), "profiling_command should be a dict when present"
                    
                    # Check profiling_command structure
                    command = result["profiling_command"]
                    if "command_type" in command:
                        assert isinstance(command["command_type"], str), "command_type should be a string"
                    if "combined_config" in command:
                        assert isinstance(command["combined_config"], dict), "combined_config should be a dict"

    def test_heartbeat_profiling_command_structure_validation(
        self,
        heartbeat_url: str,
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test detailed validation of profiling_command structure when present."""
        response = requests.post(
            heartbeat_url,
            headers=credentials,
            json=valid_heartbeat_data,
            timeout=10,
            verify=False,
        )

        if response.status_code == 200:
            result = response.json()
            
            # If there's a profiling command, validate its structure thoroughly
            if result.get("profiling_command") is not None:
                command = result["profiling_command"]
                
                # Required field in profiling command
                assert "command_type" in command, "profiling_command should have command_type"
                assert command["command_type"] in ["start", "stop"], (
                    f"command_type should be 'start' or 'stop', got: {command.get('command_type')}"
                )
                
                # If combined_config exists, validate its structure
                if "combined_config" in command and command["combined_config"] is not None:
                    config = command["combined_config"]
                    assert isinstance(config, dict), "combined_config should be a dictionary"
                    
                    # Check common config fields if they exist
                    optional_config_fields = {
                        "continuous": bool,
                        "duration": int,
                        "frequency": int,
                        "profiling_mode": str,
                        "pids": list,
                        "additional_args": dict,
                        "stop_level": str,
                    }
                    
                    for field, expected_type in optional_config_fields.items():
                        if field in config and config[field] is not None:
                            assert isinstance(config[field], expected_type), (
                                f"config field '{field}' should be {expected_type.__name__}, "
                                f"got {type(config[field]).__name__}"
                            )


class TestHeartbeatAvailablePidsFeatures:
    """Test class for available_pids functionality in heartbeat endpoint."""

    def test_heartbeat_with_available_pids(
        self,
        heartbeat_url: str,
        valid_heartbeat_data_with_pids: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test heartbeat request with available_pids."""
        response = requests.post(
            heartbeat_url,
            headers=credentials,
            json=valid_heartbeat_data_with_pids,
            timeout=10,
            verify=False,
        )

        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

        result = response.json()
        assert "message" in result, "Response should contain 'message' field"

    def test_heartbeat_with_empty_available_pids(
        self,
        heartbeat_url: str,
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test heartbeat request with empty available_pids."""
        heartbeat_data = valid_heartbeat_data.copy()
        heartbeat_data["available_pids"] = {}

        response = requests.post(
            heartbeat_url,
            headers=credentials,
            json=heartbeat_data,
            timeout=10,
            verify=False,
        )

        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

    def test_heartbeat_with_single_language_pids(
        self,
        heartbeat_url: str,
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test heartbeat request with PIDs for a single language."""
        heartbeat_data = valid_heartbeat_data.copy()
        heartbeat_data["hostname"] = "test-host-single-lang"
        heartbeat_data["available_pids"] = {"python": [1234, 5678, 9999]}

        response = requests.post(
            heartbeat_url,
            headers=credentials,
            json=heartbeat_data,
            timeout=10,
            verify=False,
        )

        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

    def test_heartbeat_with_invalid_pid_types(
        self,
        heartbeat_url: str,
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test heartbeat request with invalid PID types."""
        heartbeat_data = valid_heartbeat_data.copy()
        heartbeat_data["hostname"] = "test-host-invalid-pids"
        heartbeat_data["available_pids"] = {"python": ["not-a-pid", "also-invalid"]}

        response = requests.post(
            heartbeat_url,
            headers=credentials,
            json=heartbeat_data,
            timeout=10,
            verify=False,
        )

        # Should either accept and convert or reject
        assert response.status_code in [
            200,
            400,
            422,
        ], f"Unexpected status code: {response.status_code}"

    def test_heartbeat_with_empty_pid_lists(
        self,
        heartbeat_url: str,
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test heartbeat request with empty PID lists."""
        heartbeat_data = valid_heartbeat_data.copy()
        heartbeat_data["hostname"] = "test-host-empty-pids"
        heartbeat_data["available_pids"] = {"python": [], "java": []}

        response = requests.post(
            heartbeat_url,
            headers=credentials,
            json=heartbeat_data,
            timeout=10,
            verify=False,
        )

        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

    def test_heartbeat_with_merge_pids_false(
        self,
        heartbeat_url: str,
        valid_heartbeat_data_with_pids: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test heartbeat request with mergePids=false query parameter."""
        response = requests.post(
            f"{heartbeat_url}?mergePids=false",
            headers=credentials,
            json=valid_heartbeat_data_with_pids,
            timeout=10,
            verify=False,
        )

        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

    def test_heartbeat_with_merge_pids_true(
        self,
        heartbeat_url: str,
        valid_heartbeat_data_with_pids: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test heartbeat request with mergePids=true query parameter."""
        response = requests.post(
            f"{heartbeat_url}?mergePids=true",
            headers=credentials,
            json=valid_heartbeat_data_with_pids,
            timeout=10,
            verify=False,
        )

        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

    def test_heartbeat_merge_pids_behavior(
        self,
        heartbeat_url: str,
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test the merge behavior of PIDs across multiple heartbeats."""
        # First heartbeat with initial PIDs
        initial_data = valid_heartbeat_data.copy()
        initial_data["hostname"] = "test-host-merge-behavior"
        initial_data["available_pids"] = {"python": [1111, 2222]}

        response1 = requests.post(
            f"{heartbeat_url}?mergePids=false",
            headers=credentials,
            json=initial_data,
            timeout=10,
            verify=False,
        )
        assert response1.status_code == 200

        # Verify PIDs after first heartbeat (mergePids=false should only have initial PIDs)
        host_status_url = heartbeat_url.replace("/heartbeat", "/profiling/host_status")
        status_response1 = requests.get(
            host_status_url,
            headers=credentials,
            timeout=10,
            verify=False,
        )
        assert status_response1.status_code == 200
        
        hosts_status1 = status_response1.json()
        test_host1 = None
        for host in hosts_status1:
            if host["hostname"] == "test-host-merge-behavior":
                test_host1 = host
                break
        
        assert test_host1 is not None, "Test host not found in host status response after first heartbeat"
        
        # After first heartbeat with mergePids=false, should only have initial PIDs
        expected_initial_pids = {"python": [1111, 2222]}
        actual_pids1 = test_host1.get("available_pids", {})
        
        assert "python" in actual_pids1, "Python PIDs should be present after first heartbeat"
        assert set(actual_pids1["python"]) == set(expected_initial_pids["python"]), f"Expected python PIDs {expected_initial_pids['python']}, got {actual_pids1['python']}"
        assert "java" not in actual_pids1 or not actual_pids1["java"], "Java PIDs should not be present after first heartbeat"

        # Second heartbeat with additional PIDs and merge=true
        additional_data = initial_data.copy()
        additional_data["available_pids"] = {"python": [3333], "java": [4444]}

        response2 = requests.post(
            f"{heartbeat_url}?mergePids=true",
            headers=credentials,
            json=additional_data,
            timeout=10,
            verify=False,
        )
        assert response2.status_code == 200

        # Verify PIDs after second heartbeat (mergePids=true should have merged PIDs)
        status_response2 = requests.get(
            host_status_url,
            headers=credentials,
            timeout=10,
            verify=False,
        )
        assert status_response2.status_code == 200
        
        hosts_status2 = status_response2.json()
        test_host2 = None
        for host in hosts_status2:
            if host["hostname"] == "test-host-merge-behavior":
                test_host2 = host
                break
        
        assert test_host2 is not None, "Test host not found in host status response after second heartbeat"
        
        # After mergePids=true, should have merged PIDs: python=[1111,2222,3333], java=[4444]
        expected_merged_pids = {"python": [1111, 2222, 3333], "java": [4444]}
        actual_pids2 = test_host2.get("available_pids", {})
        
        assert "python" in actual_pids2, "Python PIDs should be present after merge"
        assert "java" in actual_pids2, "Java PIDs should be present after merge"
        assert set(actual_pids2["python"]) == set(expected_merged_pids["python"]), f"Expected python PIDs {expected_merged_pids['python']}, got {actual_pids2['python']}"
        assert set(actual_pids2["java"]) == set(expected_merged_pids["java"]), f"Expected java PIDs {expected_merged_pids['java']}, got {actual_pids2['java']}"

    def test_heartbeat_negative_pids(
        self,
        heartbeat_url: str,
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test heartbeat request with negative PIDs."""
        heartbeat_data = valid_heartbeat_data.copy()
        heartbeat_data["hostname"] = "test-host-negative-pids"
        heartbeat_data["available_pids"] = {"python": [-1, -999]}

        response = requests.post(
            heartbeat_url,
            headers=credentials,
            json=heartbeat_data,
            timeout=10,
            verify=False,
        )

        # Negative PIDs might be rejected
        assert response.status_code in [
            200,
            400,
            422,
        ], f"Unexpected status code: {response.status_code}"

    def test_heartbeat_very_large_pids(
        self,
        heartbeat_url: str,
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test heartbeat request with very large PIDs."""
        heartbeat_data = valid_heartbeat_data.copy()
        heartbeat_data["hostname"] = "test-host-large-pids"
        heartbeat_data["available_pids"] = {"python": [999999999, 2147483647]}

        response = requests.post(
            heartbeat_url,
            headers=credentials,
            json=heartbeat_data,
            timeout=10,
            verify=False,
        )

        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

    def test_heartbeat_duplicate_pids_in_same_language(
        self,
        heartbeat_url: str,
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test heartbeat request with duplicate PIDs in the same language."""
        heartbeat_data = valid_heartbeat_data.copy()
        heartbeat_data["hostname"] = "test-host-duplicate-pids"
        heartbeat_data["available_pids"] = {"python": [1234, 1234, 5678, 1234]}

        response = requests.post(
            heartbeat_url,
            headers=credentials,
            json=heartbeat_data,
            timeout=10,
            verify=False,
        )

        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

    def test_heartbeat_many_languages_many_pids(
        self,
        heartbeat_url: str,
        valid_heartbeat_data: Dict[str, Any],
        credentials: Dict[str, Any],
    ):
        """Test heartbeat request with many languages and many PIDs."""
        heartbeat_data = valid_heartbeat_data.copy()
        heartbeat_data["hostname"] = "test-host-many-langs"
        heartbeat_data["available_pids"] = {
            "python": list(range(1000, 1050)),
            "java": list(range(2000, 2020)),
            "nodejs": list(range(3000, 3010)),
            "go": list(range(4000, 4005)),
            "rust": [5001, 5002],
            "cpp": [6001],
        }

        response = requests.post(
            heartbeat_url,
            headers=credentials,
            json=heartbeat_data,
            timeout=10,
            verify=False,
        )

        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"