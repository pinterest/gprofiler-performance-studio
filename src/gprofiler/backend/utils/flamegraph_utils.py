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

import subprocess
import tempfile
from datetime import datetime, timezone
from io import BytesIO
from typing import Optional, List, Tuple

from backend.config import FLAMEGRAPH_PATH
from backend.models.filters_models import RQLFilter

TIME_RANGE_MAP = {
    24 * 60 * 60: "daily",
    24 * 60 * 60 * 7: "weekly",
    6 * 60 * 60: "6hour",
    60 * 60: "hourly",
    30 * 60: "30min",
    15 * 60: "15min",
}


def get_svg_file(collapsed_file_data: str):
    _, temp_collapsed_file = tempfile.mkstemp()
    with open(temp_collapsed_file, "w") as file:
        file.write(collapsed_file_data)
    cmd = [f"{FLAMEGRAPH_PATH}/flamegraph.pl --inverted --colors=combined", temp_collapsed_file]
    output = subprocess.check_output(f"{' '.join(cmd)}", shell=True)
    return BytesIO(output)


def get_file_name(
    start_time: datetime,
    end_time: datetime,
    service_name: str,
    suffix: str = "svg",
    rql_filter: Optional[RQLFilter] = None,
):
    delta_in_seconds = int((end_time - start_time).total_seconds())
    file_time_range_name = TIME_RANGE_MAP.get(delta_in_seconds, "custom")
    filter_str = ""
    if rql_filter:
        filter_str = f"_{rql_filter.get_formatted_filter()}"
    file_name = f"{service_name}_{file_time_range_name}{filter_str}.{suffix}"
    return file_name


# ============================================================================
# Adhoc Flamegraph Metadata Utilities
# ============================================================================


def parse_adhoc_flamegraph_filename(filename: str) -> Optional[Tuple[datetime, str]]:
    """
    Parse adhoc flamegraph filename to extract timestamp and hostname.
    
    Args:
        filename: Flamegraph filename (e.g., "2026-02-03T10:30:00Z_abc123_hostname_adhoc_flamegraph.html")
        
    Returns:
        Tuple of (timestamp, hostname) or None if parsing fails
        
    Example:
        >>> parse_adhoc_flamegraph_filename("2026-02-03T10:30:00Z_abc123_myhost_adhoc_flamegraph.html")
        (datetime(2026, 2, 3, 10, 30, 0, tzinfo=timezone.utc), "myhost")
    """
    if not filename.endswith('_adhoc_flamegraph.html'):
        return None
    
    try:
        # Remove the _adhoc_flamegraph.html suffix to get the base filename
        base_filename = filename.replace('_adhoc_flamegraph.html', '')
        
        # Parse timestamp and hostname
        # Format: timestamp_random_suffix_hostname_adhoc
        filename_parts = base_filename.split('_')
        
        if len(filename_parts) < 3:
            return None
        
        timestamp_str = filename_parts[0]
        timestamp = datetime.fromisoformat(timestamp_str).replace(tzinfo=timezone.utc)
        
        # Extract hostname (everything after timestamp and random_suffix)
        hostname = '_'.join(filename_parts[2:])
        
        return timestamp, hostname
        
    except (ValueError, IndexError):
        return None


def should_include_flamegraph(
    timestamp: datetime,
    hostname: str,
    start_time: Optional[datetime],
    end_time: Optional[datetime],
    hostname_filters: Optional[List[str]]
) -> bool:
    """
    Check if flamegraph should be included based on filters.
    
    Args:
        timestamp: Flamegraph timestamp
        hostname: Flamegraph hostname
        start_time: Optional start time filter
        end_time: Optional end time filter
        hostname_filters: Optional list of hostnames to filter by
        
    Returns:
        True if flamegraph should be included, False otherwise
    """
    # Filter by time range
    if start_time and timestamp < start_time:
        return False
    if end_time and timestamp > end_time:
        return False
    
    # Filter by hostname
    if hostname_filters and hostname and hostname not in hostname_filters:
        return False
    
    return True


def extract_perf_events_from_profile(profile_header: dict) -> Optional[List[str]]:
    """
    Extract perf_events from profile metadata.
    
    Args:
        profile_header: Profile header dictionary containing metadata
        
    Returns:
        List of perf event names or None if not found/invalid
        
    Example:
        >>> header = {"metadata": {"run_arguments": {"perf_events": "cycles,instructions"}}}
        >>> extract_perf_events_from_profile(header)
        ['cycles', 'instructions']
    """
    try:
        perf_events_str = (
            profile_header
            .get("metadata", {})
            .get("run_arguments", {})
            .get("perf_events")
        )
        
        if not perf_events_str or not isinstance(perf_events_str, str):
            return None
        
        # Convert comma-separated string to list, strip whitespace
        perf_events = [e.strip() for e in perf_events_str.split(",") if e.strip()]
        
        return perf_events if perf_events else None
        
    except Exception:
        return None
