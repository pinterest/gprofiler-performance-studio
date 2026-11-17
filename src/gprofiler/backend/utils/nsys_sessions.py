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

"""Group per-host `nsys sessions list` reports for the profiling dropdown."""

import json
from typing import Any, Dict, List


def aggregate_nsys_sessions(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse host rows into one entry per session name.

    Each result is ``{name, state, hosts}``. Hosts are the fresh hosts that
    reported that name. ``state`` is the first non-empty state seen.
    """
    grouped: Dict[str, Dict[str, Any]] = {}
    for row in rows or []:
        raw = row.get("nsys_sessions") or []
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                continue
        if not isinstance(raw, list):
            continue
        host = row.get("hostname")
        for item in raw:
            if isinstance(item, str):
                name, state = item.strip(), ""
            elif isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                state = str(item.get("state") or "").strip()
            else:
                continue
            if not name:
                continue
            entry = grouped.get(name)
            if entry is None:
                entry = {"name": name, "state": state, "hosts": []}
                grouped[name] = entry
            elif state and not entry["state"]:
                entry["state"] = state
            if host and host not in entry["hosts"]:
                entry["hosts"].append(host)
    return [grouped[name] for name in sorted(grouped)]
