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

# Set default values for TLS configuration
# These defaults work for development and can be overridden in production
# Override these environment variables to use custom certificate paths

export GPROFILER_TLS_CERT_PATH="${GPROFILER_TLS_CERT_PATH:-/usr/src/app/certs/server.crt}"
export GPROFILER_TLS_KEY_PATH="${GPROFILER_TLS_KEY_PATH:-/usr/src/app/certs/server.key}"
export GPROFILER_TLS_CA_PATH="${GPROFILER_TLS_CA_PATH:-/usr/src/app/certs/ca.crt}"
export GPROFILER_TLS_VERIFY_CLIENT="${GPROFILER_TLS_VERIFY_CLIENT:-optional}"
export HTTPS_PORT="${HTTPS_PORT:-443}"
export LISTEN_PORT="${LISTEN_PORT:-80}"

# Auto-detect HTTPS based on certificate file presence
if [[ -f "${GPROFILER_TLS_CERT_PATH}" && -r "${GPROFILER_TLS_CERT_PATH}" && \
      -f "${GPROFILER_TLS_KEY_PATH}" && -r "${GPROFILER_TLS_KEY_PATH}" ]]; then
    echo "HTTPS/mTLS Configuration:"
    echo "  HTTPS Port: ${HTTPS_PORT}"
    echo "  HTTP Port: ${LISTEN_PORT}"
    echo "  Certificate: ${GPROFILER_TLS_CERT_PATH}"
    echo "  Key: ${GPROFILER_TLS_KEY_PATH}"
    echo "  CA: ${GPROFILER_TLS_CA_PATH}"
    echo "  Verify Client: ${GPROFILER_TLS_VERIFY_CLIENT}"
    
    # Use HTTPS template (includes both HTTPS and HTTP server blocks)
    envsubst '${GPROFILER_TLS_CERT_PATH} ${GPROFILER_TLS_KEY_PATH} ${GPROFILER_TLS_CA_PATH} ${GPROFILER_TLS_VERIFY_CLIENT} ${HTTPS_PORT} ${LISTEN_PORT}' \
      < /etc/nginx/https_nginx.conf.template > /etc/nginx/nginx.conf
else
    echo "HTTPS/mTLS disabled - certificate files not found or not readable"
    echo "  Looked for certificate: ${GPROFILER_TLS_CERT_PATH}"
    echo "  Looked for key: ${GPROFILER_TLS_KEY_PATH}"
    echo "  Using HTTP only on port: ${LISTEN_PORT}"
    
    # Use HTTP-only template
    envsubst '${LISTEN_PORT}' < /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf
fi

echo "NGINX configuration prepared"
