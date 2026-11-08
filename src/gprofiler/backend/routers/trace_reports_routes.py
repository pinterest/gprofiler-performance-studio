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
"""Trace <-> profile HTML reports rendered by the indexer (see gprofiler_indexer/trace_reports)."""

import json
import re
from logging import getLogger
from typing import Any, Dict

from backend.config import TRACE_REPORTS_S3_PREFIX
from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from gprofiler_dev import S3ProfileDal
from gprofiler_dev.config import S3_PATH_PREFIX

logger = getLogger(__name__)
router = APIRouter()

# Must match the indexer's key layout: <report id>/<YYYYMMDDTHHMMZ>.html
REPORT_PATH = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}/\d{8}T\d{4}Z\.html$")

# Reports embed untrusted span/frame names; render them in an opaque origin with no access to
# the app's cookies or API even if opened directly instead of in the sandboxed iframe.
REPORT_CSP = (
    "sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox; "
    "default-src 'none'; script-src 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'unsafe-inline' https://cdn.jsdelivr.net; img-src data:; frame-ancestors 'self'"
)


def report_key(path: str) -> str:
    if not REPORT_PATH.match(path):
        raise HTTPException(400, detail="invalid report path")
    return "/".join(p for p in (S3_PATH_PREFIX, TRACE_REPORTS_S3_PREFIX, path) if p)


@router.get("", response_model=Dict[str, Any])
def list_trace_reports():
    """Published reports, newest first: {updated_at, reports: [{id, label, generated_at, path, ...}], errors}."""
    try:
        raw = S3ProfileDal(logger).get_object("/".join(p for p in (S3_PATH_PREFIX, TRACE_REPORTS_S3_PREFIX,
                                                                    "index.json") if p))
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "NoSuchKey":
            return {"updated_at": None, "reports": [], "errors": []}
        logger.exception("failed to read trace reports index")
        raise HTTPException(502, detail="trace reports index unavailable")
    index = json.loads(raw)
    index["reports"] = [r for r in index.get("reports", []) if REPORT_PATH.match(r.get("path", ""))]
    return index


@router.get("/html", response_class=HTMLResponse, responses={404: {"description": "report not found"}})
def get_trace_report(path: str = Query(..., description="report path from the index, e.g. my-report/20260101T0000Z.html")):
    key = report_key(path)
    try:
        body = S3ProfileDal(logger).get_object(key, is_gzip=True)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "NoSuchKey":
            raise HTTPException(404, detail="report not found")
        logger.exception("failed to read trace report %s", key)
        raise HTTPException(502, detail="trace report unavailable")
    return HTMLResponse(body, headers={
        "Content-Security-Policy": REPORT_CSP,
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, max-age=86400, immutable",
    })
