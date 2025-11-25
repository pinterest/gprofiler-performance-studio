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

from typing import Optional

from backend.config import MAX_SIMULTANEOUS_PROFILING_HOSTS_PERCENT
from backend.models.metrics_models import ProfilingRequest


def validate_profiling_capacity(
    profiling_request: ProfilingRequest,
    db_manager,
    service_name: Optional[str] = None
) -> tuple[bool, Optional[str]]:
    """
    Validate that the profiling request doesn't exceed the maximum simultaneous profiling capacity.
    
    This function should be called in the service/router layer where you have access to DBManager.
    
    Args:
        profiling_request: The ProfilingRequest object to validate
        db_manager: Instance of DBManager to query active profiling hosts
        service_name: Optional service name to filter by (if None, checks globally)
        
    Returns:
        tuple: (is_valid: bool, error_message: Optional[str])
        
    Example:
        >>> from gprofiler_dev.postgres.db_manager import DBManager
        >>> db_manager = DBManager()
        >>> is_valid, error = validate_profiling_capacity(request, db_manager, "my-service")
        >>> if not is_valid:
        ...     raise ValueError(error)
    """
    # Only validate for "start" requests
    if profiling_request.request_type != "start":
        return True, None
    
    # Get counts
    active_hosts_count = db_manager.get_active_hosts_count(service_name)
    currently_profiling_count = db_manager.get_actively_profiling_hosts_count(service_name)
    
    # Calculate maximum allowed profiling hosts
    max_profiling_hosts = int((active_hosts_count * MAX_SIMULTANEOUS_PROFILING_HOSTS_PERCENT) / 100)
    
    # Calculate new total if this request is approved
    new_profiling_total = currently_profiling_count + profiling_request.total_request_size
    
    # Check if it would exceed the limit
    if new_profiling_total > max_profiling_hosts:
        error_msg = (
            f"Profiling capacity exceeded. "
            f"Currently profiling: {currently_profiling_count} hosts, "
            f"Request size: {profiling_request.total_request_size} hosts, "
            f"Active hosts: {active_hosts_count}, "
            f"Maximum allowed ({MAX_SIMULTANEOUS_PROFILING_HOSTS_PERCENT}%): {max_profiling_hosts} hosts. "
            f"This request would result in {new_profiling_total} profiling hosts, "
            f"which exceeds the limit by {new_profiling_total - max_profiling_hosts} hosts."
        )
        return False, error_msg
    
    return True, None
