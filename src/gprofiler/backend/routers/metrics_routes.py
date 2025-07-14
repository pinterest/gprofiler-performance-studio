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

import json
import math
import uuid
from datetime import datetime, timedelta
from logging import getLogger
from typing import List, Optional, Dict, Any, Literal

from botocore.exceptions import ClientError

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
from pydantic import BaseModel, model_validator

from gprofiler_dev import S3ProfileDal
from gprofiler_dev.postgres.db_manager import DBManager

logger = getLogger(__name__)
router = APIRouter()


class ProfilingRequest(BaseModel):
    """Model for profiling request parameters"""
    service_name: str
    command_type: str = "start"  # start, stop
    duration: Optional[int] = 60  # seconds
    frequency: Optional[int] = 11  # Hz
    profiling_mode: Optional[Literal["cpu", "allocation", "none"]] = "cpu"  # cpu, allocation, none
    target_hostnames: Optional[List[str]] = None
    pids: Optional[List[int]] = None
    stop_level: Optional[str] = "process"  # process, host (only relevant for stop commands)
    additional_args: Optional[Dict[str, Any]] = None

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
    command_ids: Optional[List[str]] = None  # List of command IDs for agent idempotency
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
    status: Literal["completed", "failed"]  # completed, failed
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


@router.get("/html_metadata", response_model=HTMLMetadata)
def get_html_metadata(
    fg_params: FGParamsBaseModel = Depends(flamegraph_base_request_params),
):
    host_name_value = get_rql_first_eq_key(fg_params.filter, FilterTypes.HOSTNAME_KEY)
    if not host_name_value:
        raise HTTPException(400, detail="Must filter by hostname to get the html metadata")
    s3_path = get_metrics_response(fg_params, lookup_for="lasthtml")
    if not s3_path:
        raise HTTPException(404, detail="The html metadata path not found in CH")
    s3_dal = S3ProfileDal(logger)
    try:
        html_content = s3_dal.get_object(s3_path, is_gzip=True)
    except ClientError:
        raise HTTPException(status_code=404, detail="The html metadata file not found in S3")
    return HTMLMetadata(content=html_content)


@router.post("/profile_request", response_model=ProfilingResponse)
def create_profiling_request(profiling_request: ProfilingRequest):
    """
    Create a new profiling request with the specified parameters.
    
    This endpoint accepts profiling arguments in JSON format and handles both
    start and stop profiling commands. Each request generates a unique command_id
    that agents use for idempotency - agents will only execute commands with
    new command IDs they haven't seen before.
    
    START commands:
    - Create new profiling sessions with specified parameters
    - Merge multiple requests for the same host into single commands
    
    STOP commands:
    - Process-level stop: Remove specific PIDs from existing commands
      - If only one PID remains, convert to host-level stop
      - If multiple PIDs remain, update command with remaining PIDs
    - Host-level stop: Stop entire profiling session for the host
    """
    try:
        # Log the profiling request
        logger.info(
            f"Received {profiling_request.command_type} profiling request for service: {profiling_request.service_name}",
            extra={
                "command_type": profiling_request.command_type,
                "service_name": profiling_request.service_name,
                "duration": profiling_request.duration,
                "frequency": profiling_request.frequency,
                "mode": profiling_request.profiling_mode,
                "target_hostnames": profiling_request.target_hostnames,
                "pids": profiling_request.pids,
                "stop_level": profiling_request.stop_level
            }
        )
        
        db_manager = DBManager()
        request_id = str(uuid.uuid4())
        command_ids = []  # Track all command IDs created
        
        try:
            # Save the profiling request to database using enhanced method
            success = db_manager.save_profiling_request(
                request_id=request_id,
                service_name=profiling_request.service_name,
                duration=profiling_request.duration,
                frequency=profiling_request.frequency,
                profiling_mode=profiling_request.profiling_mode,
                target_hostnames=profiling_request.target_hostnames,
                pids=profiling_request.pids,
                additional_args=profiling_request.additional_args
            )
            
            if not success:
                raise Exception("Failed to save profiling request to database")
            
            # Handle start vs stop commands differently
            if profiling_request.command_type == "start":
                # Create profiling commands for target hosts
                if profiling_request.target_hostnames:
                    for hostname in profiling_request.target_hostnames:
                        command_id = str(uuid.uuid4())
                        command_ids.append(command_id)
                        db_manager.create_or_update_profiling_command(
                            command_id=command_id,
                            hostname=hostname,
                            service_name=profiling_request.service_name,
                            command_type="start",
                            new_request_id=request_id,
                        )
                else:
                    # If no specific hostnames, create command for all hosts of this service
                    command_id = str(uuid.uuid4())
                    command_ids.append(command_id)
                    db_manager.create_or_update_profiling_command(
                        command_id=command_id,
                        hostname=None,  # Will be handled for all hosts of the service
                        service_name=profiling_request.service_name,
                        command_type="start",
                        new_request_id=request_id,
                    )
            
            elif profiling_request.command_type == "stop":
                # Handle stop commands
                if profiling_request.target_hostnames:
                    for hostname in profiling_request.target_hostnames:
                        command_id = str(uuid.uuid4())
                        command_ids.append(command_id)
                        if profiling_request.stop_level == "host":
                            # Stop entire host
                            db_manager.create_stop_command_for_host(
                                command_id=command_id,
                                hostname=hostname,
                                service_name=profiling_request.service_name,
                                request_id=request_id
                            )
                        else:  # process level stop
                            # Stop specific processes or modify existing commands
                            db_manager.handle_process_level_stop(
                                command_id=command_id,
                                hostname=hostname,
                                service_name=profiling_request.service_name,
                                pids_to_stop=profiling_request.pids,
                                request_id=request_id
                            )
            
            logger.info(f"Profiling request {request_id} ({profiling_request.command_type}) saved and commands processed. Command IDs: {command_ids}")
            
        except Exception as e:
            logger.error(f"Failed to save profiling request: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail="Failed to save profiling request to database"
            )
        
        # Calculate estimated completion time (only for start commands)
        completion_time = None
        if profiling_request.command_type == "start":
            completion_time = datetime.now() + timedelta(seconds=profiling_request.duration or 60)
        
        # Create appropriate message based on number of commands
        if len(command_ids) == 1:
            message = f"{profiling_request.command_type.capitalize()} profiling request submitted successfully for service '{profiling_request.service_name}'"
        else:
            message = f"{profiling_request.command_type.capitalize()} profiling request submitted successfully for service '{profiling_request.service_name}' across {len(command_ids)} hosts"
        
        return ProfilingResponse(
            success=True,
            message=message,
            request_id=request_id,
            command_ids=command_ids,
            estimated_completion_time=completion_time
        )
        
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        logger.error(f"Failed to create profiling request: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Internal server error while processing profiling request"
        )


