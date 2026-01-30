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

from typing import List, Optional

from backend.config import MAX_SIMULTANEOUS_PROFILING_HOSTS_PERCENT, MAX_PROFILING_REQUEST_HOSTS
from backend.models.metrics_models import BulkProfilingRequest


# Perf event name normalization mapping
# Maps UI event names (e.g., 'cpu-cycles') to agent-normalized names (e.g., 'cycles')
PERF_EVENT_NORMALIZATION_MAP = {
    "cpu-cycles": "cycles",
    "cpu-instructions": "instructions",
    "cpu-cache-misses": "cache-misses",
    "cpu-cache-references": "cache-references",
    "cpu-branch-instructions": "branch-instructions",
    "cpu-branch-misses": "branch-misses",
    "cpu-stalled-cycles-frontend": "stalled-cycles-frontend",
    "cpu-stalled-cycles-backend": "stalled-cycles-backend",
}


def normalize_perf_event_name(event: str) -> str:
    """
    Normalize perf event names to match what the agent stores.
    
    The agent normalizes events like 'cpu-cycles' -> 'cycles', 
    'cpu/cache-misses/' -> 'cache-misses'
    
    Args:
        event: Event name from UI or user input (e.g., 'cpu-cycles')
        
    Returns:
        Normalized event name (e.g., 'cycles')
        
    Example:
        >>> normalize_perf_event_name('cpu-cycles')
        'cycles'
        >>> normalize_perf_event_name('cpu/cache-misses/')
        'cache-misses'
        >>> normalize_perf_event_name('cycles')
        'cycles'
    """
    # Remove cpu/ prefix and trailing slash if present
    event = event.replace("cpu/", "").replace("/", "")
    
    # Apply normalization mapping
    return PERF_EVENT_NORMALIZATION_MAP.get(event, event)


def validate_profiling_capacity(
    bulk_profiling_request: BulkProfilingRequest,
    db_manager,
    service_name: Optional[str] = None
) -> tuple[bool, Optional[str], List[str]]:
    """
    Validate that the bulk profiling request doesn't exceed the maximum simultaneous profiling capacity.
    
    This function aggregates all target hosts across all requests in the bulk operation and validates
    the total capacity requirements.
    
    Args:
        bulk_profiling_request: The BulkProfilingRequest object containing multiple requests
        db_manager: Instance of DBManager to query active profiling hosts
        service_name: Optional service name to filter by (if None, checks globally)
        
    Returns:
        tuple: (is_valid: bool, error_message: Optional[str], target_hostnames: List[str])
            - is_valid: True if capacity check passes
            - error_message: Error description if validation fails
            - target_hostnames: List of all unique hostnames from all requests
        
    Example:
        >>> from gprofiler_dev.postgres.db_manager import DBManager
        >>> db_manager = DBManager()
        >>> is_valid, error, hostnames = validate_profiling_capacity(bulk_request, db_manager)
        >>> if not is_valid:
        ...     raise ValueError(error)
    """
    # Collect all target hostnames from all requests
    all_target_hostnames: List[str] = []
    has_start_requests = False
    
    for profiling_request in bulk_profiling_request.requests:
        # Track if there are any "start" requests
        if profiling_request.request_type == "start":
            has_start_requests = True
            
        # Collect all hostnames from target_hosts
        if profiling_request.target_hosts:
            all_target_hostnames.extend(profiling_request.target_hosts.keys())
    
    # Only validate capacity for bulk requests that contain "start" operations
    if not has_start_requests:
        return True, None, all_target_hostnames
    
    # Calculate total request size from all collected hostnames
    request_size = len(all_target_hostnames)
    
    # Validate that request size doesn't exceed MAX_PROFILING_REQUEST_HOSTS
    if request_size > MAX_PROFILING_REQUEST_HOSTS:
        error_msg = (
            f"Request size exceeded.\n"
            f"Request size: {request_size} hosts\n"
            f"Maximum allowed per request: {MAX_PROFILING_REQUEST_HOSTS} hosts\n"
            f"Please reduce the number of hosts in your request by {request_size - MAX_PROFILING_REQUEST_HOSTS} hosts."
        )
        return False, error_msg, all_target_hostnames
    
    # Get counts, excluding hosts from the current bulk request
    active_hosts_count = db_manager.get_active_hosts_count(service_name)
    currently_profiling_host_count = db_manager.get_actively_profiling_hosts_count(
        service_name=service_name,
    )
    currently_profiling_host_count_inside_selection = db_manager.get_actively_profiling_hosts_count(
        service_name=service_name,
        host_inclusion_list=all_target_hostnames
    )
    currently_profiling_host_count_outside_selection = db_manager.get_actively_profiling_hosts_count(
        service_name=service_name,
        host_exclusion_list=all_target_hostnames
    )
    
    # Calculate maximum allowed profiling hosts
    max_profiling_hosts = int((active_hosts_count * MAX_SIMULTANEOUS_PROFILING_HOSTS_PERCENT) / 100)
    
    # Calculate new total if this request is approved
    new_profiling_total = currently_profiling_host_count_outside_selection + request_size
    
    # Check if it would exceed the limit
    if new_profiling_total > max_profiling_hosts:
        error_msg = (
            f"Profiling capacity exceeded.\n"
            f"Currently profiling: {currently_profiling_host_count} hosts\n"
            f"Currently profiling inside selection: {currently_profiling_host_count_inside_selection} hosts\n"
            f"Currently profiling outside selection: {currently_profiling_host_count_outside_selection} hosts\n"
            f"Request size: {request_size} hosts\n"
            f"Active hosts: {active_hosts_count}\n"
            f"Maximum allowed ({MAX_SIMULTANEOUS_PROFILING_HOSTS_PERCENT}%): {max_profiling_hosts} hosts\n"
            f"This request would result in {new_profiling_total} profiling hosts, "
            f"which exceeds the limit by {new_profiling_total - max_profiling_hosts} hosts."
        )
        return False, error_msg, all_target_hostnames
    
    return True, None, all_target_hostnames
