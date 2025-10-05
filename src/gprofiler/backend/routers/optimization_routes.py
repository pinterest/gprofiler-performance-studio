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
import requests
from fastapi import APIRouter, HTTPException, Query
from backend.config import QUERY_API_BASE_URL, REST_CERTIFICATE_PATH, REST_PASSWORD, REST_USERNAME
from logging import getLogger

logger = getLogger(__name__)

router = APIRouter()


def get_optimization_request(endpoint: str, params: dict = None):
    """Helper function to make requests to the FlameDB Rest service for optimization data"""
    try:
        response = requests.get(
            url=f"{QUERY_API_BASE_URL}/api/v1/optimization{endpoint}",
            params=params or {},
            verify=REST_CERTIFICATE_PATH,
            auth=(REST_USERNAME, REST_PASSWORD),
        )
        if response.status_code >= 300:
            logger.error(f"FlameDB Rest service error: {response.text}")
            raise HTTPException(status_code=502, detail="Failed getting optimization data")
        
        return response.json()
    except requests.exceptions.ConnectionError:
        logger.error("Failed to connect to FlameDB Rest service")
        raise HTTPException(status_code=502, detail="Failed connect to flamedb api")


@router.get("/v1/optimization")
def get_optimization_recommendations(
    service_id: Optional[str] = Query(None, alias="service_id"),
    namespace: Optional[str] = Query(None),
    technology: Optional[str] = Query(None),
    complexity: Optional[str] = Query(None),
    optimization_type: Optional[str] = Query(None, alias="optimization_type"),
    rule_name: Optional[str] = Query(None, alias="rule_name"),
    min_impact: Optional[float] = Query(None, alias="min_impact"),
    min_precision: Optional[float] = Query(None, alias="min_precision"),
    min_hosts: Optional[int] = Query(None, alias="min_hosts"),
):
    """Get optimization recommendations with optional filters"""
    params = {}
    if service_id:
        params["service_id"] = service_id
    if namespace:
        params["namespace"] = namespace
    if technology:
        params["technology"] = technology
    if complexity:
        params["complexity"] = complexity
    if optimization_type:
        params["optimization_type"] = optimization_type
    if rule_name:
        params["rule_name"] = rule_name
    if min_impact is not None:
        params["min_impact"] = min_impact
    if min_precision is not None:
        params["min_precision"] = min_precision
    if min_hosts is not None:
        params["min_hosts"] = min_hosts
    
    return get_optimization_request("", params)


@router.get("/v1/optimization/summary")
def get_optimization_summary():
    """Get optimization summary statistics"""
    return get_optimization_request("/summary")


@router.get("/v1/optimization/technologies")
def get_optimization_technologies():
    """Get distinct technologies for filtering"""
    return get_optimization_request("/technologies")