@router.post("/heartbeat", response_model=HeartbeatResponse)
def receive_heartbeat(heartbeat: HeartbeatRequest):
    """
    Receive heartbeat from host and check for pending profiling requests.
    
    This endpoint:
    1. Receives heartbeat information from hosts (IP, hostname, service, last command)
    2. Updates host status in PostgreSQL DB
    3. Checks for pending profiling requests for this host/service
    4. Returns new profiling request if available and not already executed
    """
    try:
        # Set timestamp if not provided
        if heartbeat.timestamp is None:
            heartbeat.timestamp = datetime.now()
        
        # Log the heartbeat
        logger.info(
            f"Received heartbeat from host: {heartbeat.hostname} ({heartbeat.ip_address})",
            extra={
                "hostname": heartbeat.hostname,
                "ip_address": heartbeat.ip_address,
                "service_name": heartbeat.service_name,
                "last_command_id": heartbeat.last_command_id,
                "status": heartbeat.status,
                "timestamp": heartbeat.timestamp
            }
        )
        
        db_manager = DBManager()
        
        try:
            # 1. Update host heartbeat information in PostgreSQL DB
            db_manager.upsert_host_heartbeat(
                hostname=heartbeat.hostname,
                ip_address=heartbeat.ip_address,
                service_name=heartbeat.service_name,
                last_command_id=heartbeat.last_command_id,
                status=heartbeat.status,
                heartbeat_timestamp=heartbeat.timestamp
            )
            
            # 2. Check for pending profiling commands for this host/service
            pending_command = db_manager.get_pending_profiling_command(
                hostname=heartbeat.hostname,
                service_name=heartbeat.service_name,
                exclude_command_id=heartbeat.last_command_id
            )
            
            if pending_command:
                # 3. Mark command as sent and update related request statuses
                success = db_manager.mark_profiling_command_sent(
                    command_id=pending_command["command_id"],
                    hostname=heartbeat.hostname
                )
                
                # 4. Mark related profiling requests as assigned
                if success and pending_command.get("request_ids"):
                    request_ids = pending_command["request_ids"]
                    # Handle both string and list formats from database
                    if isinstance(request_ids, str):
                        try:
                            request_ids = json.loads(request_ids) if request_ids.startswith('[') else [request_ids]
                        except json.JSONDecodeError:
                            logger.warning(f"Failed to parse request_ids for command {pending_command['command_id']}")
                            request_ids = []
                    
                    for request_id in request_ids:
                        try:
                            db_manager.mark_profiling_request_assigned(
                                request_id=request_id,
                                command_id=pending_command["command_id"],
                                hostname=heartbeat.hostname
                            )
                        except Exception as e:
                            logger.warning(f"Failed to mark request {request_id} as assigned: {e}")
                
                if success:
                    logger.info(f"Sending profiling command {pending_command['command_id']} to host {heartbeat.hostname}")
                    
                    # Extract combined_config and ensure it's properly formatted
                    combined_config = pending_command.get("combined_config", {})
                    
                    # If combined_config is a string (from DB), parse it
                    if isinstance(combined_config, str):
                        try:
                            combined_config = json.loads(combined_config)
                        except json.JSONDecodeError:
                            logger.warning(f"Failed to parse combined_config for command {pending_command['command_id']}")
                            combined_config = {}
                    
                    return HeartbeatResponse(
                        success=True,
                        message="Heartbeat received. New profiling command available.",
                        profiling_command={
                            "command_type": pending_command["command_type"],
                            "combined_config": combined_config
                        },
                        command_id=pending_command["command_id"]
                    )
                else:
                    logger.warning(f"Failed to mark command {pending_command['command_id']} as sent to host {heartbeat.hostname}")
            
            # No pending commands or marking failed
            return HeartbeatResponse(
                success=True,
                message="Heartbeat received. No pending profiling commands.",
                profiling_command=None,
                command_id=None
            )
                
        except Exception as e:
            logger.error(f"Failed to process heartbeat for {heartbeat.hostname}: {e}", exc_info=True)
            # Still return success for heartbeat, but no command
            return HeartbeatResponse(
                success=True,
                message="Heartbeat received, but failed to check for commands.",
                profiling_command=None,
                command_id=None
            )
        
    except Exception as e:
        logger.error(f"Failed to process heartbeat: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Internal server error while processing heartbeat"
        )


