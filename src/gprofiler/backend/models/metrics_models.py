#
# Copyright (C) 2023 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

from datetime import datetime
from typing import Any, Dict, List, Optional

from backend.models import CamelModel
from pydantic import BaseModel, root_validator, validator


class SampleCount(BaseModel):
    samples: int
    time: datetime


class InstanceTypeCount(BaseModel):
    instance_count: int
    instance_type: str


class Metric(BaseModel):
    avg_cpu: Optional[float]
    avg_memory: Optional[float]
    max_cpu: Optional[float]
    max_memory: Optional[float]
    percentile_memory: Optional[float]


class CpuTrend(BaseModel):
    avg_cpu: float
    max_cpu: float
    avg_memory: float
    max_memory: float
    compared_avg_cpu: float
    compared_max_cpu: float
    compared_avg_memory: float
    compared_max_memory: float


class CpuMetric(BaseModel):
    cpu_percentage: float
    time: datetime


class MetricNodesAndCores(BaseModel):
    avg_nodes: float
    max_nodes: float
    avg_cores: float
    max_cores: float
    time: datetime


class MetricSummary(Metric):
    uniq_hostnames: Optional[int]


class MetricGraph(Metric):
    uniq_hostnames: Optional[int]
    time: datetime


class MetricNodesCoresSummary(BaseModel):
    avg_cores: float
    avg_nodes: Optional[float]


class MetricK8s(CamelModel):
    name: str
    samples: Optional[int] = None
    cores: Optional[int] = None
    cpu: Optional[float] = None
    memory: Optional[float] = None


class HTMLMetadata(CamelModel):
    content: str


class ProfilingRequest(BaseModel):
    """Model for profiling request parameters"""

    service_name: str
    request_type: str  # "start" or "stop"
    continuous: Optional[bool] = False
    duration: Optional[int] = 60
    frequency: Optional[int] = 11
    profiling_mode: Optional[str] = "cpu"  # "cpu", "allocation", "none"
    target_hosts: Dict[str, Optional[List[int]]]
    stop_level: Optional[str] = "process"  # "process" or "host"
    additional_args: Optional[Dict[str, Any]] = None
    dry_run: Optional[bool] = False

    @validator("request_type")
    def validate_request_type(cls, v):
        if v not in ["start", "stop"]:
            raise ValueError('request_type must be "start" or "stop"')
        return v

    @validator("profiling_mode")
    def validate_profiling_mode(cls, v):
        if v not in ["cpu", "allocation", "none"]:
            raise ValueError('profiling_mode must be "cpu", "allocation", or "none"')
        return v

    @validator("target_hosts")
    def validate_target_hosts(cls, v):
        """Validate that target_hosts is not empty."""
        if len(v) == 0:
            raise ValueError("target_hosts cannot be empty")
        return v

    @validator("stop_level")
    def validate_stop_level(cls, v):
        if v not in ["process", "host"]:
            raise ValueError('stop_level must be "process" or "host"')
        return v

    @validator("duration")
    def validate_duration(cls, v):
        if v is not None and v <= 0:
            raise ValueError("Duration must be a positive integer (seconds)")
        return v

    @validator("frequency")
    def validate_frequency(cls, v):
        if v is not None and v <= 0:
            raise ValueError("Frequency must be a positive integer (Hz)")
        return v

    @root_validator
    def validate_profile_request(cls, values):
        """Validate that PIDs are provided when request_type is stop and stop_level is process"""
        request_type = values.get("request_type")
        stop_level = values.get("stop_level")
        target_hosts = values.get("target_hosts")

        if request_type == "stop" and stop_level == "process":
            # Check if PIDs are provided in target_hosts mapping
            has_pids = target_hosts and any(pids for pids in target_hosts.values() if pids)
            if not has_pids:
                raise ValueError(
                    'At least one PID must be provided when request_type is "stop" and stop_level is "process"'
                )

        # Validate if a process id is provided when request_type is stop and stop_level is host, if so raises
        if request_type == "stop" and stop_level == "host":
            has_pids = target_hosts and any(pids for pids in target_hosts.values() if pids is not None)
            if has_pids:
                raise ValueError('No PIDs should be provided when request_type is "stop" and stop_level is "host"')

        return values


class ProfilingResponse(BaseModel):
    """Response model for profiling requests"""

    success: bool
    message: str
    request_id: Optional[str] = None
    command_ids: Optional[List[str]] = None
    estimated_completion_time: Optional[datetime] = None


class BulkProfilingRequest(BaseModel):
    """Model for bulk profiling request parameters"""
    
    requests: List[ProfilingRequest]
    dry_run: Optional[bool] = False
    
    @validator("requests")
    def validate_requests_not_empty(cls, v):
        if len(v) == 0:
            raise ValueError("requests list cannot be empty")
        return v
    
    @root_validator
    def apply_bulk_dry_run(cls, values):
        """Apply bulk-level dry_run to all individual requests, overwriting their dry_run values"""
        bulk_dry_run = values.get("dry_run", False)
        requests = values.get("requests", [])
        
        # Overwrite dry_run for each individual request with the bulk-level dry_run
        for request in requests:
            request.dry_run = bulk_dry_run
        
        return values


class BulkProfilingRequestResult(BaseModel):
    """Individual result for a bulk profiling request item"""
    
    index: int
    service_name: str
    success: bool
    response: Optional[ProfilingResponse] = None
    error: Optional[str] = None


class BulkProfilingResponse(BaseModel):
    """Response model for bulk profiling requests"""
    
    total_submitted: int
    successful_count: int
    failed_count: int
    results: List[BulkProfilingRequestResult]


class HeartbeatRequest(BaseModel):
    """Model for host heartbeat request"""

    ip_address: str
    hostname: str
    service_name: str
    last_command_id: Optional[str] = None
    received_command_ids: Optional[List[str]] = None
    executed_command_ids: Optional[List[str]] = None
    status: str = "active"  # active, idle, error
    timestamp: Optional[datetime] = None
    perf_supported_events: Optional[List[str]] = None  # Changed to match agent format


class HeartbeatResponse(BaseModel):
    """Response model for heartbeat requests"""

    success: bool
    message: str
    profiling_command: Optional[Dict[str, Any]] = None
    command_id: Optional[str] = None


class CommandCompletionRequest(BaseModel):
    """Model for reporting command completion"""

    command_id: str
    hostname: str
    status: str
    execution_time: Optional[int] = None
    error_message: Optional[str] = None
    results_path: Optional[str] = None

    @validator("status")
    def validate_status(cls, v):
        if v not in ["completed", "failed"]:
            raise ValueError(f"invalid status: {v}. Must be 'completed' or 'failed'.")
        return v


class ProfilingHostStatusRequest(BaseModel):
    """Model for profiling host status request parameters"""
    
    service_name: Optional[List[str]] = None
    exact_match: bool = False
    hostname: Optional[List[str]] = None
    ip_address: Optional[List[str]] = None
    profiling_status: Optional[List[str]] = None
    command_type: Optional[List[str]] = None
    pids: Optional[List[int]] = None


class ProfilingHostStatus(BaseModel):
    id: int
    service_name: str
    hostname: str
    ip_address: str
    pids: List[int]
    command_type: str
    profiling_status: str
    heartbeat_timestamp: datetime


class ProfilingHostStatusResponse(BaseModel):
    """Response model for profiling host status with counts"""
    hosts: List[ProfilingHostStatus]
    active_count: int  # Hosts with heartbeat in last 2 minutes
    total_count: int   # total number of hosts for the selected service
