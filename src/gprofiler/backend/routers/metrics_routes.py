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

import math
import uuid
from datetime import datetime, timedelta
from logging import getLogger
from typing import List, Optional

from backend.models.filters_models import FilterTypes
from backend.models.flamegraph_models import FGParamsBaseModel
from backend.models.metrics_models import (
    CpuMetric,
    CpuTrend,
    InstanceTypeCount,
    MetricGraph,
    MetricNodesAndCores,
    MetricNodesCoresSummary,
    MetricSummary,
    SampleCount,
    HTMLMetadata,
)
from backend.utils.filters_utils import get_rql_first_eq_key, get_rql_only_for_one_key
from backend.utils.request_utils import flamegraph_base_request_params, get_metrics_response, get_query_response
from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import Response
from gprofiler_dev.postgres.db_manager import DBManager

logger = getLogger(__name__)
router = APIRouter()


class ProfilingRequest(BaseModel):
    """Model for profiling request parameters"""
    service_name: str
    request_type: str = "start"  # start, stop
    duration: Optional[int] = 60  # seconds
    frequency: Optional[int] = 11  # Hz
    profiling_mode: Optional[str] = "cpu"  # cpu, allocation, none
    target_hostnames: List[str]  # Required - list of target hostnames
    pids: Optional[List[int]] = None
    stop_level: Optional[str] = "process"  # process, host (only relevant for stop commands)
    additional_args: Optional[Dict[str, Any]] = None


class ProfilingResponse(BaseModel):
    """Response model for profiling requests"""
    success: bool
    message: str
    request_id: Optional[str] = None
    command_id: Optional[str] = None  # Added for agent idempotency
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
    profiling_command: Optional[Dict[str, Any]] = None  # Changed from profiling_request to profiling_command
    command_id: Optional[str] = None


class ProfilingCommand(BaseModel):
    """Model for combined profiling command sent to hosts"""
    command_id: str
    command_type: str  # start, stop
    hostname: str
    service_name: str
    combined_config: Dict[str, Any]  # Combined configuration from multiple requests
    request_ids: List[str]  # List of request IDs that make up this command
    created_at: datetime
    status: str  # pending, sent, completed, failed


class CommandCompletionRequest(BaseModel):
    """Model for reporting command completion"""
    command_id: str
    hostname: str
    status: str  # completed, failed
    execution_time: Optional[int] = None  # seconds
    error_message: Optional[str] = None
    results_path: Optional[str] = None  # S3 path or local path to results


def get_time_interval_value(start_time: datetime, end_time: datetime, interval: str) -> str:
    if interval in [
        "15 seconds",
        "30 seconds",
        "1 minutes",
        "5 minutes",
        "15 minutes",
        "1 hours",
        "2 hours",
        "6 hours",
        "24 hours",
    ]:
        return interval
    diff = end_time - start_time
    value = diff.total_seconds()
    if value <= 60 * 30:  # half an hour
        return "15 seconds"
    if value <= 60 * 60:  # hour
        return "30 seconds"
    if value <= 60 * 60 * 24:  # day
        return "15 minutes"
    if value <= 60 * 60 * 24 * 7:  # week
        return "2 hours"
    if value <= 60 * 60 * 24 * 14:  # 2 weeks
        return "6 hours"
    return "24 hours"


@router.get("/instance_type_count", response_model=List[InstanceTypeCount])
def get_instance_type_count(fg_params: FGParamsBaseModel = Depends(flamegraph_base_request_params)):
    response = get_query_response(fg_params, lookup_for="instance_type_count")
    res = []
    for row in response:
        res.append(
            {
                "instance_count": row.get("instance_count", 0),
                "instance_type": row.get("instance_type", "").split("/")[-1],
            }
        )
    return res


@router.get("/samples", response_model=List[SampleCount])
def get_flamegraph_samples_count(
    requested_interval: Optional[str] = "",
    fg_params: FGParamsBaseModel = Depends(flamegraph_base_request_params),
):
    return get_query_response(fg_params, lookup_for="samples", interval=requested_interval)


@router.get(
    "/graph", response_model=List[MetricGraph], responses={204: {"description": "Good request, just has no data"}}
)
def get_flamegraph_metrics_graph(
    requested_interval: Optional[str] = "",
    fg_params: FGParamsBaseModel = Depends(flamegraph_base_request_params),
):
    deployment_name = get_rql_first_eq_key(fg_params.filter, FilterTypes.K8S_OBJ_KEY)
    if deployment_name:
        return Response(status_code=204)
    fg_params.filter = get_rql_only_for_one_key(fg_params.filter, FilterTypes.HOSTNAME_KEY)
    return get_metrics_response(fg_params, lookup_for="graph", interval=requested_interval)