@router.post("/command_completion")
def report_command_completion(completion: CommandCompletionRequest):
    """
    Report completion of a profiling command from a host.
    
    This endpoint:
    1. Receives command completion status from hosts
    2. Updates command status in PostgreSQL DB
    3. Updates related profiling requests status
    """
    try:
        db_manager = DBManager()
        
        # Log the completion
        logger.info(
            f"Received command completion from host: {completion.hostname}",
            extra={
                "command_id": completion.command_id,
                "hostname": completion.hostname,
                "status": completion.status,
                "execution_time": completion.execution_time,
                "error_message": completion.error_message
            }
        )
        
        # Update command status
        db_manager.update_profiling_command_status(
            command_id=completion.command_id,
            hostname=completion.hostname,
            status=completion.status,
            execution_time=completion.execution_time,
            error_message=completion.error_message,
            results_path=completion.results_path
        )
        
        # Update related profiling requests status
        try:
            # Get the command to find related request IDs
            command_info = db_manager.get_profiling_command_by_id(completion.command_id)
            if command_info and command_info.get("request_ids"):
                request_ids = command_info["request_ids"]
                # Handle both string and list formats from database
                if isinstance(request_ids, str):
                    try:
                        request_ids = json.loads(request_ids) if request_ids.startswith('[') else [request_ids]
                    except json.JSONDecodeError:
                        logger.warning(f"Failed to parse request_ids for command {completion.command_id}")
                        request_ids = []
                
                for request_id in request_ids:
                    try:
                        # Map command status to request status
                        request_status = "completed" if completion.status == "completed" else "failed"
                        completed_at = datetime.now() if completion.status in ["completed", "failed"] else None
                        
                        db_manager.update_profiling_request_status(
                            request_id=request_id,
                            status=request_status,
                            completed_at=completed_at,
                            error_message=completion.error_message
                        )
                    except Exception as e:
                        logger.warning(f"Failed to update status for request {request_id}: {e}")
        except Exception as e:
            logger.warning(f"Failed to update related profiling requests for command {completion.command_id}: {e}")
            # Don't fail the entire operation if request status update fails
        
        return {
            "success": True,
            "message": f"Command completion recorded for {completion.command_id}"
        }
        
    except Exception as e:
        logger.error(f"Failed to process command completion: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Internal server error while processing command completion"
        )
