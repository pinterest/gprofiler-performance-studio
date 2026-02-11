#!/usr/bin/env bash

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

set -ueo pipefail

CURRENT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
LISTEN_PORT="${NGINX_PORT:-80}"

# TLS certificate reload configuration
ENABLE_CERT_RELOAD="${GPROFILER_ENABLE_CERT_RELOAD:-false}"
CERT_RELOAD_PERIOD="${GPROFILER_CERT_RELOAD_PERIOD:-21600}"  # Default: 6 hours (21600 seconds)

if [[ "${GUNICORN_PROCESS_COUNT:-}" == "" ]]; then
    GUNICORN_PROCESS_COUNT=$(nproc)
    echo GUNICORN_PROCESS_COUNT not specified. Using "${GUNICORN_PROCESS_COUNT}"
fi
if [[ "${GUNICORN_DD_STATSD_HOST:-}" == "" ]]; then
   GUNICORN_DD_STATSD_HOST="localhost:8125"
   echo GUNICORN_DD_STATSD_HOST not specified. Using "${GUNICORN_DD_STATSD_HOST}"
fi
if [[ "${GUNICORN_MAX_REQUESTS:-}" == "" ]]; then
   GUNICORN_MAX_REQUESTS=10000
   echo GUNICORN_MAX_REQUESTS not specified. Using "${GUNICORN_MAX_REQUESTS}"
fi
if [[ "${GUNICORN_LOG_LEVEL:-}" == "" ]]; then
    GUNICORN_LOG_LEVEL="warning"
   echo GUNICORN_LOG_LEVEL not specified. Using "${GUNICORN_LOG_LEVEL}"
fi
gunicorn_pid_file=/var/run/gunicorn.pid

gunicorn_cmd_line=" --workers=${GUNICORN_PROCESS_COUNT} \
               --bind=unix:/tmp/mysite.sock \
               --worker-class=uvicorn.workers.UvicornWorker \
               --statsd-host=${GUNICORN_DD_STATSD_HOST} \
               --name=gprofiler_gunicorn \
               --max-requests=${GUNICORN_MAX_REQUESTS} \
               --max-requests-jitter=1000 \
               --timeout=300 \
               --preload \
               --forwarded-allow-ips=* \
               --log-level=${GUNICORN_LOG_LEVEL} \
               --pid=${gunicorn_pid_file}"

function clean_up {
    kill "${GUNICORN_PID}"
    kill "${NGINX_PID}"
    if [[ "${CERT_RELOAD_PID:-}" != "" ]]; then
        kill "${CERT_RELOAD_PID}" 2>/dev/null || true
    fi
    exit
}
trap clean_up SIGHUP SIGINT SIGTERM
rm -f ${gunicorn_pid_file}

# Background process for periodic certificate reload
function reload_certificates {
    while true; do
        sleep "${CERT_RELOAD_PERIOD}"
        echo "Reloading NGINX to refresh TLS certificates"
        nginx -s reload
    done
}

cd ${CURRENT_DIR} && gunicorn backend.main:app ${gunicorn_cmd_line} &
GUNICORN_PID=$!

# Prepare nginx config with TLS settings from environment variables
/usr/local/bin/prepare-nginx-config.sh

nginx -g "daemon off;" &
NGINX_PID=$!

# Start certificate reload process if enabled
if [[ "${ENABLE_CERT_RELOAD}" == "true" ]]; then
    echo "Certificate auto-reload enabled (period: ${CERT_RELOAD_PERIOD} seconds)"
    reload_certificates &
    CERT_RELOAD_PID=$!
else
    echo "Certificate auto-reload disabled"
fi

wait ${GUNICORN_PID} ${NGINX_PID}
