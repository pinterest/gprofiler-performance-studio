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
import logging

import anyio
from backend import config, routers
from backend.utils.metrics_publisher import MetricsPublisher
from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


def format_time(dt: datetime):
    iso_format = dt.isoformat()
    if "+" in iso_format:
        return iso_format.replace("+00:00", "Z") if "+00:00" in iso_format else iso_format
    return iso_format + "Z"


app = FastAPI(openapi_url="/api/v1/openapi.json", docs_url="/api/v1/docs")


@app.on_event("startup")
async def startup_event():
    """Initialize services on application startup."""
    # Size the sync-route threadpool per worker. Each thread owns one Postgres connection,
    # so this is also the number of DB connections per worker process (fleet total =
    # containers x GUNICORN_PROCESS_COUNT x this). 0 keeps the framework default (40).
    if config.WEBAPP_THREAD_POOL_SIZE > 0:
        anyio.to_thread.current_default_thread_limiter().total_tokens = config.WEBAPP_THREAD_POOL_SIZE
        logger.info("Webapp threadpool size set to %s", config.WEBAPP_THREAD_POOL_SIZE)

    # Initialize MetricsPublisher
    metrics_publisher = MetricsPublisher(
        server_url=config.METRICS_AGENT_URL,
        service_name=config.METRICS_SERVICE_NAME,
        sli_metric_uuid=config.METRICS_SLI_UUID,
        enabled=config.METRICS_ENABLED
    )
    
    # Log initialization status
    publisher = MetricsPublisher.get_instance()
    if publisher:
        logger.info(
            f"MetricsPublisher initialized: service={config.METRICS_SERVICE_NAME}, "
            f"server={config.METRICS_AGENT_URL}"
        )
    else:
        logger.info("MetricsPublisher disabled")

    # Forward heartbeat inventory outcomes (synced / unchanged / deferred / not_reported) to the
    # metrics publisher. "deferred" climbing and then draining to ~0 is the signal to watch when
    # raising GPROFILER_INVENTORY_SYNC_PER_SEC after a rollout.
    from gprofiler_dev.postgres.db_manager import DBManager as _DBManager

    def _publish_inventory_outcome(outcome: str, count: int) -> None:
        metrics_publisher.send_counter("heartbeat_inventory", count, {"outcome": outcome})

    _DBManager.inventory_outcome_hook = staticmethod(_publish_inventory_outcome)


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup services on application shutdown."""
    # Cleanup MetricsPublisher
    publisher = MetricsPublisher.get_instance()
    if publisher:
        publisher.flush_and_close()
        logger.info("MetricsPublisher closed")


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(_, exc):
    if exc.status_code == 204:
        return Response(status_code=204)
    return JSONResponse(exc.detail, status_code=exc.status_code)


app.include_router(routers.router, prefix="/api")
