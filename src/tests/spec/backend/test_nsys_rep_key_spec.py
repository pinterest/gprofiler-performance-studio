#!/usr/bin/env python3
"""
Spec tests for the S3 key of raw .nsys-rep uploads.

The key must live under the same base directory as every other profile file
(S3_PATH_PREFIX + "products"). Bucket policies may only allow writes under that
prefix; a bare "products/..." key was rejected with AccessDenied in dev.

    cd src && python -m pytest tests/spec/backend/test_nsys_rep_key_spec.py -v
"""

import sys
from datetime import datetime
from pathlib import Path

import pytest

_SRC_ROOT = Path(__file__).resolve().parents[3]
for _pkg_root in (_SRC_ROOT / "gprofiler", _SRC_ROOT / "gprofiler-dev"):
    if str(_pkg_root) not in sys.path:
        sys.path.insert(0, str(_pkg_root))

pytest.importorskip("fastapi", reason="fastapi is required to import the nsys routes")
pytest.importorskip("boto3", reason="boto3 is required to import gprofiler_dev")

try:
    from gprofiler_dev import config
    from backend.routers.nsys_routes import nsys_rep_s3_key
except Exception as exc:  # pragma: no cover - environment guard
    pytest.skip(f"backend.routers.nsys_routes not importable: {exc}", allow_module_level=True)

START = datetime(2026, 11, 13, 23, 49, 25, 123456)


def test_key_includes_s3_path_prefix(monkeypatch):
    monkeypatch.setattr(config, "S3_PATH_PREFIX", "retention-365days")
    assert (
        nsys_rep_s3_key("devapp", START, "devrestricted-lassis-1")
        == "retention-365days/products/devapp/stacks/nsys/2026-11-13T23:49:25_devrestricted-lassis-1.nsys-rep"
    )


def test_key_without_prefix(monkeypatch):
    monkeypatch.setattr(config, "S3_PATH_PREFIX", "")
    assert (
        nsys_rep_s3_key("devapp", START, "host/a b")
        == "products/devapp/stacks/nsys/2026-11-13T23:49:25_host-a-b.nsys-rep"
    )
