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
from typing import Optional, Literal, Any

from backend.models import CamelModel
from pydantic import BaseModel, model_validator


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
    command_type: str = "start"  # start, stop
    duration: Optional[int] = 60  # seconds
    frequency: Optional[int] = 11  # Hz
    profiling_mode: Optional[Literal["cpu", "allocation", "none"]] = "cpu"  # cpu, allocation, none
    target_hostnames: Optional[list[str]] = None
    pids: Optional[list[int]] = None
    stop_level: Optional[str] = "process"  # process, host (only relevant for stop commands)
    additional_args: Optional[dict[str, Any]] = None

    @model_validator(mode='after')
    def validate_pids_for_process_stop(cls, model):
        """Validate that PIDs are provided when command_type is stop and stop_level is process"""
        if model.command_type == 'stop' and model.stop_level == 'process':
            if not model.pids or len(model.pids) == 0:
                raise ValueError('At least one PID must be provided when command_type is "stop" and stop_level is "process"')

        # Validate the duration
        if model.duration and model.duration <= 0:
            raise ValueError("Duration must be a positive integer (seconds)")

        # Validate the frequency
        if model.frequency and model.frequency <= 0:
            raise ValueError("Frequency must be a positive integer (Hz)")

        return model


class ProfilingResponse(BaseModel):
    """Response model for profiling requests"""
    success: bool
    message: str
    request_id: Optional[str] = None
    command_ids: Optional[list[str]] = None  # List of command IDs for agent idempotency
    estimated_completion_time: Optional[datetime] = None


class HeartbeatRequest(BaseModel):
    """Model for host heartbeat request"""
    ip_address: str
    hostname: str
    service_name: str
    last_command_id: Optional[str] = None
    status: Optional[str] = "active"  # active, idle, error
    timestamp: Optional[datetime] = None


class HeartbeatResponse(BaseModel):
    """Response model for heartbeat requests"""
    success: bool
    message: str
    profiling_command: Optional[dict[str, Any]] = None  # Changed from profiling_request to profiling_command
    command_id: Optional[str] = None


class ProfilingCommand(BaseModel):
    """Model for combined profiling command sent to hosts"""
    command_id: str
    command_type: str  # start, stop
    hostname: str
    service_name: str
    combined_config: dict[str, Any]  # Combined configuration from multiple requests
    request_ids: list[str]  # List of request IDs that make up this command
    created_at: datetime
    status: str  # pending, sent, completed, failed


class CommandCompletionRequest(BaseModel):
    """Model for reporting command completion"""
    command_id: str
    hostname: str
    status: Literal["completed", "failed"]  # completed, failed
    execution_time: Optional[int] = None  # seconds
    error_message: Optional[str] = None
    results_path: Optional[str] = None  # S3 path or local path to results