@router.get(
    "/function_cpu",
    response_model=List[CpuMetric],
    responses={204: {"description": "Good request, just has no data"}},
)
def get_function_cpu_overtime(
    function_name: str = Query(..., alias="functionName"),
    fg_params: FGParamsBaseModel = Depends(flamegraph_base_request_params),
):
    fg_params.function_name = function_name
    return get_query_response(fg_params, lookup_for="samples_count_by_function")


@router.get(
    "/graph/nodes_and_cores",
    response_model=List[MetricNodesAndCores],
    responses={204: {"description": "Good request, just has no data"}},
)
def get_flamegraph_nodes_cores_graph(
    requested_interval: str = "",
    fg_params: FGParamsBaseModel = Depends(flamegraph_base_request_params),
):
    deployment_name = get_rql_first_eq_key(fg_params.filter, FilterTypes.K8S_OBJ_KEY)
    container_name_value = get_rql_first_eq_key(fg_params.filter, FilterTypes.CONTAINER_KEY)
    host_name_value = get_rql_first_eq_key(fg_params.filter, FilterTypes.HOSTNAME_KEY)

    if deployment_name or container_name_value:
        return Response(status_code=204)

    db_manager = DBManager()
    service_id = db_manager.get_service_id_by_name(fg_params.service_name)
    parsed_interval_value = get_time_interval_value(fg_params.start_time, fg_params.end_time, requested_interval)
    return db_manager.get_nodes_and_cores_graph(
        service_id, fg_params.start_time, fg_params.end_time, parsed_interval_value, host_name_value
    )


@router.get(
    "/summary",
    response_model=MetricSummary,
    response_model_exclude_none=True,
    responses={204: {"description": "Good request, just has no data"}},
)
def get_metrics_summary(
    fg_params: FGParamsBaseModel = Depends(flamegraph_base_request_params),
):
    deployment_name = get_rql_first_eq_key(fg_params.filter, FilterTypes.K8S_OBJ_KEY)

    if not deployment_name:  # hostname before deployment or no filters at all
        fg_params.filter = get_rql_only_for_one_key(fg_params.filter, FilterTypes.HOSTNAME_KEY)
        return get_metrics_response(fg_params, lookup_for="summary")

    return Response(status_code=204)


@router.get(
    "/nodes_cores/summary",
    response_model=MetricNodesCoresSummary,
    response_model_exclude_none=True,
    responses={204: {"description": "Good request, just has no data"}},
)
def get_nodes_and_cores_metrics_summary(
    fg_params: FGParamsBaseModel = Depends(flamegraph_base_request_params),
    ignore_zeros: bool = Query(False, alias="ignoreZeros"),
):
    db_manager = DBManager()
    deployment_name = get_rql_first_eq_key(fg_params.filter, FilterTypes.K8S_OBJ_KEY)
    if not deployment_name:  # hostname before deployment or no filters at all

        service_id = db_manager.get_service_id_by_name(fg_params.service_name)
        host_name_value = get_rql_first_eq_key(fg_params.filter, FilterTypes.HOSTNAME_KEY)
        res = db_manager.get_nodes_cores_summary(
            service_id, fg_params.start_time, fg_params.end_time, ignore_zeros=ignore_zeros, hostname=host_name_value
        )
        if res["avg_cores"] is None and res["avg_nodes"] is None:
            return Response(status_code=204)
        return res

    return Response(status_code=204)


@router.get("/cpu_trend", response_model=CpuTrend)
def calculate_trend_in_cpu(
    fg_params: FGParamsBaseModel = Depends(flamegraph_base_request_params),
):
    diff_in_days: int = (fg_params.end_time - fg_params.start_time).days
    diff_in_hours: float = (fg_params.end_time - fg_params.start_time).seconds / 3600
    diff_in_weeks = timedelta(weeks=1)

    # if the time range is longer then onw week we would like to increase the delta in x +1 weeks from the current range
    if diff_in_days >= 7:
        weeks = math.ceil(diff_in_days / 7)
        # If it is exactly X weeks diff, verify that it is exactly X weeks diff without hours, otherwise add 1 week
        if diff_in_days % 7 == 0 and diff_in_hours > 0:
            weeks += 1
        diff_in_weeks = timedelta(weeks=weeks)
    compared_start_date = fg_params.start_time - diff_in_weeks
    compared_end_date = fg_params.end_time - diff_in_weeks

    response = get_metrics_response(
        fg_params,
        lookup_for="cpu_trend",
        compared_start_datetime=compared_start_date.strftime("%Y-%m-%dT%H:%M:%S"),
        compared_end_datetime=compared_end_date.strftime("%Y-%m-%dT%H:%M:%S"),
    )

    return response
