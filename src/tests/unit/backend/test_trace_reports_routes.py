#!/usr/bin/env python3
"""
Unit tests for /api/trace_reports, run in-process with S3 stubbed out:

    PYTHONPATH=src/gprofiler:src/gprofiler-dev pytest src/tests/unit/backend/test_trace_reports_routes.py
"""

import gzip
import json

import pytest
from botocore.exceptions import ClientError
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import trace_reports_routes

GOOD = "web-home/20261107T2100Z.html"


class FakeS3:
    objects = {}

    def __init__(self, logger):
        pass

    def get_object(self, key, is_gzip=False):
        if key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        body = self.objects[key]
        return (gzip.decompress(body) if is_gzip else body).decode()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(trace_reports_routes, "S3ProfileDal", FakeS3)
    monkeypatch.setattr(trace_reports_routes, "S3_PATH_PREFIX", "")
    FakeS3.objects = {}
    app = FastAPI()
    app.include_router(trace_reports_routes.router, prefix="/api/trace_reports")
    return TestClient(app)


def test_list_without_index_is_empty(client):
    assert client.get("/api/trace_reports").json() == {"updated_at": None, "reports": [], "errors": []}


def test_list_drops_entries_with_unexpected_paths(client):
    FakeS3.objects["trace_reports/index.json"] = json.dumps(
        {"updated_at": "2026-11-07T21:00:00Z", "reports": [{"id": "web-home", "path": GOOD}, {"path": "../x.html"}]}
    ).encode()
    assert [r["path"] for r in client.get("/api/trace_reports").json()["reports"]] == [GOOD]


def test_html_is_served_sandboxed(client):
    FakeS3.objects[f"trace_reports/{GOOD}"] = gzip.compress(b"<html>report</html>")
    r = client.get("/api/trace_reports/html", params={"path": GOOD})
    assert r.status_code == 200
    assert r.text == "<html>report</html>"
    assert r.headers["content-security-policy"].startswith("sandbox allow-scripts")
    assert "allow-same-origin" not in r.headers["content-security-policy"]


@pytest.mark.parametrize("path", ["../index.json", "index.json", "a/b/20261107T2100Z.html",
                                  "Web/20261107T2100Z.html", f"{GOOD}/../../secret"])
def test_html_rejects_paths_outside_reports(client, path):
    assert client.get("/api/trace_reports/html", params={"path": path}).status_code == 400


def test_html_missing_report_is_404(client):
    assert client.get("/api/trace_reports/html", params={"path": GOOD}).status_code == 404
