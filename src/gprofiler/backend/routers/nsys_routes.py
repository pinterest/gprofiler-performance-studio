#
# Copyright (C) 2026 Intel Corporation / Pinterest
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

import os
import re
from datetime import datetime
from logging import getLogger

from fastapi import APIRouter, Header, HTTPException, Query, Request
from gprofiler_dev import get_s3_profile_dal
from gprofiler_dev.api_key import get_service_by_api_key

logger = getLogger(__name__)
router = APIRouter()

# Raw nsys reports are large; cap uploads independently of the (much smaller)
# profile upload limit.
MAX_NSYS_REP_LEN = int(os.environ.get("CONFIG_MAX_NSYS_REP_LEN", 512 * 1024 * 1024))

NSYS_REP_S3_DIR = "nsys"


def nsys_rep_s3_key(service_name: str, start_time: datetime, hostname: str) -> str:
    """S3 key for a raw .nsys-rep, pairable with its AdhocFlamegraphMetadata row
    (which is keyed by the same profile start_time + hostname)."""
    start_iso = start_time.replace(microsecond=0, tzinfo=None).isoformat()
    safe_hostname = re.sub(r"[^A-Za-z0-9._-]", "-", hostname)
    return f"products/{service_name}/stacks/{NSYS_REP_S3_DIR}/{start_iso}_{safe_hostname}.nsys-rep"


@router.post("")
async def upload_nsys_rep(
    request: Request,
    start_time: str = Query(...),
    hostname: str = Query(...),
    gprofiler_api_key: str = Header(...),
    gprofiler_service_name: str = Header(...),
):
    """
    Agent-facing: store a raw .nsys-rep capture in S3 so it can later be
    downloaded from the Adhoc Profiling view and opened in NVIDIA Nsight Systems.
    The body is the raw report bytes (application/octet-stream, not gzipped).
    """
    service_name, _token_id = get_service_by_api_key(gprofiler_api_key, gprofiler_service_name)
    if not service_name:
        raise HTTPException(400, {"message": "Invalid GPROFILER-SERVICE-NAME header"})

    if "content-length" not in request.headers:
        raise HTTPException(411, {"message": "Content-Length is required"})
    content_length = int(request.headers["content-length"])
    if content_length > MAX_NSYS_REP_LEN:
        raise HTTPException(413, {"message": f"nsys rep exceeds max allowed size ({MAX_NSYS_REP_LEN} bytes)"})

    try:
        parsed_start_time = datetime.fromisoformat(start_time)
    except ValueError:
        raise HTTPException(400, {"message": f"Invalid start_time {start_time!r} (expected ISO-8601)"})

    body = await request.body()
    if not body:
        raise HTTPException(400, {"message": "Empty request body"})

    s3_key = nsys_rep_s3_key(service_name, parsed_start_time, hostname)
    profile_dal = get_s3_profile_dal(logger)
    try:
        profile_dal.write_file(s3_key, body)
    except Exception as e:
        logger.error(f"Failed to write nsys rep to S3 at {s3_key}: {e}")
        raise HTTPException(500, {"message": "Failed to store nsys rep"})

    logger.info(f"Stored nsys rep for service {service_name} at {s3_key} ({len(body)} bytes)")
    return {"message": "ok", "s3_key": s3_key}
