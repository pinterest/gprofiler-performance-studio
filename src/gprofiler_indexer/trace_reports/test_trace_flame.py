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
"""Offline tests for trace_flame.py: python3 -m unittest test_trace_flame (from this directory)."""

import json
import os
import re
import tempfile
import unittest

import trace_flame as tf

T0 = 1_762_500_000_000_000  # us
EVIL = "</script><img src=x onerror=alert(1)>"


def span(sid, parent, kind, service, name, start_ms, dur_ms, **tags):
    s = {"traceId": "abc123", "id": sid, "kind": kind, "name": name, "localEndpoint": {"serviceName": service},
         "timestamp": T0 + start_ms * 1000, "duration": dur_ms * 1000, "tags": tags}
    if parent:
        s["parentId"] = parent
    return s


SPANS = [
    span("a", None, "SERVER", "frontend", "/api/items", 0, 100, server_host_type="frontend-prod"),
    span("d", "a", "LOCAL", "frontend", EVIL, 1, 5),
    span("b", "a", "CLIENT", "frontend", "getitems", 10, 85),
    span("b", "a", "SERVER", "items", "getitems", 12, 80, server_host_type="items-prod"),
    span("c", "b", "LOCAL", "items", "com.example.store.ItemStore", 20, 50),
]

PROFILE = {"name": "all", "value": 10_000, "children": [
    {"name": "com/example/thrift/ItemService$Processor$getitems.process(ItemService.java)", "value": 4_000,
     "children": [{"name": "com/example/store/ItemStore.load(ItemStore.java)", "value": 3_000}]},
    {"name": "java/lang/Thread.run(Thread.java)", "value": 6_000},
]}


class FakeES:
    def trace(self, trace_id, date):
        if trace_id != "abc123":
            raise LookupError(trace_id)
        return [dict(s) for s in SPANS]


class SearchES(tf.ZipkinES):
    """Real query building over canned search responses: one client root joined to SPANS."""

    def __init__(self, cfg):
        super().__init__(cfg)
        self.bodies = []

    def search(self, index, body):
        self.bodies.append(body)
        if "track_total_hits" in body:
            return {"hits": {"total": {"value": 1234}}, "aggregations": {"p": {"values": {"50.0": 850e3, "90.0": 2100e3}}}}
        if "aggs" in body:
            return {"aggregations": {"t": {"buckets": [{"key": "abc123", "doc_count": 3}]}}}
        return {"hits": {"hits": [{"_source": {"traceId": "abc123", "name": "timing/home_render", "duration": 120e3}}]}}

    def trace(self, trace_id, date):
        root = span("r", None, "LOCAL", "web", "timing/home_render", 0, 120)
        return [root] + [dict(s, parentId=s.get("parentId", "r")) for s in SPANS]


class FakeStudio:
    def __init__(self):
        self.calls = []

    def flamegraph(self, service, s, e):
        self.calls.append(service)
        return PROFILE if service == "items-prod" else None


class TraceFlameTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "config.example.json")) as f:
            conf = json.load(f)
        conf["reports"] = [{"id": "items", "label": "items", "trace_id": "abc123"},
                           {"id": "missing", "trace_id": "nope"}]
        self.config = os.path.join(self.tmp.name, "config.json")
        with open(self.config, "w") as f:
            json.dump(conf, f)
        self.cfg = tf.Config(self.config)
        self.ap = tf.build_parser()

    def tearDown(self):
        self.tmp.cleanup()

    def test_render_joins_profile_and_escapes_span_names(self):
        out = os.path.join(self.tmp.name, "r.html")
        args = self.ap.parse_args(["--config", self.config, "--trace-id", "abc123", "--out", out, "--quiet"])
        src = FakeStudio()
        meta = tf.render(self.cfg, FakeES(), src, ["items-prod"], args)

        self.assertEqual(meta["trace_id"], "abc123")
        self.assertEqual(meta["n_spans"], 5)  # the CLIENT and SERVER halves of span "b" are separate nodes
        self.assertEqual(meta["wall_ms"], 100)
        self.assertIn("items-prod", meta["embedded_profiles"])
        self.assertEqual(src.calls, ["items-prod"])
        with open(out) as f:
            page = f.read()
        self.assertNotIn(EVIL, page)
        self.assertEqual(len(re.findall(r"</script>", page, re.I)), len(re.findall(r"<script\b", page, re.I)))
        self.assertIn("ItemStore", page)

    def test_batch_writes_manifest_and_isolates_failures(self):
        out_dir = os.path.join(self.tmp.name, "out")
        rc = tf.run_batch(self.cfg, FakeES(), None, [], self.ap, out_dir)

        self.assertEqual(rc, 0)
        with open(os.path.join(out_dir, "manifest.json")) as f:
            reports = {r["id"]: r for r in json.load(f)["reports"]}
        self.assertEqual(reports["items"]["file"], "items.html")
        self.assertTrue(os.path.exists(os.path.join(out_dir, "items.html")))
        self.assertNotIn("file", reports["missing"])
        self.assertIn("LookupError", reports["missing"]["error"])

    def test_root_report_filters_by_tags_and_records_path_meta(self):
        es = SearchES(self.cfg)
        args = tf.report_args(self.ap, {"id": "web-home-desktop", "root": "home_render", "platform": "web",
                                        "device": "desktop", "tags": {"device.typeName": "desktop"}}, self.tmp.name)
        meta = tf.render(self.cfg, es, None, [], args)

        self.assertEqual(meta["platform"], "web")
        self.assertEqual(meta["device"], "desktop")
        self.assertEqual(meta["flow"], "timing/home_render")
        self.assertEqual(meta["stats"], {"window_hours": 6, "count": 1234, "p50_ms": 850, "p90_ms": 2100})
        roots_and_stats = [b for b in es.bodies if "aggs" not in b or "track_total_hits" in b]
        self.assertEqual(len(roots_and_stats), 2)
        for body in roots_and_stats:
            self.assertIn({"term": {"_q": "device.typeName=desktop"}}, body["query"]["bool"]["filter"])

    def test_report_args_rejects_bad_specs(self):
        for spec in ({"id": "../x", "root": "r"}, {"id": "ok", "root": "r", "slowest": "s"},
                     {"id": "ok"}, {"id": "ok", "root": "r", "out": "/etc/passwd"},
                     {"id": "ok", "root": "r", "tags": ["device=phone"]}, {"id": "ok", "root": "r", "tags": {"k": 1}}):
            with self.assertRaises(ValueError, msg=spec):
                tf.report_args(self.ap, spec, self.tmp.name)

    def test_hosts_file_url_follows_file_and_overrides_port(self):
        hosts = os.path.join(self.tmp.name, "hosts")
        with open(hosts, "w") as f:
            f.write("# comment\n10.0.0.5:9192\n\n")
        self.assertEqual(tf.hosts_file_url(hosts, 9092), "http://10.0.0.5:9092")
        self.assertEqual(tf.hosts_file_url(hosts, None), "http://10.0.0.5:9192")
        self.assertEqual(tf.hosts_file_url(os.path.join(self.tmp.name, "missing"), 9092), "")

    def test_script_json_cannot_close_script(self):
        self.assertNotIn("</", tf.script_json({"n": EVIL}))
        self.assertEqual(json.loads(tf.script_json({"n": EVIL})), {"n": EVIL})


if __name__ == "__main__":
    unittest.main()
