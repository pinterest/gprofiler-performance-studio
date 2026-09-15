#!/bin/sh

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

# Runs logrotate only when LOGROTATE_ENABLED=true. Log rotation is opt-in so
# deployments that already rotate these logs externally can skip it entirely.
# cron jobs do not inherit the container env; load the flag from the file the
# container start-up persisted (see periodic_tasks/Dockerfile).
if [ -f /tmp/cron.env ]; then
    while IFS='=' read -r _k _v; do
        case "$_k" in LOGROTATE_ENABLED) export "$_k=$_v" ;; esac
    done < /tmp/cron.env
fi

if [ "$LOGROTATE_ENABLED" = "true" ]; then
    # State file under /tmp: the container runs as non_root, which cannot write
    # logrotate's default /var/lib/logrotate.status.
    /usr/sbin/logrotate -s /tmp/logrotate.status /etc/logrotate.conf
fi
