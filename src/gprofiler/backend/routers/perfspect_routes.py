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

from backend.models.filters_models import FilterTypes
from backend.models.flamegraph_models import FGParamsBaseModel
from backend.utils.filters_utils import get_rql_first_eq_key
from backend.utils.request_utils import flamegraph_base_request_params, get_metrics_response
from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from gprofiler_dev import S3ProfileDal

logger = getLogger(__name__)
router = APIRouter()


@router.get(
    "/download_report",
    responses={
        200: {"content": {"text/html": {}}},
        404: {"description": "PerfSpect report not found"},
    },
)
def download_perfspect_report(
    fg_params: FGParamsBaseModel = Depends(flamegraph_base_request_params),
):
    """
    Download the latest PerfSpect HTML report for a specific service and hostname.
    
    This endpoint:
    1. Queries ClickHouse for the latest HTML report path using get_metrics_response
    2. Downloads the report from S3
    3. Returns the HTML report as a downloadable file
    
    Query Parameters:
        serviceName: The service name (e.g., 'devapp')
        startTime: Start of time range (ISO 8601 format)
        endTime: End of time range (ISO 8601 format)
        filter: RQL format filter (must include hostname filter)
    
    Returns:
        StreamingResponse with HTML content and Content-Disposition header for download
    
    Example:
        GET /api/perfspect/download_report?serviceName=devapp&startTime=2025-01-01T00:00:00&endTime=2025-01-02T00:00:00&filter=hostname==my-host
    """
    host_name_value = get_rql_first_eq_key(fg_params.filter, FilterTypes.HOSTNAME_KEY)
    if not host_name_value:
        raise HTTPException(400, detail="Must filter by hostname to download the PerfSpect report")
    
    # Get S3 path from ClickHouse using the same pattern as get_html_metadata
    s3_path = get_metrics_response(fg_params, lookup_for="lasthtml")
    if not s3_path:
        raise HTTPException(404, detail="The PerfSpect report path not found in ClickHouse")
    
    # Initialize S3 client
    s3_dal = S3ProfileDal(logger)
    
    # Download the HTML file from S3
    try:
        # Note: PerfSpect reports are stored as gzipped HTML
        html_content = s3_dal.get_object(s3_path, is_gzip=True)
    except ClientError:
        raise HTTPException(status_code=404, detail="The PerfSpect report file not found in S3")
    
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

