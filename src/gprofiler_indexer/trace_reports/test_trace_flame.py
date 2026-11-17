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

import trace_advisor as advisor
import trace_flame as tf

T0 = 1_762_500_000_000_000  # us
EVIL = "</script><img src=x onerror=alert(1)>"


def span(sid, parent, kind, service, name, start_ms, dur_ms, **tags):
    s = {
        "traceId": "abc123",
        "id": sid,
        "kind": kind,
        "name": name,
        "localEndpoint": {"serviceName": service},
        "timestamp": T0 + start_ms * 1000,
        "duration": dur_ms * 1000,
        "tags": tags,
    }
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

PROFILE = {
    "name": "all",
    "value": 10_000,
    "children": [
        {
            "name": "com/example/thrift/ItemService$Processor$getitems.process(ItemService.java)",
            "value": 4_000,
            "children": [{"name": "com/example/store/ItemStore.load(ItemStore.java)", "value": 3_000}],
        },
        {"name": "java/lang/Thread.run(Thread.java)", "value": 6_000},
    ],
}


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
            return {
                "hits": {"total": {"value": 1234}},
                "aggregations": {"p": {"values": {"50.0": 850e3, "90.0": 2100e3, "99.0": 6000e3}}},
            }
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
        conf["reports"] = [
            {"id": "items", "label": "items", "trace_id": "abc123"},
            {"id": "missing", "trace_id": "nope"},
        ]
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
        args = tf.report_args(
            self.ap,
            {
                "id": "web-home-desktop",
                "root": "home_render",
                "platform": "web",
                "device": "desktop",
                "tags": {"device.typeName": "desktop"},
            },
            self.tmp.name,
        )
        meta = tf.render(self.cfg, es, None, [], args)

        self.assertEqual(meta["platform"], "web")
        self.assertEqual(meta["device"], "desktop")
        self.assertEqual(meta["flow"], "timing/home_render")
        self.assertEqual(meta["sample"], "tail")
        self.assertEqual(
            meta["stats"], {"window_hours": 6, "count": 1234, "p50_ms": 850, "p90_ms": 2100, "p99_ms": 6000}
        )
        roots_and_stats = [b for b in es.bodies if "aggs" not in b or "track_total_hits" in b]
        self.assertEqual(len(roots_and_stats), 2)
        for body in roots_and_stats:
            self.assertIn({"term": {"_q": "device.typeName=desktop"}}, body["query"]["bool"]["filter"])

    def test_typical_sample_picks_roots_near_the_percentile(self):
        es = SearchES(self.cfg)
        args = tf.report_args(
            self.ap, {"id": "web-home-p50", "root": "home_render", "platform": "web", "sample": "p50"}, self.tmp.name
        )
        meta = tf.render(self.cfg, es, None, [], args)

        self.assertEqual(meta["sample"], "p50")
        roots = next(b for b in es.bodies if b.get("size") == 1000)
        self.assertIn("random_score", roots["query"]["function_score"])
        flt = roots["query"]["function_score"]["query"]["bool"]["filter"]
        dur = next(f["range"]["duration"] for f in flt if "duration" in f.get("range", {}))
        self.assertEqual(
            dur, {"gte": int(850 * (1 - tf.SAMPLE_BAND)) * 1000, "lte": (int(850 * (1 + tf.SAMPLE_BAND)) + 1) * 1000}
        )

    def test_llm_recommendations_are_embedded_escaped(self):
        class FakeLLM(advisor.LLM):
            def __init__(self):
                super().__init__({"url": "http://llm.invalid/v1/chat/completions", "model": "m1"})
                self.prompts = []

            def complete(self, system, user):
                self.prompts.append(user)
                return json.dumps(
                    {
                        "summary": EVIL,
                        "recommendations": [
                            {
                                "title": "Batch getitems " + EVIL,
                                "evidence": ["F1", "F999"],
                                "est_savings_ms": 40,
                                "confidence": "high",
                            }
                        ],
                    }
                )

        llm = FakeLLM()
        out = os.path.join(self.tmp.name, "r.html")
        args = self.ap.parse_args(["--config", self.config, "--trace-id", "abc123", "--out", out, "--quiet"])
        meta = tf.render(self.cfg, FakeES(), FakeStudio(), ["items-prod"], args, llm)

        self.assertEqual(len(llm.prompts), 1)
        self.assertIn("items-prod", llm.prompts[0])
        self.assertEqual(meta["advice"]["model"], "m1")
        self.assertTrue(meta["advice"]["top"][0].startswith("Batch getitems"))
        with open(out) as f:
            page = f.read()
        self.assertNotIn(EVIL, page)
        self.assertEqual(len(re.findall(r"</script>", page, re.I)), len(re.findall(r"<script\b", page, re.I)))
        advice_js = json.loads(re.search(r"const ADVICE = (.*);", page).group(1))
        self.assertEqual(advice_js["llm"]["recommendations"][0]["evidence"], ["F1"])  # unknown ids dropped
        self.assertIn("Report context (JSON)", advice_js["prompt"])

    def test_report_args_rejects_bad_specs(self):
        for spec in (
            {"id": "../x", "root": "r"},
            {"id": "ok", "root": "r", "slowest": "s"},
            {"id": "ok"},
            {"id": "ok", "root": "r", "out": "/etc/passwd"},
            {"id": "ok", "root": "r", "tags": ["device=phone"]},
            {"id": "ok", "root": "r", "tags": {"k": 1}},
            {"id": "ok", "root": "r", "sample": "median"},
            {"id": "ok", "root": "r", "sample": "p100"},
        ):
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


