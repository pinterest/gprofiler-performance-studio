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
from io import BytesIO
from logging import getLogger
from typing import Optional

from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from gprofiler_dev import S3ProfileDal
from gprofiler_dev.tags import get_hash_filter_tag

logger = getLogger(__name__)
router = APIRouter()


def get_latest_perfspect_report_path(
    service_name: str,
    hostname: str,
    s3_dal: S3ProfileDal
) -> Optional[str]:
    """
    Find the latest PerfSpect HTML report for a given service and hostname
    by listing S3 objects and selecting the one with the highest timestamp.
    
    Args:
        service_name: The service name (e.g., 'devapp')
        hostname: The hostname to filter by
        s3_dal: S3 data access layer instance
        
    Returns:
        S3 path to the latest HTML report, or None if not found
        
    The S3 path structure is:
        products/{service_name}/stacks/{timestamp}_{uuid}_{hostname_hash}.html
        
    Example:
        products/devapp/stacks/2025-12-17T22:31:27_efb6a4780e6a46e285d45cf809a95bda_aa7017823fd49a2a5baaed009b7c6734.html
        
    Note: The .html files in S3 are gzipped and need to be decompressed before serving.
    """
    # Calculate hostname hash (MD5)
    hostname_hash = get_hash_filter_tag(hostname)
    
    # Build S3 prefix to list files
    # Format: products/{service_name}/stacks/
    prefix = s3_dal.join_path(s3_dal.base_directory, service_name, s3_dal.input_folder_name) + "/"
    
    logger.info(f"Searching for PerfSpect reports with prefix: {prefix}, hostname_hash: {hostname_hash}")
    
    try:
        # List all objects with the prefix
        response = s3_dal._s3_client.list_objects_v2(
            Bucket=s3_dal.bucket_name,
            Prefix=prefix
        )
        
        if 'Contents' not in response:
            logger.warning(f"No files found with prefix: {prefix}")
            return None
        
        # Filter files matching our criteria
        matching_files = []
        for obj in response['Contents']:
            key = obj['Key']
            
            # Must be an HTML file (PerfSpect reports have .html extension in S3)
            if not key.endswith('.html'):
                continue
            
            # Must contain the hostname hash
            if hostname_hash not in key:
                continue
            
            # Extract timestamp from filename
            # Format: products/service/stacks/2025-12-17T22:31:27_uuid_hash.html
            filename = key.split('/')[-1]  # Get just the filename
            try:
                # Extract timestamp (before first underscore)
                timestamp_str = filename.split('_')[0]
                file_timestamp = datetime.fromisoformat(timestamp_str)
                
                matching_files.append({
                    'key': key,
                    'timestamp': file_timestamp,
                    'last_modified': obj['LastModified']
                })
            except (ValueError, IndexError) as e:
                logger.warning(f"Could not parse timestamp from filename: {filename}, error: {e}")
                continue
        
        if not matching_files:
            logger.warning(f"No PerfSpect reports found for hostname: {hostname}")
            return None
        
        # Sort by timestamp (descending) to get the latest
        matching_files.sort(key=lambda x: x['timestamp'], reverse=True)
        latest_file = matching_files[0]
        
        logger.info(f"Found latest PerfSpect report: {latest_file['key']} at {latest_file['timestamp']}")
        return latest_file['key']
        
    except ClientError as e:
        logger.error(f"S3 error while listing objects: {e}")
        raise HTTPException(status_code=500, detail="Failed to list S3 objects")


@router.get(
    "/download_report",
    responses={
        200: {"content": {"text/html": {}}},
        404: {"description": "PerfSpect report not found"},
    },
)
def download_perfspect_report(
    service_name: str = Query(..., alias="serviceName"),
    hostname: str = Query(..., alias="hostname"),
):
    """
    Download the latest PerfSpect HTML report for a specific service and hostname.
    
    This endpoint:
    1. Lists S3 objects in products/{service}/stacks/
    2. Filters by hostname hash
    3. Returns the LATEST HTML report (highest timestamp) as a downloadable file
    
    Time range parameters are NOT needed - always returns the most recent report.
    
    Query Parameters:
        serviceName: The service name (e.g., 'devapp')
        hostname: The hostname (e.g., 'devrestricted-achatharajupalli')
    
    Returns:
        StreamingResponse with HTML content and Content-Disposition header for download
    
    Example:
        GET /api/perfspect/download_report?serviceName=devapp&hostname=my-host
    """
    if not hostname:
        raise HTTPException(400, detail="hostname parameter is required to download the PerfSpect report")
    
    # Initialize S3 client
    s3_dal = S3ProfileDal(logger)
    
    # Find the latest report (always gets the most recent, ignoring time range)
    s3_path = get_latest_perfspect_report_path(
        service_name=service_name,
        hostname=hostname,
        s3_dal=s3_dal
    )
    
    if not s3_path:
        raise HTTPException(
            status_code=404,
            detail=f"No PerfSpect report found for service '{service_name}' and hostname '{hostname}'"
        )
    
    # Download the HTML file from S3
    try:
        # Note: PerfSpect reports are stored as gzipped HTML
        html_content = s3_dal.get_object(s3_path, is_gzip=True)
    except ClientError as e:
        logger.error(f"Failed to download PerfSpect report from S3: {s3_path}, error: {e}")
        raise HTTPException(status_code=404, detail="The PerfSpect report file could not be downloaded from S3")
    
    # Generate filename from S3 path
    filename = s3_path.split('/')[-1]
    
    # Return as downloadable file
    # Use application/octet-stream to force download instead of rendering in browser
    html_bytes = BytesIO(html_content.encode('utf-8'))
    return StreamingResponse(
        html_bytes,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )

