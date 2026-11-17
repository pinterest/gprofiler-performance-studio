#!/usr/bin/env python3
"""Sessions from several hosts collapse to one dropdown entry per name."""

import json
import sys
from pathlib import Path

import pytest

_SRC_ROOT = Path(__file__).resolve().parents[3]
_pkg = _SRC_ROOT / "gprofiler"
if str(_pkg) not in sys.path:
    sys.path.insert(0, str(_pkg))

from backend.utils.nsys_sessions import aggregate_nsys_sessions


def test_groups_the_same_session_across_hosts():
    rows = [
        {
            "hostname": "host-b",
            "nsys_sessions": [{"name": "gprofiler-cuda-burn", "state": "Collection"}],
        },
        {
            "hostname": "host-a",
            "nsys_sessions": json.dumps(
                [
                    {"name": "gprofiler-cuda-burn", "state": "Collection"},
                    {"name": "other", "state": "Idle"},
                ]
            ),
        },
    ]
    assert aggregate_nsys_sessions(rows) == [
        {"name": "gprofiler-cuda-burn", "state": "Collection", "hosts": ["host-b", "host-a"]},
        {"name": "other", "state": "Idle", "hosts": ["host-a"]},
    ]


def test_skips_blank_names():
    assert aggregate_nsys_sessions([{"hostname": "h", "nsys_sessions": [{"name": "  "}]}]) == []