class AdvisorTest(unittest.TestCase):
    def setUp(self):
        here = os.path.dirname(os.path.abspath(__file__))
        self.cfg = tf.Config(os.path.join(here, "config.example.json"))

    def tree(self, spans):
        root, nodes = tf.build_tree(spans, self.cfg)
        parents = {}

        def link(n):
            for c in n.children:
                parents[id(c)] = n
                link(c)

        link(root)
        return root, nodes, parents

    def test_fan_out_sequential_calls_and_critical_path(self):
        # api-prod handler: six back-to-back 30 ms calls to items, then 40 ms of its own work.
        spans = [span("a", None, "SERVER", "api", "/feed", 0, 230, server_host_type="api-prod")]
        for i in range(6):
            sid = f"c{i}"
            spans += [
                span(sid, "a", "CLIENT", "api", "getitem", 10 + 30 * i, 30),
                span(sid, "a", "SERVER", "items", "getitem", 11 + 30 * i, 28, server_host_type="items-prod"),
            ]
        root, nodes, parents = self.tree(spans)
        ranked = tf.rank_clusters(nodes, parents, 3)
        info = {a["cluster"]: {"profiled": False, "proxy": None} for a in ranked}
        found = advisor.find(root, parents, ranked, info, {}, [], 0, [], advisor.rules_config({}))
        by_rule = {f["rule"]: f for f in found}

        self.assertEqual(found[0]["rule"], "critical_path")
        self.assertEqual(found[0]["id"], "F1")
        self.assertIn("items-prod", found[0]["detail"])
        self.assertIn("6× api: getitem", by_rule["fan_out"]["title"])
        self.assertIn("mostly serial", by_rule["fan_out"]["detail"])
        self.assertEqual(by_rule["sequential_calls"]["impact_ms"], 150)
        self.assertIn("unprofiled", by_rule)
        # A rule set to false is off; thresholds are overridable.
        rules = advisor.rules_config({"rules": {"fan_out": False, "sequential_calls": {"min_calls": 7}}})
        found = advisor.find(root, parents, ranked, info, {}, [], 0, [], rules)
        self.assertFalse({"fan_out", "sequential_calls"} & {f["rule"] for f in found})

    def test_critical_path_follows_concurrent_server_work_not_a_childless_wait(self):
        # The app's request span has no children; the request's server side hangs off the page root.
        spans = [
            span("r", None, "LOCAL", "web", "timing/related", 0, 1000),
            span("q", "r", "CLIENT", "web", "first_page_load", 0, 1000),
            span("e", "r", "SERVER", "cdn", "edge", 10, 900),
            span("e2", "e", "SERVER", "api", "/related", 50, 800, server_host_type="api-prod"),
        ]
        root, nodes, parents = self.tree(spans)
        held = {n.label(): us // 1000 for n, us in advisor.critical_path(root)}

        self.assertEqual(held["api: /related"], 800)
        self.assertEqual(held["cdn: edge"], 100)
        self.assertEqual(held["web: first_page_load"], 100)
        self.assertEqual(advisor.owner_of(next(n for n in nodes if n.service == "cdn"), parents), "cdn")

    def test_parse_recommendations_keeps_only_known_shape(self):
        content = "Sure:\n" + json.dumps(
            {
                "summary": "s" * 2000,
                "recommendations": [
                    "not a dict",
                    {"title": "t", "evidence": ["F1", "F7", 3], "est_savings_ms": True, "confidence": "certain"},
                ],
            }
        )
        out = advisor.parse_recommendations(content, {"F1", "F2"})
        self.assertEqual(len(out["summary"]), 600)
        self.assertEqual(
            out["recommendations"],
            [
                {
                    "title": "t",
                    "why": "",
                    "action": "",
                    "owner": "",
                    "evidence": ["F1"],
                    "est_savings_ms": None,
                    "confidence": None,
                }
            ],
        )

    def test_llm_disabled_without_url_or_model(self):
        self.assertFalse(advisor.LLM({}).enabled)
        self.assertFalse(advisor.LLM({"url": "http://x", "model": "m", "enabled": False}).enabled)
        self.assertTrue(advisor.LLM({"url": "http://x", "model": "m"}).enabled)


class ProfileWindowTest(unittest.TestCase):
    def test_window_follows_the_server_clock_not_the_skew_corrected_one(self):
        cfg = tf.Config(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.example.json"))
        # The client's clock is an hour behind the server's: skew correction moves the server
        # subtree into the client span for display, but its profiles are an hour later.
        hour_ms = 3600 * 1000
        spans = [
            span("r", None, "CLIENT", "web", "pwt/home_feed_render", 0, 1000),
            span("r", None, "SERVER", "api", "/feed", hour_ms + 100, 800, server_host_type="api-prod"),
        ]
        root, nodes = tf.build_tree(spans, cfg)
        parents = {}

        def link(n):
            for c in n.children:
                parents[id(c)] = n
                link(c)

        link(root)
        server = next(n for n in nodes if n.cluster == "api-prod")
        self.assertLess(abs(server.start - root.start), 1_000_000)  # displayed inside the client span

        (api,) = tf.rank_clusters(nodes, parents, 3)
        s, e = api["window"]
        server_start = tf.us_to_dt(T0 + (hour_ms + 100) * 1000)
        self.assertLessEqual(s, server_start)
        self.assertGreaterEqual(e, server_start)


if __name__ == "__main__":
    unittest.main()
