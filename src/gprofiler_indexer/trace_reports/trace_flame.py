#!/usr/bin/env python3
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
"""
trace_flame.py — render a Zipkin trace as a flame graph and join each span to the
gProfiler profile of its cluster on *function names*, not trace ids.

Join model:
    key    = (cluster, time window)   -> span.tags.<cluster_tag> == gProfiler service name
    symbol = class / method name on the span  <->  frame names in the profile
A span named by a fully-qualified class, or by a method / RPC name, is looked up in its
cluster's flame graph for that window: inclusive CPU share, matching frames, hottest leaves
under them; the deep link into Performance Studio pre-fills the frame search.

Output is a self-contained HTML page (d3-flame-graph). The indexer runs this hourly in
--batch mode (see trace_reports.go) and uploads the pages to S3 for the Studio "Trace
Profiles" view.

Everything deployment-specific (trace store URL, cluster tag, package prefixes, proxy
clusters, Studio URL, which traces to render) comes from --config; see
config.example.json. Credentials are read from environment variables named in the config.

Usage
  python3 trace_flame.py --config config.json --trace-id <id> --out trace.html
  python3 trace_flame.py --config config.json --slowest <trace-service> --op <span-name> --out trace.html
  python3 trace_flame.py --config config.json --root <root-span-name> --platform <client-service> --out t.html
  python3 trace_flame.py --config config.json --batch --out-dir <dir>   # every entry of config "reports"
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import html
import json
import os
import random
import re
import sys
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlencode

import trace_advisor

# --------------------------------------------------------------------------- config


def hosts_file_url(path: Optional[str], port: Optional[int]) -> str:
    if not path:
        return ""
    try:
        with open(path) as f:
            hosts = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    except OSError:
        return ""
    if not hosts:
        return ""
    host, _, listed = random.choice(hosts).partition(":")
    return f"http://{host}:{port or listed or 80}"


class Config:
    def __init__(self, path: str):
        with open(path) as f:
            c = json.load(f)
        ts = c["trace_store"]
        self.es_url: str = ts["url"].rstrip("/")
        self.index_pattern: str = ts.get("index_pattern", "zipkin-span-{date}")
        self.username_env: str = ts.get("username_env", "TRACE_STORE_USERNAME")
        self.password_env: str = ts.get("password_env", "TRACE_STORE_PASSWORD")
        self.cluster_tag: str = ts.get("cluster_tag", "server_host_type")
        # Some tracers put the serving cluster under a different tag on SERVER spans; tried in order.
        self.cluster_tags: List[str] = [self.cluster_tag] + list(ts.get("cluster_tag_fallbacks", []))
        self.host_tag: Optional[str] = ts.get("host_tag")
        cl = c.get("client", {})
        # Client-side (browser/app) services whose flat resource-timing leaves get grouped by name,
        # and an optional span-name prefix shared by their user-facing timing roots.
        self.client_services: List[str] = cl.get("services", [])
        self.root_prefix: str = cl.get("root_prefix", "")
        ps = c.get("profile_source", {})
        # Studio API base URL used to read profiles (/api/flamegraph, /api/services): the env variable,
        # else "url", else a host picked from the hosts file (one "host[:port]" per line, re-read every
        # run so replaced webapp hosts are followed) with "port" overriding the listed port.
        hosts_file = os.environ.get(ps.get("hosts_file_env", "TRACE_REPORTS_PROFILE_HOSTS_FILE")) or ps.get(
            "hosts_file"
        )
        self.profile_api_url: str = (
            os.environ.get(ps.get("url_env", "TRACE_REPORTS_PROFILE_API_URL"))
            or ps.get("url")
            or hosts_file_url(hosts_file, ps.get("port"))
        ).rstrip("/")
        self.profile_timeout_s: int = int(ps.get("timeout_seconds", 120))
        self.studio_url: str = c["studio_url"].rstrip("/")
        self.reports: List[Dict[str, Any]] = c.get("reports", [])
        fr = c.get("frames", {})
        self.app_prefixes: List[str] = fr.get("app_prefixes", [])
        self.framework_prefixes: List[str] = fr.get("framework_prefixes", [])
        self.proxy_overrides: Dict[str, str] = c.get("proxy_overrides", {})
        self.min_window_minutes: int = int(c.get("min_window_minutes", 3))
        self.advisor: Dict[str, Any] = c.get("advisor", {})
        esc = "|".join(re.escape(p) for p in self.app_prefixes) or "(?!)"
        # Application frames: JVM form "<prefix>pkg/Class.method(" built from app_prefixes, plus any
        # extra regexes from config with named groups (?P<cls>...) and (?P<meth>...) — e.g. py-spy's
        # "func (/path/to/module.py:123)" where cls is the module path.
        self.app_patterns = [re.compile(rf"^(?P<cls>(?:{esc})[\w/$]+)\.(?P<meth>[\w$<>]+)\(")]
        self.app_patterns += [re.compile(p) for p in fr.get("app_regexes", [])]
        fesc = "|".join(re.escape(p) for p in self.framework_prefixes) or "(?!)"
        self.framework = re.compile(rf"^(?:{fesc})")

    def match_app(self, name: str) -> Optional[Tuple[str, str]]:
        for p in self.app_patterns:
            m = p.match(name)
            if m:
                return m.group("cls"), m.group("meth")
        return None


SYNTHETIC = re.compile(r"lambda\$|access\$|\$\$Lambda|^\$|\$\d+$")
FQ_CLASS = re.compile(r"^[a-z][\w$]*(?:\.[\w$]+){2,}$")  # looks like a.b.c.ClassName (Zipkin lower-cases it)
IDENT = re.compile(r"^[a-z_][\w]*$")  # looks like a method/function name (Thrift op, Python func)
GENERIC_OPS = {
    "get",
    "post",
    "put",
    "delete",
    "wsgi",
    "call",
    "run",
    "execute",
    "process",
    "handle",
    "apply",
    "handler",
    "api",
    "tasks",
    "params",
    "encode",
    "decode",
    "init",
    "main",
    "unknown_function",
}


def op_frame_keys(cls: str, meth: str) -> Set[str]:
    """Lower-cased tokens under which an app frame can be joined to a span named by a method:
    the method itself, Thrift client send_/recv_ wrappers, and Thrift-generated per-method classes
    (Service$Processor$<op>, <Op>_args, <Op>_result)."""
    keys = set()
    m = meth.lower()
    keys.add(m)
    for pre in ("send_", "recv_", "process_"):
        if m.startswith(pre):
            keys.add(m[len(pre) :])
    if "." not in cls:  # JVM class (py-spy "classes" are file paths; their basename is not a symbol)
        seg = cls.rsplit("/", 1)[-1].rsplit("$", 1)[-1].lower()
        for suf in ("_args", "_result"):
            if seg.endswith(suf):
                seg = seg[: -len(suf)]
        keys.add(seg)
    return keys


def op_search_regex(*ops: str) -> str:
    """Studio frame-search regex (case-insensitive) for a method-named span; matches JVM
    "pkg/Class.meth(" / "Service$Processor$meth" and py-spy "meth (/path.py:N)" frame forms."""
    o = "|".join(re.escape(x) for x in ops if x)
    return rf"(?:^|[.$/])(?:send_|recv_|process_)?(?:{o})(?:_args|_result)?(?:[.($]| \()"


# --------------------------------------------------------------------------- trace store (Zipkin ES)


class ZipkinES:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._auth: Optional[str] = None

    def _creds(self) -> str:
        if self._auth:
            return self._auth
        u, p = os.environ.get(self.cfg.username_env), os.environ.get(self.cfg.password_env)
        self._auth = "Basic " + base64.b64encode(f"{u}:{p}".encode()).decode() if (u and p) else ""
        return self._auth

    def search(self, index: str, body: Dict[str, Any]) -> Dict[str, Any]:
        hdrs = {"Content-Type": "application/json"}
        if self._creds():
            hdrs["Authorization"] = self._auth
        req = urllib.request.Request(
            f"{self.cfg.es_url}/{index}/_search?ignore_unavailable=true", data=json.dumps(body).encode(), headers=hdrs
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)

    def indices(self, date: Optional[str]) -> List[str]:
        base = dt.date.fromisoformat(date) if date else dt.datetime.now(dt.timezone.utc).date()
        return [self.cfg.index_pattern.format(date=(base + dt.timedelta(days=d)).isoformat()) for d in (0, -1, 1)]

    def window_indices(self, date: Optional[str], hours: float) -> str:
        """Comma-joined daily indices covering `date` (or the last `hours` up to now) for one search."""
        if date:
            return self.indices(date)[0]
        now = dt.datetime.now(dt.timezone.utc)
        day, first = now.date(), (now - dt.timedelta(hours=hours or 24)).date()
        names = []
        while day >= first:
            names.append(self.cfg.index_pattern.format(date=day.isoformat()))
            day -= dt.timedelta(days=1)
        return ",".join(names)

    def trace(self, trace_id: str, date: Optional[str]) -> List[Dict[str, Any]]:
        page = 10000  # index.max_result_window; big traces need search_after paging
        # One search over adjacent days: a trace that crosses midnight UTC is split across indices.
        idx = ",".join(self.indices(date))
        spans: List[Dict[str, Any]] = []
        after: Optional[List[Any]] = None
        while True:
            body: Dict[str, Any] = {
                "size": page,
                "query": {"term": {"traceId": trace_id}},
                "sort": [{"timestamp_millis": "asc"}, {"_doc": "asc"}],
            }
            if after:
                body["search_after"] = after
            hits = self.search(idx, body).get("hits", {}).get("hits", [])
            spans.extend(h["_source"] for h in hits)
            if len(hits) < page:
                break
            after = hits[-1]["sort"]
        if not spans:
            raise LookupError(f"trace {trace_id} not found in {idx}")
        return spans

    def slowest(
        self, service: str, cluster: Optional[str], op: Optional[str], date: Optional[str], max_ms: int, limit: int
    ) -> List[Dict[str, Any]]:
        must: List[Dict[str, Any]] = [
            {"term": {"localEndpoint.serviceName": service}},
            {"match": {"kind": "SERVER"}},
            {"range": {"duration": {"lte": max_ms * 1000}}},
        ]
        if cluster:
            must.append({"term": {f"tags.{self.cfg.cluster_tag}": cluster}})
        if op:
            must.append({"term": {"name": op}})
        res = self.search(
            self.window_indices(date, 24),
            {
                "size": limit,
                "sort": [{"duration": {"order": "desc"}}],
                "query": {"bool": {"must": must}},
                "_source": ["traceId", "name", "duration", "timestamp_millis", f"tags.{self.cfg.cluster_tag}"],
            },
        )
        return [h["_source"] for h in res["hits"]["hits"]]

    def root_filter(
        self, root: str, platform: str, hours: float, min_age_min: float = 0, tags: Optional[Dict[str, str]] = None
    ) -> List[Dict[str, Any]]:
        """ES filter for client timing roots: client service + '<root_prefix><root>' span, from the
        last `hours` but at least `min_age_min` old, carrying every tag in `tags`."""
        pre = self.cfg.root_prefix
        name = root if (not pre or root.startswith(pre)) else pre + root
        flt: List[Dict[str, Any]] = [{"term": {"localEndpoint.serviceName": platform}}, {"term": {"name": name}}]
        # Zipkin indexes tag values only as "key=value" terms in _q.
        flt += [{"term": {"_q": f"{k}={v}"}} for k, v in sorted((tags or {}).items())]
        now = dt.datetime.now(dt.timezone.utc)
        rng: Dict[str, int] = {}
        if hours:
            rng["gte"] = int((now - dt.timedelta(hours=hours)).timestamp() * 1000)
        if min_age_min:
            rng["lte"] = int((now - dt.timedelta(minutes=min_age_min)).timestamp() * 1000)
        if rng:
            flt.append({"range": {"timestamp_millis": rng}})
        return flt

    def root_stats(
        self,
        root: str,
        platform: str,
        date: Optional[str],
        hours: float,
        tags: Optional[Dict[str, str]] = None,
        extra_pct: Optional[int] = None,
    ) -> Dict[str, Any]:
        """How hot a client timing root is: count and p50/p90/p99 duration over the last `hours`."""
        percents = sorted({50, 90, 99} | ({extra_pct} if extra_pct else set()))
        res = self.search(
            self.window_indices(date, hours),
            {
                "size": 0,
                "track_total_hits": True,
                "query": {"bool": {"filter": self.root_filter(root, platform, hours, 0, tags)}},
                "aggs": {"p": {"percentiles": {"field": "duration", "percents": percents}}},
            },
        )
        pct = res["aggregations"]["p"]["values"]
        out: Dict[str, Any] = {"window_hours": hours, "count": res["hits"]["total"]["value"]}
        for p in percents:
            out[f"p{p}_ms"] = round((pct.get(f"{float(p)}") or 0) / 1000)
        return out

    def root_candidates(
        self,
        root: str,
        platform: str,
        date: Optional[str],
        hours: float,
        max_ms: int,
        limit: int,
        min_age_min: float = 0,
        tags: Optional[Dict[str, str]] = None,
        min_ms: int = 0,
        random_seed: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Client timing roots (see root_filter; at least `min_age_min` old so profiles for the
        window are indexed) with min_ms <= duration <= max_ms that share a traceId with backend SERVER
        spans (the client stamped its trace id on the API calls). The slowest `limit` roots, or a
        random `limit` of them with random_seed. Returns slowest-first with the number of backend
        spans per trace."""
        idx = self.window_indices(date, hours)
        flt = self.root_filter(root, platform, hours, min_age_min, tags)
        flt.append({"range": {"duration": {"gte": min_ms * 1000, "lte": max_ms * 1000}}})
        query: Dict[str, Any] = {"bool": {"filter": flt}}
        if random_seed is None:
            order: List[Any] = [{"duration": {"order": "desc"}}]
        else:
            query = {
                "function_score": {
                    "query": query,
                    "boost_mode": "replace",
                    "random_score": {"seed": random_seed, "field": "_seq_no"},
                }
            }
            order = ["_score"]
        res = self.search(
            idx,
            {
                "size": limit,
                "sort": order,
                "query": query,
                "_source": ["traceId", "name", "duration", "timestamp_millis"],
            },
        )
        roots = sorted((h["_source"] for h in res["hits"]["hits"]), key=lambda r: -r["duration"])
        if not roots:
            return []
        ids = [r["traceId"] for r in roots]
        agg = self.search(
            idx,
            {
                "size": 0,
                "query": {
                    "bool": {
                        "filter": [{"terms": {"traceId": ids}}, {"exists": {"field": f"tags.{self.cfg.cluster_tag}"}}]
                    }
                },
                "aggs": {"t": {"terms": {"field": "traceId", "size": len(ids)}}},
            },
        )
        joined = {b["key"]: b["doc_count"] for b in agg["aggregations"]["t"]["buckets"]}
        out = []
        for r in roots:
            r["backend_spans"] = joined.get(r["traceId"], 0)
            r["total_roots"] = len(roots)
            if r["backend_spans"]:
                out.append(r)
        return out


# --------------------------------------------------------------------------- span tree


class Node:
    __slots__ = (
        "id",
        "kind",
        "parent_id",
        "service",
        "name",
        "start",
        "dur",
        "tags",
        "children",
        "self_us",
        "eff_us",
        "eff_start",
        "cluster",
        "host",
        "fq_class",
        "class_case",
        "members",
        "skew_us",
        "op_key",
        "op_alt",
    )

    def __init__(self, s: Dict[str, Any], cluster_tags: List[str], host_tag: Optional[str] = None):
        self.id = s.get("id") or s.get("spanId")
        self.kind = (s.get("kind") or "LOCAL").upper()
        self.parent_id = s.get("parentId")
        self.service = (s.get("localEndpoint") or {}).get("serviceName") or s.get("service") or "?"
        self.name = s.get("name") or "?"
        self.start = int(s.get("timestamp") or int(s.get("timestamp_millis", 0)) * 1000)  # us
        self.dur = int(s.get("duration") or 0)
        self.tags = s.get("tags") or {}
        self.children: List["Node"] = []
        self.self_us = 0
        self.eff_us = self.dur  # duration after clipping to the parent (see build_tree(clip=True))
        self.eff_start = self.start
        self.skew_us = 0  # clock-skew correction applied to this span (and its subtree), us
        self.members: Optional[List[Tuple[int, int]]] = None  # grouped leaf siblings: their intervals
        self.cluster = None
        if self.kind == "SERVER":
            self.cluster = next((self.tags[t] for t in cluster_tags if self.tags.get(t)), None)
        self.host = self.tags.get(host_tag) if host_tag and self.kind == "SERVER" else None
        # Span named by a fully-qualified class (e.g. DAG-node spans). Zipkin lower-cases the name; the
        # original case survives in the "<ClassName>_duration_ms" tag key.
        self.fq_class = self.name if FQ_CLASS.match(self.name) else None
        # Span named by a method (Thrift op, Python function, "module/func" path): joined to frames by
        # exact token on the last path segment. CLIENT spans join too (caller-side send_/recv_ cost).
        self.op_key: Optional[str] = None
        if not self.fq_class and self.kind in ("SERVER", "CLIENT"):
            seg = self.name.rsplit("/", 1)[-1].lower()
            if IDENT.match(seg) and seg not in GENERIC_OPS:
                self.op_key = seg
        self.op_alt: Optional[str] = None  # caller's name for the same RPC, when it differs (set in build_tree)
        self.class_case = None
        if self.fq_class:
            for k in self.tags:
                if k.endswith("_duration_ms"):
                    self.class_case = k[: -len("_duration_ms")]
                    break

    @property
    def end(self) -> int:
        return self.start + self.dur

    def label(self) -> str:
        return f"{self.service}: {self.name}"


def merge_intervals(iv: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    merged: List[List[int]] = []
    for a, b in sorted(iv):
        if b <= a:
            continue
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def union_len(iv: List[Tuple[int, int]]) -> int:
    return sum(b - a for a, b in merge_intervals(iv))


def intersect_len(a: List[Tuple[int, int]], b: List[Tuple[int, int]]) -> int:
    """Length of the intersection of two merged interval lists."""
    i = j = 0
    tot = 0
    while i < len(a) and j < len(b):
        lo, hi = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if hi > lo:
            tot += hi - lo
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return tot


def build_tree(
    spans: List[Dict[str, Any]], cfg: Config, clip: bool = False, group_services: Optional[List[str]] = None
) -> Tuple[Node, List[Node]]:
    """Span list -> tree. clip=True confines every span to its parent's interval (client resource
    timings routinely outlive the performance-timing root). group_services: services whose leaf
    siblings with the same name are folded into one '<name> ×N' node valued at the union of their
    intervals (parallel downloads are not N× the work)."""
    # Drop unfinished/unknown spans (no timestamp and no duration): they would stretch the trace to epoch 0.
    spans = [s for s in spans if (s.get("id") or s.get("spanId")) and (s.get("timestamp") or s.get("timestamp_millis"))]
    tags, htag = cfg.cluster_tags, cfg.host_tag
    nodes = [Node(s, tags, htag) for s in spans]
    by_id: Dict[str, List[Node]] = defaultdict(list)
    for n in nodes:
        by_id[n.id].append(n)
    t0 = min(n.start for n in nodes)
    t1 = max(n.end for n in nodes)
    root = Node(
        {
            "id": "root",
            "name": "trace",
            "localEndpoint": {"serviceName": "all"},
            "timestamp": t0,
            "duration": t1 - t0,
            "kind": "LOCAL",
        },
        tags,
    )
    missing: Dict[str, Node] = {}

    def best(cands: List[Node], n: Node, prefer: str) -> Node:
        """Among candidate parents (some clients reuse one span id for several requests), prefer the
        wanted kind, then one that fully contains the child, then the closest start time (robust to
        client clock skew, unlike overlap)."""

        def score(c: Node) -> Tuple[int, int, int]:
            fits = 1 if c.start <= n.start and c.end >= n.end else 0
            return (1 if c.kind == prefer else 0, fits, -abs(c.start - n.start))

        return max(cands, key=score)

    def pick_parent(n: Node) -> Optional[Node]:
        twins = [c for c in by_id[n.id] if c is not n and c.kind == "CLIENT"]
        if n.kind == "SERVER" and twins:  # shared span: callee under caller
            return best(twins, n, "CLIENT")
        if not n.parent_id:
            return root
        cands = [c for c in by_id.get(n.parent_id, []) if c is not n]
        if not cands:
            m = missing.get(n.parent_id)
            if m is None:
                m = Node(
                    {
                        "id": f"missing-{n.parent_id}",
                        "name": f"[missing parent {n.parent_id}]",
                        "localEndpoint": {"serviceName": "[missing]"},
                        "timestamp": n.start,
                        "duration": 0,
                        "kind": "LOCAL",
                    },
                    tags,
                )
                missing[n.parent_id] = m
                root.children.append(m)
            return m
        return best(cands, n, "SERVER")

    for n in nodes:
        p = pick_parent(n)
        if p is not None and p is not n:
            p.children.append(n)
            # The server may name its span after an internal op while the caller names the RPC after
            # the RPC method (server span "rank" vs caller span "getRankedFeed"); keep the
            # caller's name as a second key for the method-name join.
            if n.kind == "SERVER" and p.kind == "CLIENT" and p.id == n.id and p.op_key and p.op_key != n.op_key:
                n.op_alt = p.op_key
    for m in missing.values():
        if m.children:
            m.start = min(c.start for c in m.children)
            m.dur = max(c.end for c in m.children) - m.start

    # Clock-skew correction, same rule as Zipkin's UI: a SERVER span that does not fit inside its
    # CLIENT parent (mobile/browser clocks are not NTP-tight) is centred in it and its whole subtree
    # shifted by the same amount.
    def shift(n: Node, d: int) -> None:
        n.start -= d
        n.skew_us += d
        for c in n.children:
            shift(c, d)

    def correct_skew(n: Node) -> None:
        for c in n.children:
            if (
                c.kind == "SERVER"
                and n.kind == "CLIENT"
                and c.service != n.service
                and n.dur > 0
                and (c.start < n.start or c.end > n.end)
            ):
                latency = max((n.dur - c.dur) // 2, 0)
                shift(c, c.start - latency - n.start)
            correct_skew(c)

    correct_skew(root)
    t0 = min(n.start for n in nodes)
    root.start, root.dur = t0, max(n.end for n in nodes) - t0

    if clip:
        # A child that does not overlap its parent at all (client tracers parent later requests to
        # long-finished spans) would be clipped to nothing; hang it off the nearest ancestor that
        # does overlap it instead, so it keeps its time but outside the parent's budget.
        def reparent(n: Node, chain: List[Node]) -> None:
            keep: List[Node] = []
            moved: List[Tuple[Node, List[Node]]] = []
            for c in n.children:
                if n is not root and (c.end <= n.start or c.start >= n.end):
                    i = next(
                        (k for k in range(len(chain) - 1, -1, -1) if c.start < chain[k].end and c.end > chain[k].start),
                        0,
                    )
                    chain[i].children.append(c)
                    c.tags = dict(c.tags, _moved_from=n.label())
                    moved.append((c, chain[: i + 1]))
                else:
                    keep.append(c)
            n.children = keep
            for c in keep:
                reparent(c, chain + [n])
            for c, ch in moved:
                reparent(c, ch)

        reparent(root, [])

    if group_services:

        def group(n: Node) -> None:
            buckets: Dict[Tuple[str, str], List[Node]] = defaultdict(list)
            for c in n.children:
                if not c.children and c.service in group_services and c.members is None:
                    buckets[(c.service, c.name)].append(c)
            for (svc, name), leaves in buckets.items():
                if len(leaves) < 3:
                    continue
                g = Node(
                    {
                        "id": f"group-{id(n)}-{name}",
                        "name": f"{name} ×{len(leaves)}",
                        "localEndpoint": {"serviceName": svc},
                        "kind": "CLIENT",
                        "timestamp": min(c.start for c in leaves),
                        "duration": max(c.end for c in leaves) - min(c.start for c in leaves),
                    },
                    tags,
                )
                g.members = [(c.start, c.end) for c in leaves]
                n.children = [c for c in n.children if c not in leaves] + [g]
            for c in n.children:
                group(c)

        group(root)

    def finish(n: Node, lo: int, hi: int) -> None:
        s, e = max(n.start, lo), min(n.end, hi)
        n.eff_start, n.eff_us = s, max(e - s, 0)
        if n.members is not None:  # grouped leaves: value = time at least one member was in flight
            n.self_us = union_len([(max(a, s), min(b, e)) for a, b in n.members])
        else:
            kids = [(max(c.start, s), min(c.end, e)) for c in n.children]
            n.self_us = max(n.eff_us - union_len(kids), 0)
        for c in n.children:
            finish(c, s, e) if clip else finish(c, -(1 << 62), 1 << 62)

    finish(root, -(1 << 62), 1 << 62)
    return root, nodes


# --------------------------------------------------------------------------- profile source (Studio API)


class StudioAPI:
    """Reads profiles through the Studio backend: /api/services and /api/flamegraph."""

    def __init__(self, base_url: str, timeout_s: int = 120):
        if not base_url:
            raise ValueError("profile_source.url (or its url_env variable) is not set")
        self.base, self.timeout = base_url, timeout_s

    def _get(self, path: str, params: Optional[Dict[str, str]] = None) -> Optional[str]:
        url = f"{self.base}{path}" + (f"?{urlencode(params)}" if params else "")
        with urllib.request.urlopen(
            urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=self.timeout
        ) as r:
            if r.status == 204:
                return None
            return r.read().decode()

    def services_with_data(self) -> List[str]:
        body = self._get("/api/services")
        return [s["name"] for s in json.loads(body) if s.get("hasData", True)] if body else []

    def flamegraph(self, service: str, s: dt.datetime, e: dt.datetime) -> Optional[Dict[str, Any]]:
        body = self._get("/api/flamegraph", {"serviceName": service, "startTime": iso(s), "endTime": iso(e)})
        return json.loads(body) if body and body.strip() else None


def display_frame(cls: str, meth: str) -> str:
    """JVM 'pkg/Class' + 'method' -> 'pkg.Class.method'; file-path modules (py-spy) -> 'func (path)'."""
    if cls.startswith("/"):
        return f"{meth} ({cls})"
    return f"{cls.replace('/', '.')}.{meth}"


def profile_summary(
    tree: Dict[str, Any], cfg: Config, want_classes: Set[str], want_ops: Optional[Set[str]] = None, top: int = 12
) -> Dict[str, Any]:
    """Hot app methods for the cluster + symbol join for the spans of this trace: by class (spans
    named by a fully-qualified class) and by method name (spans named by a Thrift op / function)."""
    total = tree.get("value", 0) or 1
    incl: Counter = Counter()
    selfc: Counter = Counter()
    app_total = 0
    classes: Dict[str, Dict[str, Any]] = {
        c: {"incl": 0, "methods": Counter(), "leaves": Counter()} for c in want_classes
    }
    ops: Dict[str, Dict[str, Any]] = {
        o: {"incl": 0, "frames": Counter(), "leaves": Counter()} for o in (want_ops or ())
    }

    def walk(n: Dict[str, Any], active: frozenset, in_app: bool, anc_v: float, active_cls: frozenset) -> None:
        nonlocal app_total
        name = n.get("name", "")
        v = n.get("value", 0)
        kids = n.get("children") or []
        selfv = v - sum(k.get("value", 0) for k in kids)
        m = cfg.match_app(name)
        nxt, nxt_anc, nxt_cls = active, anc_v, active_cls
        if m:
            cls, meth = m
            if not in_app:
                app_total += v
                in_app = True
            # --- symbol join: this frame's class vs the span classes seen in the trace
            dotted = cls.replace("/", ".").lower()
            for key in {dotted, dotted.split("$")[0]}:  # set: no double count when there is no '$'
                if key in classes:
                    if key not in nxt_cls:
                        classes[key]["incl"] += v
                        nxt_cls = nxt_cls | {key}
                    if key == dotted:
                        classes[key]["methods"][meth] += v
            # --- symbol join: this frame's method/Thrift class vs spans named by a method
            if ops:
                for key in op_frame_keys(cls, meth):
                    if key in ops:
                        okey = "op:" + key
                        if okey not in nxt_cls:
                            ops[key]["incl"] += v
                            nxt_cls = nxt_cls | {okey}
                        ops[key]["frames"][display_frame(cls, meth)] += v
            # --- generic hot list (framework + lambdas hidden, wrapper chains collapsed)
            if not cfg.framework.match(cls) and not SYNTHETIC.search(cls) and not SYNTHETIC.search(meth):
                key2 = display_frame(cls, meth)
                if key2 not in active and v < 0.97 * anc_v:
                    incl[key2] += v
                    nxt = active | {key2}
                    nxt_anc = v
                selfc[key2] += selfv
        if selfv > 0:
            leaf = display_frame(*m) if m else name.split("(")[0].replace("/", ".")
            for key in active_cls:
                (ops[key[3:]] if key.startswith("op:") else classes[key])["leaves"][leaf] += selfv
        for c in kids:
            walk(c, nxt, in_app, nxt_anc, nxt_cls)

    walk(tree, frozenset(), False, total * 10, frozenset())
    fmt = lambda k, v: {"frame": k, "samples": v, "pct": round(100 * v / total, 2)}  # noqa: E731
    pct = lambda v: round(100 * v / total, 2)  # noqa: E731
    out_classes = {}
    for c, d in classes.items():
        if d["incl"] <= 0:
            continue
        out_classes[c] = {
            "pct": pct(d["incl"]),
            "samples": d["incl"],
            "methods": [{"name": k, "pct": pct(v)} for k, v in d["methods"].most_common(6)],
            "leaves": [{"frame": k, "pct": pct(v)} for k, v in d["leaves"].most_common(6)],
        }
    out_ops = {}
    for o, d in ops.items():
        if d["incl"] <= 0:
            continue
        out_ops[o] = {
            "pct": pct(d["incl"]),
            "samples": d["incl"],
            "frames": [{"frame": k, "pct": pct(v)} for k, v in d["frames"].most_common(8)],
            "leaves": [{"frame": k, "pct": pct(v)} for k, v in d["leaves"].most_common(6)],
        }
    return {
        "total_samples": total,
        "app_pct": round(100 * app_total / total, 1),
        "top": [fmt(k, v) for k, v in incl.most_common(top)],
        "top_self": [fmt(k, v) for k, v in selfc.most_common(top) if v > 0],
        "classes": out_classes,
        "ops": out_ops,
    }


def fetch_profile(
    src: StudioAPI, cluster: str, s: dt.datetime, e: dt.datetime
) -> Tuple[str, Optional[Dict[str, Any]], Optional[str]]:
    try:
        tree = src.flamegraph(cluster, s, e)
    except urllib.error.HTTPError as ex:
        if ex.code == 406:  # unknown service
            return "no_data", None, "service unknown to the profile source"
        raise
    if not tree or (tree.get("value") or 0) < 1000:
        return "no_data", None, f"only {(tree or {}).get('value', 0)} samples in window"
    return "ok", tree, None


def prune_tree(tree: Dict[str, Any], min_frac: float) -> Dict[str, Any]:
    """Compact copy of the profile for embedding: drop frames under min_frac of total samples
    (their samples stay in the parent's inclusive value, i.e. show as parent self time) and strip
    JVM method signatures. ~0.03% keeps a few thousand frames / a few hundred KB."""
    thr = min_frac * (tree.get("value") or 1)

    def short(name: str) -> str:
        i = name.find("(")
        return name[:i] if i > 0 and re.search(r"[./]", name[:i]) else name

    def rec(n: Dict[str, Any]) -> Dict[str, Any]:
        kids = [rec(c) for c in (n.get("children") or []) if (c.get("value") or 0) >= thr]
        kids.sort(key=lambda k: -k["value"])
        return {"name": short(n.get("name", "")), "value": n.get("value", 0), "children": kids}

    return rec(tree)


# --------------------------------------------------------------------------- windows, links, ranking


def iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def us_to_dt(us: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(us / 1e6, tz=dt.timezone.utc)


def script_json(v: Any) -> str:
    """JSON safe to inline in <script>: span and frame names are untrusted and may contain '</script>'."""
    s = json.dumps(v, separators=(",", ":"))
    return (
        s.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def profile_window(start_us: int, end_us: int, min_minutes: int) -> Tuple[dt.datetime, dt.datetime]:
    s = us_to_dt(start_us).replace(second=0, microsecond=0) - dt.timedelta(minutes=1)
    e = us_to_dt(end_us).replace(second=0, microsecond=0) + dt.timedelta(minutes=2)
    if (e - s) < dt.timedelta(minutes=min_minutes):
        pad = (dt.timedelta(minutes=min_minutes) - (e - s)) / 2
        s, e = s - pad, e + pad
    return s, e


def deep_link(cfg: Config, cluster: str, s: dt.datetime, e: dt.datetime, search: Optional[str] = None) -> str:
    # Table view when a symbol is searched (ranked frames are easier to relate to a span than a
    # highlighted flame graph); flame graph for the cluster-level link. Same parameter set Studio's
    # own ProfilingStatusPage uses: gtab=graph tab, rtms=mixed runtime stacks, pm=process inclusion,
    # wp=weight percentile.
    q = {
        "service": cluster,
        "startTime": iso(s),
        "endTime": iso(e),
        "view": "table" if search else "flamegraph",
        "gtab": "1",
        "rtms": "1",
        "pm": "1",
        "wp": "100",
    }
    if search:
        q["search"] = search
    return f"{cfg.studio_url}/profiles?{urlencode(q)}"


def proxy_cluster(cfg: Config, cluster: str, profiled: List[str]) -> Optional[str]:
    if cluster in profiled:
        return cluster
    if cluster in cfg.proxy_overrides and cfg.proxy_overrides[cluster] in profiled:
        return cfg.proxy_overrides[cluster]
    head = cluster.split("-")[0]
    cands = [p for p in profiled if p.split("-")[0] == head]
    if not cands:
        return None

    def common(a: str, b: str) -> int:
        n = 0
        for x, y in zip(a, b):
            if x != y:
                break
            n += 1
        return n

    return max(cands, key=lambda p: (common(p, cluster), -len(p)))


def rank_clusters(nodes: List[Node], parents: Dict[int, Node], min_minutes: int) -> List[Dict[str, Any]]:
    """Per cluster: self_ms = sum of span self times (additive 'work', over-counts services that
    emit concurrent spans, e.g. one per greenlet); excl_ms = wall time the request spent inside the
    cluster's spans and not inside any downstream cluster's spans (union-based, concurrency-safe).
    Ranking uses excl_ms."""
    agg: Dict[str, Dict[str, Any]] = {}
    own: Dict[str, List[Tuple[int, int]]] = defaultdict(list)
    down: Dict[str, List[Tuple[int, int]]] = defaultdict(list)
    for n in nodes:
        if not n.cluster:
            continue
        a = agg.setdefault(
            n.cluster,
            {
                "cluster": n.cluster,
                "spans": 0,
                "self_ms": 0.0,
                "wall_ms": 0.0,
                "start": n.start,
                "end": n.end,
                "ops": Counter(),
                "classes": set(),
                "op_keys": set(),
            },
        )
        a["spans"] += 1
        if n.op_key:
            a["op_keys"].add(n.op_key)
        if n.op_alt:
            a["op_keys"].add(n.op_alt)
        a["self_ms"] += n.self_us / 1000
        a["wall_ms"] += n.dur / 1000
        a["start"] = min(a["start"], n.start)
        a["end"] = max(a["end"], n.end)
        a["ops"][n.name] += 1
        iv = (n.eff_start, n.eff_start + n.eff_us)
        own[n.cluster].append(iv)
        up = parents.get(id(n))  # nearest clustered ancestor of a different cluster
        while up is not None and (not up.cluster or up.cluster == n.cluster):
            up = parents.get(id(up))
        if up is not None:
            down[up.cluster].append(iv)
    for cl, a in agg.items():
        mine = merge_intervals(own[cl])
        a["excl_ms"] = (union_len(mine) - intersect_len(mine, merge_intervals(down[cl]))) / 1000
    # Node spans (named by class) and CLIENT spans carry no cluster tag; they belong to the enclosing
    # server span's cluster (and their symbols join against that cluster's profile).
    for n in nodes:
        if n.cluster or not (n.fq_class or n.op_key):
            continue
        cl = owning_cluster(n, parents)
        if cl in agg:
            if n.fq_class:
                agg[cl]["classes"].add(n.fq_class)
            else:
                agg[cl]["op_keys"].add(n.op_key)
    out = sorted(agg.values(), key=lambda a: -a["excl_ms"])
    for a in out:
        a["window"] = profile_window(a["start"], a["end"], min_minutes)
        a["ops"] = [k for k, _ in a["ops"].most_common(4) if not FQ_CLASS.match(k)] or [
            k for k, _ in a["ops"].most_common(2)
        ]
    return out


def owning_cluster(n: Node, parents: Dict[int, Node]) -> Optional[str]:
    """Nearest SERVER ancestor's cluster: node spans inherit it from their Finagle server span."""
    cur: Optional[Node] = n
    while cur is not None:
        if cur.cluster:
            return cur.cluster
        cur = parents.get(id(cur))
    return None


# --------------------------------------------------------------------------- flame graph JSON


def to_flame(n: Node, info: Dict[str, Dict[str, Any]], parents: Dict[int, Node], cfg: Config) -> Dict[str, Any]:
    kids = sorted((to_flame(c, info, parents, cfg) for c in n.children), key=lambda k: -k["total"])
    # value = SELF time, so widths are additive and a frame's width is its subtree's total work.
    d: Dict[str, Any] = {
        "name": n.label(),
        "value": n.self_us,
        "children": kids,
        "total": n.self_us + sum(k["total"] for k in kids),
        "svc": n.service,
        "kind": n.kind,
        "ms": round(n.dur / 1000, 2),
        "self_ms": round(n.self_us / 1000, 2),
        "start": iso(us_to_dt(n.start)) if n.start else "",
    }
    if n.kind == "LOCAL":
        del d["kind"]
    if d["self_ms"] == d["ms"]:
        del d["self_ms"]  # leaf: self == wall (JS falls back)
    if n.eff_us != n.dur:
        d["clipped_ms"] = round(n.eff_us / 1000, 2)
    if n.skew_us:
        d["skew_ms"] = round(n.skew_us / 1000, 1)
    if n.tags.get("_moved_from"):
        d["moved_from"] = n.tags["_moved_from"]
    if n.members is not None:
        d["group_n"] = len(n.members)
    if n.host:
        d["host"] = n.host
    cl = n.cluster or (owning_cluster(n, parents) if (n.fq_class or n.op_key) else None)
    if cl and cl in info:
        # Studio's frame search is a case-insensitive regex over frame names, which are in JVM
        # slash form (com/example/.../ClassName.method). Search for the class path exactly.
        search = None
        if n.fq_class:
            base = n.fq_class.split("$")[0]  # Node$9 -> Node: inner-class spans match the outer class frames
            search = re.escape(base.replace(".", "/")) + r"[.$]"
        d["cluster"] = cl  # profiled/proxy/window/link come from the per-cluster table in JS
        if not n.cluster:
            d["inherited"] = 1
        if n.fq_class:
            d.update({"cls": n.fq_class, "cls_case": n.class_case, "search": search})
        elif n.op_key:
            d.update({"op": n.op_key, "search": op_search_regex(n.op_key, n.op_alt)})
            if n.op_alt:
                d["op_alt"] = n.op_alt
    return d


def strip_totals(d: Dict[str, Any]) -> None:
    """`total` is only needed for sorting/pruning on the Python side."""
    d.pop("total", None)
    for k in d["children"]:
        strip_totals(k)


def prune_flame(d: Dict[str, Any], thr_us: float) -> int:
    """Fold spans whose subtree work is below thr_us into their parent's self value (widths stay
    exact, the JSON shrinks ~10x on traces with thousands of sub-millisecond spans). Returns the
    number of spans folded. Spans with a cluster or a class are always kept."""
    own = deeper = 0
    keep = []
    for k in d["children"]:
        if k["total"] < thr_us and not k.get("cluster") and not k.get("cls"):
            d["value"] += k["total"]
            own += 1 + count_spans(k)
        else:
            deeper += prune_flame(k, thr_us)
            keep.append(k)
    d["children"] = keep
    if own:
        d["folded"] = own
    return own + deeper


def count_spans(d: Dict[str, Any]) -> int:
    return sum(1 + count_spans(k) for k in d["children"])


# --------------------------------------------------------------------------- HTML

HTML_TMPL = r"""<!doctype html>
<html><head><meta charset="utf-8">
<title>Trace %(trace_id)s → gProfiler</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/d3-flame-graph@4.1.3/dist/d3-flamegraph.css"
      integrity="sha384-DgAQSBzzhv8bu6Qc6Lq08THluOr+kO5qLMHt1yv8A3my7Jz2OQv6aq/WSZRYIQkG" crossorigin="anonymous">
<style>
 body{font:13px/1.4 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;margin:0;color:#222}
 header{padding:10px 14px;border-bottom:1px solid #ddd;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
 header b{font-size:14px} input{padding:4px 6px;width:260px} button{padding:4px 10px}
 #wrap{display:grid;grid-template-columns:1fr 440px;align-items:start}
 #left{min-width:0}
 #chart,#pchart{overflow-x:auto;padding:8px}
 #side{border-left:1px solid #ddd;padding:10px 14px;position:sticky;top:0;height:100vh;overflow:auto;box-sizing:border-box}
 #profile{border-top:2px solid #1a73e8;margin-top:6px}
 #phead{padding:8px 14px;background:#f3f7ff;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
 #phead input{width:220px} .mode{padding:3px 8px;border:1px solid #9bb8ea;background:#fff;border-radius:4px;cursor:pointer}
 .mode.on{background:#1a73e8;color:#fff;border-color:#1a73e8}
 td.click{cursor:pointer} td.click:hover{text-decoration:underline;color:#1a73e8}
 table{border-collapse:collapse;width:100%%} td,th{padding:3px 6px;border-bottom:1px solid #eee;text-align:left;font-size:12px}
 th{background:#f6f6f6} .num{text-align:right;font-variant-numeric:tabular-nums}
 .tag{display:inline-block;padding:1px 6px;border-radius:9px;font-size:11px;background:#eee;margin-left:4px}
 .ok{background:#d9f2d9}.proxy{background:#fff1c2}.none{background:#f7d6d6}.join{background:#dbe9ff}
 a.btn{display:inline-block;margin:6px 0;padding:5px 9px;background:#1a73e8;color:#fff;border-radius:4px;text-decoration:none}
 .mono{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11.5px;word-break:break-all}
 h3{margin:10px 0 4px} h4{margin:10px 0 2px} .muted{color:#777}
 .box{border:1px solid #cfe0ff;background:#f3f7ff;border-radius:6px;padding:8px 10px;margin:8px 0}
 #advice{border-bottom:1px solid #ddd;padding:6px 14px;max-height:340px;overflow:auto}
 #advice summary{cursor:pointer;padding:2px 0} #advice p{margin:4px 0}
 .rec{margin:6px 0;padding:6px 9px;border-left:3px solid #1a73e8;background:#f8faff}
 .fid,.ev{cursor:pointer;color:#1a56b8} .ev:hover,.fid:hover{text-decoration:underline}
 textarea.prompt{width:100%%;height:180px;box-sizing:border-box;font:11px/1.35 ui-monospace,Menlo,Consolas,monospace}
</style></head><body>
<header>
 <b>Trace %(trace_id)s</b> <span class="muted">(%(n_spans)d spans, %(trace_ms).0f ms wall, %(t0)s)</span>
 <button onclick="fg.resetZoom()">Reset zoom</button>
 <input id="q" placeholder="search (regex: service, op, class)" oninput="fg.search(this.value)">
 <span class="muted">width = total work in subtree · click a bar for the profile join</span>
</header>
<div id="advice" hidden></div>
<div id="wrap">
 <div id="left">
  <div id="chart"></div>
  <div id="profile" hidden>
   <div id="phead">
    <b>gProfiler CPU profile</b> <span id="ptitle" class="mono"></span>
    <span id="pmodes"></span>
    <input id="pq" placeholder="search frames (regex)" oninput="pg && pg.search(this.value)">
    <button onclick="pg && pg.resetZoom()">Reset zoom</button>
    <a id="plink" class="btn" style="margin:0" target="_blank">Studio ↗</a>
    <span id="pdetails" class="muted" style="flex-basis:100%%"></span>
   </div>
   <div id="pchart"></div>
  </div>
 </div>
 <div id="side">
  <div id="detail"><p class="muted">Click a span.</p></div>
  <h3>Clusters on this trace</h3>
  <div class="muted">excl = wall time inside this cluster's spans and not inside any downstream cluster · self = Σ span self time (over-counts concurrent spans)</div>
  <table><tr><th>cluster</th><th class="num">excl ms</th><th class="num">self ms</th><th class="num">spans</th><th>profile</th></tr>
  %(cluster_rows)s
  </table>
  %(agg_html)s
 </div>
</div>
<script src="https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js"
        integrity="sha384-CjloA8y00+1SDAUkjs099PVfnY2KmDC2BZnws9kh8D/lX1s46w6EPhpXdqMfjK6i" crossorigin="anonymous"></script>
<script src="https://cdn.jsdelivr.net/npm/d3-flame-graph@4.1.3/dist/d3-flamegraph.min.js"
        integrity="sha384-p4NaVVE+k6MT/enE0MtQ8B15rM9BGzHCnx8DizawPGks1ssZUeNdw6bAPpH2gp2w" crossorigin="anonymous"></script>
<script>
const data = %(flame_json)s;
const prof = %(prof_json)s;
const CL = %(clusters_json)s;      // cluster -> {profiled, proxy, window:[start,end]}
const STUDIO = %(studio_json)s;    // base URL of Performance Studio
const APP_PREFIXES = %(app_prefixes_json)s;  // slash-form package prefixes of application frames
const ADVICE = %(advice_json)s;    // {findings, prompt, llm?: {summary, recommendations, model}, llm_error?}
function studioLink(cluster, win, search){
  const q = new URLSearchParams({service: cluster, startTime: win[0], endTime: win[1], view: search ? 'table' : 'flamegraph', gtab: '1', rtms: '1', pm: '1', wp: '100'});
  if (search) q.set('search', search);
  return `${STUDIO}/profiles?${q}`;
}
const palette = d3.scaleOrdinal(d3.schemeTableau10.concat(d3.schemeSet3));
let totalWork = 0; (function sum(n){ totalWork += n.value; (n.children||[]).forEach(sum); })(data);
const fg = flamegraph().width(Math.max(document.getElementById('chart').clientWidth-16, 900)).cellHeight(17)
  .inverted(true).sort(false).selfValue(true).minFrameSize(1).transitionDuration(250)
  .setColorMapper((d, orig) => d.highlight ? '#e6c200' : palette(d.data.svc))
  .label(d => `${d.data.name} — total work ${(d.value/1000).toFixed(1)} ms (${(100*d.value/totalWork).toFixed(2)}%%), wall ${d.data.ms} ms, self ${d.data.self_ms ?? d.data.ms} ms`)
  .setSearchMatch((d, term) => { if (term.startsWith('cluster=')) return d.data.cluster === term.slice(8);
                                 try { return new RegExp(term,'i').test(d.data.name); } catch(e){ return false; } })
  .onClick(d => show(d.data, d.value));
d3.select('#chart').datum(data).call(fg);
window.addEventListener('load', () => { const ci = data.cluster && CL[data.cluster]; if (ci && prof[ci.proxy||data.cluster]) renderProfile(ci.proxy||data.cluster, null, null); });
function esc(s){return String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function escRe(s){return s.replace(/[.*+?^${}()|[\]\\\/]/g,'\\$&');}
// dotted com.example.a.B.method -> regex over slash-form frame names
function frameRx(dotted){
  const py = dotted.match(/^(\S+) \((\/.*)\)$/); if (py) return '^'+escRe(py[1])+' \\('+escRe(py[2]);   // "func (/path/mod.py)" py-spy frames
  const i=dotted.lastIndexOf('.'); return i<0 ? escRe(dotted) : escRe(dotted.slice(0,i).replace(/\./g,'/')+'.'+dotted.slice(i+1)); }
function tbl(rows, k1, k2, h1, rxOf){ let t=`<table><tr><th>${h1}</th><th class="num">%% CPU</th></tr>`;
  for(const r of rows){ const rx = rxOf ? rxOf(r) : null;
    t+=`<tr><td class="mono${rx?' click':''}" ${rx?`title="highlight in the profile below" onclick="psearch(${esc(JSON.stringify(rx))})"`:''}>${esc(r[k1])}</td><td class="num">${r[k2]}</td></tr>`; }
  return t+'</table>'; }

// ---------------------------------------------------------------- embedded gProfiler profile
let pg = null, pState = {cluster:null, sym:null, pattern:null, mode:'full'};
const PKG = d3.scaleOrdinal(d3.schemeTableau10);
function frameColor(name){
  if (APP_PREFIXES.some(p => name.startsWith(p) || name.startsWith(p.replace(/\//g, '.')))) { const h = 10 + (hash(name)%%35); return `hsl(${h},85%%,${62+(hash(name)>>3)%%12}%%)`; }   // app: warm
  if (/^(java|jdk|sun|javax)\//.test(name)) return `hsl(210,55%%,${66+hash(name)%%14}%%)`;                                        // JDK: blue
  if (name.startsWith('[k]')||/^\(\//.test(name)||/^__|^_[a-z]/.test(name)) return `hsl(0,0%%,${70+hash(name)%%14}%%)`;           // kernel/native: gray
  return `hsl(${120+hash(name)%%60},40%%,${66+hash(name)%%14}%%)`;                                                                 // other
}
function hash(s){ let h=0; for (let i=0;i<s.length;i++) h=(h*31+s.charCodeAt(i))>>>0; return h; }
function mergeInto(dst, src){ dst.value += src.value; for (const c of src.children){ let t=dst.children.find(k=>k.name===c.name); if(!t){ t={name:c.name,value:0,children:[]}; dst.children.push(t);} mergeInto(t,c);} }
function sortTree(n){ n.children.sort((a,b)=>b.value-a.value); n.children.forEach(sortTree); return n; }
function outermost(tree, rx){ const out=[]; (function rec(n, path){ if (rx.test(n.name)) { out.push({n, path}); return; } for (const c of n.children) rec(c, path.concat(n)); })(tree, []); return out; }
function calleesTree(tree, rx, label){ const root={name:label,value:0,children:[]}; for (const {n} of outermost(tree,rx)) mergeInto(root,{name:label,value:n.value,children:n.children}); return sortTree(root); }
function callersTree(tree, rx, label){ const root={name:label,value:0,children:[]};
  for (const {n,path} of outermost(tree,rx)){ const chain={name:label,value:n.value,children:[]}; let cur=chain;
    for (let i=path.length-1;i>=0;i--){ const k={name:path[i].name,value:n.value,children:[]}; cur.children.push(k); cur=k; } mergeInto(root, chain); }
  return sortTree(root); }
// sym = the span's symbol (class or method name) the profile is joined on; pattern = frame regex for it
function renderProfile(cluster, sym, pattern){
  const p = prof[cluster]; const el = document.getElementById('profile');
  if (!p || p.outcome!=='ok' || !p.tree){ el.hidden = true; return; }
  if (pState.cluster!==cluster || pState.sym!==sym) pState = {cluster, sym, pattern, mode:'full'};
  el.hidden = false; drawProfile();
}
function setMode(m){ pState.mode = m; drawProfile(); }
function psearch(rx){ if (!pg) return; if (pState.mode!=='full'){ pState.mode='full'; drawProfile(); } document.getElementById('pq').value = rx; pg.search(rx); document.getElementById('profile').scrollIntoView({behavior:'smooth',block:'start'}); }
function drawProfile(){
  const p = prof[pState.cluster], sym = pState.sym, rx = pState.pattern ? new RegExp(pState.pattern,'i') : null;
  const short = sym ? sym.split('.').pop() : null;
  let tree = p.tree, note = '';
  if (rx && pState.mode==='callees') { tree = calleesTree(p.tree, rx, short); note = `callees: everything running under ${short} frames, merged`; }
  else if (rx && pState.mode==='callers') { tree = callersTree(p.tree, rx, short); note = `callers: who runs ${short}, stacks inverted (symbol at top, root at bottom)`; }
  const pct = v => (100*v/p.total_samples).toFixed(2);
  document.getElementById('ptitle').textContent = `${p.cluster} ${p.window[0].slice(11,16)}–${p.window[1].slice(11,16)}Z · ${p.total_samples.toLocaleString()} samples · frames ≥ ${p.min_pct}%% shown${p.note?' · '+p.note:''}`;
  document.getElementById('plink').href = p.link;
  document.getElementById('pmodes').innerHTML = sym ? ['full','callees','callers'].map(m=>`<span class="mode${pState.mode===m?' on':''}" onclick="setMode('${m}')">${m==='full'?'full profile, matches highlighted':m+' of '+esc(short)}</span>`).join(' ') : '';
  document.getElementById('pdetails').textContent = note;
  if (rx && pState.mode!=='full' && tree.value===0) { document.getElementById('pdetails').textContent = `${short}: no frames ≥ ${p.min_pct}%% in the embedded tree (lower --profile-min-pct, or open Studio)`; }
  d3.select('#pchart').html('');
  pg = flamegraph().width(Math.max(document.getElementById('left').clientWidth-16, 900)).cellHeight(16)
    .inverted(true).sort(false).selfValue(false).minFrameSize(1).transitionDuration(200)
    .setColorMapper((d, orig) => d.highlight ? '#e6c200' : frameColor(d.data.name))
    .label(d => `${d.data.name} — ${d.value.toLocaleString()} samples (${pct(d.value)}%% of cluster CPU)`)
    .setSearchMatch((d, term) => { try { return new RegExp(term,'i').test(d.data.name); } catch(e){ return false; } })
    .setDetailsElement(document.getElementById('pdetails'));
  d3.select('#pchart').datum(tree).call(pg);
  if (pState.mode==='full' && pState.pattern) { document.getElementById('pq').value = pState.pattern; pg.search(pState.pattern); }
  else document.getElementById('pq').value = '';
}
function show(d, total){
  const ci = d.cluster ? (CL[d.cluster] || {}) : {};
  d = Object.assign({}, d, {profiled: ci.profiled, proxy: ci.proxy, self_ms: d.self_ms ?? d.ms, kind: d.kind || 'LOCAL',
                            link: ci.window ? studioLink(ci.proxy || d.cluster, ci.window, d.search || null) : null});
  let h = `<h3>${esc(d.name)}</h3><div>kind <b>${esc(d.kind)}</b> · wall <b>${d.ms} ms</b>${d.clipped_ms!==undefined?` <span class="muted">(${d.clipped_ms} ms inside parent)</span>`:''} · self <b>${d.self_ms} ms</b> · subtree work <b>${(total/1000).toFixed(0)} ms</b> (${(100*total/totalWork).toFixed(1)}%% of trace work) · start ${esc(d.start)}</div>`;
  if (d.group_n) h += `<div class="muted">${d.group_n} parallel client spans of the same name folded into one bar; value = time at least one was in flight.</div>`;
  if (d.folded) h += `<div class="muted">${d.folded} tiny child spans folded into this bar's self time (below --span-min-pct of the root's work).</div>`;
  if (d.skew_ms) h += `<div class="muted">clock skew: this span's subtree shifted by ${d.skew_ms} ms to fit inside its caller (Zipkin rule).</div>`;
  if (d.moved_from) h += `<div class="muted">re-parented: the tracer parented it to <span class="mono">${esc(d.moved_from)}</span>, which had already finished; shown under the nearest ancestor that overlaps it.</div>`;
  if (d.host) h += `<div>host <span class="mono">${esc(d.host)}</span></div>`;
  if (!d.cluster){ h += '<p class="muted">No cluster on this span or its ancestors (client-side span); nothing to join to.</p>'; document.getElementById('detail').innerHTML = h; renderProfile(null); return; }
  const tag = ci.no_samples ? `<span class="tag proxy">listed as profiled, but no samples → proxy ${esc(d.proxy)}</span>`
            : d.profiled ? '<span class="tag ok">profiled</span>'
            : (d.proxy ? `<span class="tag proxy">not profiled → proxy ${esc(d.proxy)}</span>` : '<span class="tag none">not profiled anywhere</span>');
  h += `<div style="margin-top:6px">cluster <span class="mono">${esc(d.cluster)}</span>${d.inherited?' <span class="muted">(from enclosing server span)</span>':''} ${tag}</div>`;
  const p = prof[d.proxy||d.cluster];
  if (p && p.outcome==='ok' && p.tree) h += `<div class="muted" style="margin-top:4px">▼ the cluster's CPU flame graph is rendered <a href="#profile" onclick="document.getElementById('profile').scrollIntoView({behavior:'smooth'});return false;">below the trace</a>${d.cls?', with this class highlighted; click any frame in the tables to highlight it':'; click any frame in the tables to highlight it'}.</div>`;
  else if (!p && (d.profiled || d.proxy)) h += `<div class="muted" style="margin-top:4px">profile not embedded for this cluster (outside the top --embed-hotspots by exclusive time); the Studio link below opens it.</div>`;
  if (d.cls){
    const j = p && p.classes && p.classes[d.cls];
    const baseSlash = escRe(d.cls.split('$')[0].replace(/\./g,'/'));
    h += '<div class="box"><b>Symbol join</b> <span class="tag join">span class ↔ profile frames</span>';
    h += `<div class="mono" style="margin:4px 0">${esc(d.cls)}${d.cls_case && d.cls_case.toLowerCase()!==d.cls.split('.').pop() ? ' <span class="muted">(node name: '+esc(d.cls_case)+')</span>' : ''}</div>`;
    if (j){
      h += `<div>inclusive CPU in this cluster's profile: <b>${j.pct}%%</b> (${j.samples.toLocaleString()} samples)`;
      if (p.tree) h += ` · <a href="#profile" onclick="setMode('callees');document.getElementById('profile').scrollIntoView({behavior:'smooth'});return false;">callees</a> · <a href="#profile" onclick="setMode('callers');document.getElementById('profile').scrollIntoView({behavior:'smooth'});return false;">callers</a>`;
      h += '</div>';
      if (j.methods.length) h += '<h4>methods of this class</h4>' + tbl(j.methods,'name','pct','method', r => `^${baseSlash}(\\$[\\w$]*)?\\.${escRe(r.name)}$`);
      if (j.leaves.length)  h += '<h4>hottest leaves under it</h4>' + tbl(j.leaves,'frame','pct','frame', r => frameRx(r.frame));
      h += `<div class="muted" style="margin-top:4px">CPU share is cluster-wide over the profile window (all requests), the span is one request: a high share means this class is a real CPU cost on the cluster; a near-zero share with a long span means the span is mostly waiting (downstream, lock, I/O).</div>`;
    } else if (p && p.outcome==='ok') {
      h += `<div>class <b>not present</b> in the hot part of the profile (below ~0.01%% CPU) → this span's time is <b>off-CPU</b> (waiting), or the node runs on a thread the profiler attributes elsewhere.</div>`;
    } else { h += '<div class="muted">profile not fetched for this cluster (increase --embed-hotspots).</div>'; }
    h += '</div>';
  } else if (d.op) {
    const key = p && p.ops && (p.ops[d.op] ? d.op : (d.op_alt && p.ops[d.op_alt] ? d.op_alt : null));
    const j = key ? p.ops[key] : null;
    h += '<div class="box"><b>Symbol join</b> <span class="tag join">span name ↔ method / Thrift class</span>';
    h += `<div class="mono" style="margin:4px 0">${esc(d.op)}${d.op_alt ? ' <span class="muted">(caller names this RPC</span> '+esc(d.op_alt)+'<span class="muted">)</span>' : ''}</div>`;
    if (j){
      h += `<div>frames named <span class="mono">${esc(key)}</span> (method, send_/recv_, $Processor, _args/_result): inclusive CPU <b>${j.pct}%%</b> (${j.samples.toLocaleString()} samples)`;
      if (p.tree) h += ` · <a href="#profile" onclick="setMode('callees');document.getElementById('profile').scrollIntoView({behavior:'smooth'});return false;">callees</a> · <a href="#profile" onclick="setMode('callers');document.getElementById('profile').scrollIntoView({behavior:'smooth'});return false;">callers</a>`;
      h += '</div>';
      h += '<h4>matching frames</h4>' + tbl(j.frames,'frame','pct','frame', r => frameRx(r.frame));
      if (j.leaves.length) h += '<h4>hottest leaves under them</h4>' + tbl(j.leaves,'frame','pct','frame', r => frameRx(r.frame));
      h += `<div class="muted" style="margin-top:4px">Exact-token match only (a frame called <i>get</i> does not match <i>getFuture</i>). The handler for a Thrift method usually shows up as <i>Service$Processor$${esc(key)}</i> or <i>Handler.${esc(key)}</i>; <i>Client.send_/recv_</i> frames are this cluster calling the same-named method on another service.</div>`;
    } else if (p && p.outcome==='ok') {
      h += `<div>no app frame named <span class="mono">${esc(d.op)}</span>${d.op_alt?' or <span class="mono">'+esc(d.op_alt)+'</span>':''} in the hot part of the profile → the handler's own CPU is below ~0.01%%; this span's time is downstream calls / waiting.</div>`;
    } else { h += '<div class="muted">profile not fetched for this cluster (increase --embed-hotspots).</div>'; }
    h += '</div>';
  }
  if (d.link) h += `<a class="btn" target="_blank" href="${esc(d.link)}">Open in Performance Studio: ${esc(d.proxy||d.cluster)}, this window${d.cls?', table view filtered to this class':''} ↗</a>`;
  if (p && p.outcome==='ok'){
    h += `<div class="muted">cluster-wide CPU profile for the window: ${p.total_samples.toLocaleString()} samples, ${p.app_pct}%% in app code ${p.note?'('+esc(p.note)+')':''}</div>`;
    h += '<h4>hot app methods, inclusive</h4>' + tbl(p.top,'frame','pct','frame', r => frameRx(r.frame));
    h += '<h4>hot app methods, self</h4>' + tbl(p.top_self,'frame','pct','frame', r => frameRx(r.frame));
  } else if (p && p.outcome==='no_data') { h += `<div class="muted">gProfiler has no samples for this cluster in the window (${esc(p.detail||'')}).</div>`;
  } else if (p) { h += `<div class="muted">profile fetch: ${esc(p.outcome)} ${esc(p.detail||'')}</div>`; }
  document.getElementById('detail').innerHTML = h;
  renderProfile(d.proxy||d.cluster, d.cls||d.op||null, d.search||null);
}
// ---------------------------------------------------------------- findings & recommendations
function spanSearch(term){ document.getElementById('q').value = term; fg.search(term); document.getElementById('chart').scrollIntoView({behavior:'smooth',block:'start'}); }
function copyPrompt(){
  const ta = document.getElementById('promptText'), done = m => { document.getElementById('copied').textContent = m; };
  const fallback = () => { ta.closest('details').open = true; ta.focus(); ta.select();
    try { done(document.execCommand('copy') ? 'copied' : 'selected: press Ctrl/Cmd+C'); } catch(e){ done('selected: press Ctrl/Cmd+C'); } };
  if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(ADVICE.prompt).then(() => done('copied'), fallback);
  else fallback();
}
(function renderAdvice(){
  const el = document.getElementById('advice');
  if (!ADVICE || !(ADVICE.findings||[]).length) return;
  const EV = [];
  const ev = (e, cluster) => { EV.push(Object.assign({cluster}, e)); const i = EV.length-1;
    if (e.span) return `<span class="ev mono" data-ev="${i}" title="highlight in the trace">${esc(e.span)}</span>${e.ms!=null?` ${e.ms} ms`:''}`;
    if (e.cluster) return `<span class="ev mono" data-ev="${i}" title="highlight this cluster's spans">${esc(e.cluster)}</span>${e.ms!=null?` ${e.ms} ms`:''}`;
    if (e.frame) return `<span class="ev mono" data-ev="${i}" title="highlight in the profile">${esc(e.frame)}</span>${e.pct!=null?` ${e.pct}%%`:''}`;
    return ''; };
  const L = ADVICE.llm;
  let h = `<details open><summary><b>Recommendations</b> ${L ? `<span class="tag join">LLM · ${esc(L.model)}</span>`
          : `<span class="muted">rule-based findings only${ADVICE.llm_error ? ' (LLM: '+esc(ADVICE.llm_error)+')' : ''}</span>`}
          · <b>${ADVICE.findings.length}</b> findings · <button id="copyPrompt" title="the findings and trace context as an LLM prompt">Copy LLM prompt</button> <span id="copied" class="muted"></span></summary>`;
  if (L){
    if (L.summary) h += `<p>${esc(L.summary)}</p>`;
    for (const r of L.recommendations) h += `<div class="rec"><b>${esc(r.title)}</b>${r.owner?` <span class="tag">${esc(r.owner)}</span>`:''}`
      + `${r.est_savings_ms!=null?` <span class="tag ok">~${r.est_savings_ms} ms</span>`:''}${r.confidence?` <span class="muted">${esc(r.confidence)} confidence</span>`:''}`
      + `<div>${esc(r.why)}</div><div><i>Action:</i> ${esc(r.action)}</div>`
      + `${r.evidence.length?`<div class="muted">evidence: ${r.evidence.map(id => `<span class="fid" data-f="${esc(id)}">${esc(id)}</span>`).join(' ')}</div>`:''}</div>`;
  }
  h += '<table><tr><th>id</th><th>finding</th><th class="num">impact</th><th>evidence</th></tr>';
  for (const f of ADVICE.findings) h += `<tr id="f-${esc(f.id)}"><td>${esc(f.id)}</td><td><b>${esc(f.title)}</b><div class="muted">${esc(f.rule)} · ${esc(f.detail)}</div></td>`
      + `<td class="num">${f.impact_ms!=null?f.impact_ms+' ms':''}</td><td>${f.evidence.map(e => ev(e, f.cluster)).join('<br>')}</td></tr>`;
  h += '</table><details><summary class="muted">LLM prompt (paste into any LLM)</summary><textarea class="prompt" readonly id="promptText"></textarea></details></details>';
  el.innerHTML = h; el.hidden = false;
  document.getElementById('promptText').value = ADVICE.prompt;
  el.addEventListener('click', e => {
    const t = e.target.closest('[data-ev],[data-f],#copyPrompt'); if (!t) return;
    if (t.id === 'copyPrompt'){ e.preventDefault(); copyPrompt(); return; }
    if (t.dataset.f){ const row = document.getElementById('f-'+t.dataset.f); if (row){ row.scrollIntoView({block:'nearest'}); row.style.background='#fff6cc'; setTimeout(() => { row.style.background=''; }, 1500); } return; }
    const x = EV[+t.dataset.ev];
    if (x.span) spanSearch(escRe(x.span));
    else if (x.frame){ const ci = CL[x.cluster]; renderProfile(prof[x.cluster] ? x.cluster : (ci && ci.proxy) || x.cluster, null, null); psearch(frameRx(x.frame)); }
    else if (x.cluster) spanSearch('cluster='+x.cluster);
  });
})();
</script></body></html>
"""


def aggregate_clusters(
    es: ZipkinES, cfg: Config, trace_ids: List[str], date: Optional[str]
) -> Tuple[int, List[Dict[str, Any]]]:
    """Cluster ranking across several traces: mean exclusive ms per trace (absent = 0) and presence."""
    acc: Dict[str, Dict[str, Any]] = {}
    n = 0
    for tid in trace_ids:
        try:
            spans = es.trace(tid, date)
        except SystemExit:
            continue
        root, nodes = build_tree(spans, cfg, clip=True, group_services=cfg.client_services)
        parents: Dict[int, Node] = {}

        def link(x: Node) -> None:
            for c in x.children:
                parents[id(c)] = x
                link(c)

        link(root)
        n += 1
        for a in rank_clusters(nodes, parents, cfg.min_window_minutes):
            r = acc.setdefault(
                a["cluster"],
                {"cluster": a["cluster"], "excl_ms": 0.0, "self_ms": 0.0, "traces": 0, "spans": 0, "ops": Counter()},
            )
            r["excl_ms"] += a["excl_ms"]
            r["self_ms"] += a["self_ms"]
            r["traces"] += 1
            r["spans"] += a["spans"]
            r["ops"].update(a["ops"][:2])
    rows = sorted(acc.values(), key=lambda r: -r["excl_ms"])
    for r in rows:
        r["mean_excl_ms"] = r["excl_ms"] / max(n, 1)
        r["mean_self_ms"] = r["self_ms"] / max(n, 1)
        r["ops"] = [k for k, _ in r["ops"].most_common(3)]
    return n, rows


def agg_html(rows: List[Dict[str, Any]], n: int, label: Optional[str], profiled: List[str], cfg: Config) -> str:
    out = [
        f'<h3>Across {n} joined traces of {html.escape(label or "")}</h3>',
        '<div class="muted">mean exclusive ms per trace (absent counts as 0) · in how many of the traces</div>',
        '<table><tr><th>cluster</th><th class="num">mean excl ms</th><th class="num">traces</th><th>profile</th></tr>',
    ]
    for r in rows[:30]:
        px = proxy_cluster(cfg, r["cluster"], profiled) if profiled else None
        tag = (
            '<span class="tag ok">yes</span>'
            if r["cluster"] in profiled
            else (
                f'<span class="tag proxy">proxy {html.escape(px)}</span>' if px else '<span class="tag none">no</span>'
            )
        )
        out.append(
            f'<tr><td class="mono" title="{html.escape(", ".join(r["ops"]))}">{html.escape(r["cluster"])}</td>'
            f'<td class="num">{r["mean_excl_ms"]:.0f}</td><td class="num">{r["traces"]}/{n}</td><td>{tag}</td></tr>'
        )
    out.append("</table>")
    return "\n".join(out)


def cluster_rows(ranked: List[Dict[str, Any]], info: Dict[str, Dict[str, Any]]) -> str:
    rows = []
    for a in ranked[:25]:
        c = info[a["cluster"]]
        if c.get("no_samples"):
            tag = f'<span class="tag proxy" title="listed as profiled but no samples in this window">no samples → proxy {html.escape(c["proxy"])}</span>'
        elif c["profiled"]:
            tag = '<span class="tag ok">yes</span>'
        elif c["proxy"]:
            tag = f'<span class="tag proxy">proxy {html.escape(c["proxy"])}</span>'
        else:
            tag = '<span class="tag none">no</span>'
        link = f'<a target="_blank" href="{html.escape(c["link"])}">open</a>' if c.get("link") else ""
        rows.append(
            f'<tr><td class="mono">{html.escape(a["cluster"])}</td><td class="num">{a["excl_ms"]:.0f}</td>'
            f'<td class="num">{a["self_ms"]:.0f}</td>'
            f'<td class="num">{a["spans"]}</td><td>{tag} {link}</td></tr>'
        )
    return "\n".join(rows)


# --------------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"))
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--trace-id")
    g.add_argument("--slowest", metavar="TRACE_SERVICE", help="pick the slowest SERVER span's trace")
    g.add_argument(
        "--root",
        metavar="SPAN_NAME",
        help="end-to-end: a client timing root span (config "
        "client.root_prefix is prepended) whose trace joined to backend spans; rooted at that span",
    )
    g.add_argument(
        "--batch",
        action="store_true",
        help="render every entry of config 'reports' into --out-dir " "and write manifest.json there",
    )
    ap.add_argument("--platform", help="with --root: client service (default: first of config client.services)")
    ap.add_argument(
        "--tag",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="with --root: only roots carrying this span tag (repeatable), e.g. a device class",
    )
    ap.add_argument("--device", help="with --root: device type label recorded in the report metadata")
    ap.add_argument(
        "--sample",
        default="tail",
        type=check_sample,
        help="with --root: 'tail' = among the slowest roots (default), or pNN (e.g. p50, p90) = "
        f"roots within ±{SAMPLE_BAND * 100:.0f}%% of that duration percentile",
    )
    ap.add_argument("--no-llm", action="store_true", help="rule-based findings only; skip the advisor LLM call")
    ap.add_argument("--hours", type=float, default=6, help="with --root: look back this many hours")
    ap.add_argument(
        "--min-age-min",
        type=float,
        default=20,
        help="with --root: skip traces younger than this (profile indexing lag)",
    )
    ap.add_argument(
        "--pick",
        choices=["median", "richest", "slowest"],
        default="median",
        help="with --root: the joined trace with the median number of backend spans (default; "
        "extremes are usually bots or shared trace ids), the most, or the slowest client timing",
    )
    ap.add_argument(
        "--agg",
        type=int,
        default=0,
        metavar="N",
        help="with --root: also rank clusters across the N richest joined traces",
    )
    ap.add_argument("--cluster", help="with --slowest: restrict to this cluster tag value")
    ap.add_argument("--op", help="with --slowest: restrict to this span name")
    ap.add_argument(
        "--max-ms", type=int, default=5000, help="with --slowest/--root: ignore spans above this (timeouts)"
    )
    ap.add_argument(
        "--clip", action="store_true", help="confine spans to their parent's interval (default with --root)"
    )
    ap.add_argument("--date", help="UTC day YYYY-MM-DD (default today)")
    ap.add_argument(
        "--embed-hotspots",
        type=int,
        default=6,
        metavar="K",
        help="embed profiles for the top K clusters by exclusive time (clusters with node-span "
        "classes are always included; 0 = links only)",
    )
    ap.add_argument(
        "--span-min-pct",
        type=float,
        default=0.02,
        metavar="PCT",
        help="fold spans whose subtree work is below this %% of the root into their parent (0 = keep all)",
    )
    ap.add_argument(
        "--profile-min-pct",
        type=float,
        default=0.03,
        metavar="PCT",
        help="embedded profile flame graph drops frames below this %% of samples (size/detail trade-off)",
    )
    ap.add_argument("--no-profiles", action="store_true", help="links only; do not contact the profile source")
    ap.add_argument(
        "--focus", metavar="REGEX", help="re-root the flame graph at the longest span matching 'service: op'"
    )
    ap.add_argument("--out", help="output HTML (single-trace modes)")
    ap.add_argument("--out-dir", help="with --batch: output directory")
    ap.add_argument("--quiet", action="store_true", help="no text summary on stdout")
    return ap


def render(
    cfg: Config,
    es: ZipkinES,
    src: Optional[StudioAPI],
    profiled: List[str],
    args: argparse.Namespace,
    llm: Optional[trace_advisor.LLM] = None,
) -> Dict[str, Any]:
    """Render one trace page to args.out; returns its manifest entry."""
    trace_id = args.trace_id
    if args.slowest:
        cands = es.slowest(args.slowest, args.cluster, args.op, args.date, args.max_ms, 5)
        if not cands:
            raise LookupError("no matching server spans")
        for c in cands:
            print(
                f"  candidate {c['traceId']} {c['name']} {c['duration']/1000:.0f} ms "
                f"{(c.get('tags') or {}).get(cfg.cluster_tag)}",
                file=sys.stderr,
            )
        trace_id = cands[0]["traceId"]

    focus = args.focus
    clip = args.clip
    agg_rows: List[Dict[str, Any]] = []
    agg_n = 0
    root_label = None
    path_meta: Dict[str, Any] = {}
    if args.root:
        platform = args.platform or (cfg.client_services[0] if cfg.client_services else None)
        if not platform:
            raise LookupError("--root needs --platform or client.services in config")
        tags = parse_tags(args.tag)
        pct = sample_percentile(args.sample)
        max_ms = args.max_ms if args.max_ms != 5000 else 15000  # client timings run longer than server spans
        min_ms = 0
        stats = None
        try:
            stats = es.root_stats(args.root, platform, args.date, args.hours, tags, pct)
        except Exception as ex:  # noqa: BLE001 -- volume is for sorting only; tail sampling does not need it
            if pct:
                raise
            print(f"  warning: root stats unavailable ({str(ex)[:200]})", file=sys.stderr)
        if pct:
            # Typical traces: roots within ±SAMPLE_BAND of the percentile, not the slowest ones.
            target = stats[f"p{pct}_ms"]
            min_ms, max_ms = int(target * (1 - SAMPLE_BAND)), min(max_ms, int(target * (1 + SAMPLE_BAND)) + 1)
        # Few roots join to backend spans (only sampled requests carry the trace id), so typical
        # sampling draws a larger random set from the band; the seed changes every hour.
        cands = es.root_candidates(
            args.root,
            platform,
            args.date,
            args.hours,
            max_ms,
            1000 if pct else 200,
            args.min_age_min,
            tags,
            min_ms,
            int(dt.datetime.now(dt.timezone.utc).timestamp() // 3600) if pct else None,
        )
        if not cands:
            raise LookupError(f"no {platform} {args.root} trace joined to backend spans in the last {args.hours}h")
        path_meta = {"platform": platform, "flow": cands[0]["name"], "sample": args.sample}
        if args.device:
            path_meta["device"] = args.device
        if stats:
            path_meta["stats"] = stats
        print(
            f"  {len(cands)}/{cands[0]['total_roots']} {platform} {cands[0]['name']} roots (last {args.hours:g}h, "
            f"{min_ms}..{max_ms} ms) joined to backend spans",
            file=sys.stderr,
        )
        by_rich = sorted(cands, key=lambda c: -c["backend_spans"])
        if args.pick == "richest":
            order = by_rich
        elif args.pick == "slowest":
            order = cands
        else:  # median by backend spans, then outward from it
            mid = len(by_rich) // 2
            order = sorted(by_rich, key=lambda c: abs(by_rich.index(c) - mid))
        for c in order[:5]:
            print(
                f"  candidate {c['traceId']} client {c['duration']/1000:.0f} ms, {c['backend_spans']} backend spans",
                file=sys.stderr,
            )
        trace_id = order[0]["traceId"]
        root_label = f"{platform}: {order[0]['name']}"
        focus = focus or re.escape(root_label)
        clip = True
        if args.agg:
            agg_n, agg_rows = aggregate_clusters(es, cfg, [c["traceId"] for c in order[: args.agg]], args.date)

    spans = es.trace(trace_id, args.date)
    root, nodes = build_tree(spans, cfg, clip=clip, group_services=cfg.client_services if args.root else None)
    parents: Dict[int, Node] = {}

    def link_parents(n: Node) -> None:
        for c in n.children:
            parents[id(c)] = n
            link_parents(c)

    link_parents(root)
    ranked = rank_clusters(nodes, parents, cfg.min_window_minutes)

    info: Dict[str, Dict[str, Any]] = {}
    for a in ranked:
        px = proxy_cluster(cfg, a["cluster"], profiled) if profiled else None
        info[a["cluster"]] = {
            "profiled": a["cluster"] in profiled,
            "proxy": px if px != a["cluster"] else None,
            "link": deep_link(cfg, px or a["cluster"], *a["window"]),
            "window": a["window"],
            "classes": a["classes"],
            "op_keys": a["op_keys"],
        }

    prof: Dict[str, Any] = {}
    if src and args.embed_hotspots:

        def fetch_into(target: str, s: dt.datetime, e: dt.datetime, classes: Set[str], op_keys: Set[str]) -> bool:
            """Fetch target's profile for the window into prof[target]; True if it has samples."""
            if target in prof:
                return prof[target].get("outcome") == "ok"
            print(
                f"  profile: {target} {iso(s)}..{iso(e)} (join {len(classes)} span classes, {len(op_keys)} op names)",
                file=sys.stderr,
            )
            try:
                outcome, tree, note = fetch_profile(src, target, s, e)
                if tree is not None:
                    prof[target] = {
                        "outcome": "ok",
                        "note": note,
                        "cluster": target,
                        "window": [iso(s), iso(e)],
                        "link": deep_link(cfg, target, s, e),
                        "min_pct": args.profile_min_pct,
                        "tree": prune_tree(tree, args.profile_min_pct / 100),
                        **profile_summary(tree, cfg, classes, op_keys),
                    }
                    return True
                prof[target] = {"outcome": outcome, "detail": note}
            except Exception as ex:  # noqa: BLE001
                prof[target] = {"outcome": "error", "detail": str(ex)[:200]}
            return False

        # Clusters with class-named spans are what the symbol join is for: always try them.
        order = sorted(ranked, key=lambda a: (0 if a["classes"] else 1, ranked.index(a)))
        done = 0
        for a in order:
            if done >= args.embed_hotspots and not a["classes"]:
                break
            c = info[a["cluster"]]
            s, e = c["window"]
            # Being in the profiled list only means an agent is registered; a cluster can have no
            # samples in the window (agent registered but not shipping). Fall through to a proxy.
            cands = ([a["cluster"]] if c["profiled"] else []) + ([c["proxy"]] if c["proxy"] else [])
            if c["profiled"] and not c["proxy"]:
                alt = proxy_cluster(cfg, a["cluster"], [p for p in profiled if p != a["cluster"]])
                if alt:
                    cands.append(alt)
            for t in cands:
                if fetch_into(t, s, e, info_classes(info, t) | c["classes"], info_ops(info, t) | c["op_keys"]):
                    if t != a["cluster"]:
                        if c["profiled"]:
                            c["no_samples"] = True  # listed as profiled, but empty in this window
                        c["proxy"] = t
                        c["link"] = deep_link(cfg, t, s, e)
                    done += 1
                    break

    flame_root = root
    if focus:
        rx = re.compile(focus, re.I)
        hits = [n for n in nodes if rx.search(n.label())]
        if not hits:
            raise LookupError(f"--focus {focus!r} matched no span")
        flame_root = max(hits, key=lambda n: n.dur)

    flame = to_flame(flame_root, info, parents, cfg)
    if args.span_min_pct > 0:
        folded = prune_flame(flame, flame["total"] * args.span_min_pct / 100)
        if folded:
            print(
                f"  folded {folded} spans under {args.span_min_pct}% of the root's work into their parents",
                file=sys.stderr,
            )
    strip_totals(flame)
    clusters_js = {
        cl: {
            "profiled": c["profiled"],
            "proxy": c["proxy"],
            "no_samples": bool(c.get("no_samples")),
            "window": [iso(c["window"][0]), iso(c["window"][1])],
        }
        for cl, c in info.items()
    }
    try:
        advice = trace_advisor.advise(
            cfg.advisor,
            llm,
            flame_root,
            parents,
            ranked,
            info,
            prof,
            agg_rows,
            agg_n,
            cfg.client_services,
            path_meta,
            {"root": flame_root.label(), "wall_ms": round(flame_root.dur / 1000), "spans": len(nodes)},
        )
    except Exception as ex:  # noqa: BLE001 -- the trace/profile join is the report; advice is extra
        print(f"  warning: findings failed ({type(ex).__name__}: {str(ex)[:200]})", file=sys.stderr)
        advice = {"findings": [], "prompt": "", "error": f"{type(ex).__name__}: {str(ex)[:200]}"}
    page = HTML_TMPL % {
        "trace_id": html.escape(trace_id) + (f" · {html.escape(flame_root.label())}" if focus else ""),
        "n_spans": len(nodes),
        "trace_ms": root.dur / 1000,
        "t0": iso(us_to_dt(root.start)),
        "cluster_rows": cluster_rows(ranked, info),
        "flame_json": script_json(flame),
        "prof_json": script_json(prof),
        "clusters_json": script_json(clusters_js),
        "studio_json": script_json(cfg.studio_url),
        "app_prefixes_json": script_json(cfg.app_prefixes),
        "advice_json": script_json(advice),
        "agg_html": agg_html(agg_rows, agg_n, root_label, profiled, cfg) if agg_rows else "",
    }
    with open(args.out, "w") as f:
        f.write(page)
    meta = {
        "trace_id": trace_id,
        "root": flame_root.label(),
        "n_spans": len(nodes),
        "wall_ms": round(root.dur / 1000),
        "trace_start": iso(us_to_dt(root.start)),
        "trace_end": iso(us_to_dt(root.end)),
        "clusters": [
            {
                "cluster": a["cluster"],
                "excl_ms": round(a["excl_ms"]),
                "profile": info[a["cluster"]]["proxy"] or (a["cluster"] if info[a["cluster"]]["profiled"] else None),
            }
            for a in ranked[:10]
        ],
        "embedded_profiles": sorted(k for k, v in prof.items() if v.get("outcome") == "ok"),
        "advice": trace_advisor.summary(advice) if advice["findings"] else None,
        **path_meta,
    }
    if args.quiet:
        return meta

    # ---- text summary
    print(f"\ntrace {trace_id}: {len(nodes)} spans, {root.dur/1000:.0f} ms wall, starts {iso(us_to_dt(root.start))}")
    print(f"{'cluster':<48}{'excl ms':>9}{'self ms':>9}{'spans':>6}  profile")
    for a in ranked[:15]:
        c = info[a["cluster"]]
        st = (
            f"profiled but NO SAMPLES -> proxy={c['proxy']}"
            if c.get("no_samples")
            else "profiled" if c["profiled"] else (f"proxy={c['proxy']}" if c["proxy"] else "NOT PROFILED")
        )
        print(
            f"{a['cluster']:<48}{a['excl_ms']:>9.0f}{a['self_ms']:>9.0f}{a['spans']:>6}  {st}  ops={','.join(a['ops'])}"
        )
    if agg_rows:
        print(f"\nacross {agg_n} joined {root_label} traces (mean exclusive ms per trace):")
        print(f"{'cluster':<48}{'mean excl':>10}{'traces':>8}  profile")
        for r in agg_rows[:15]:
            px = proxy_cluster(cfg, r["cluster"], profiled) if profiled else None
            st = "profiled" if r["cluster"] in profiled else (f"proxy={px}" if px else "NOT PROFILED")
            print(
                f"{r['cluster']:<48}{r['mean_excl_ms']:>10.0f}{r['traces']:>5}/{agg_n:<2}  {st}  ops={','.join(r['ops'])}"
            )
    for k, v in prof.items():
        if v.get("outcome") != "ok":
            print(f"\n{k}: profile {v.get('outcome')} {v.get('detail') or ''}")
            continue
        print(f"\n{k}: {v['total_samples']:,} samples, {v['app_pct']}% app code")
        for t in v["top"][:6]:
            print(f"   {t['pct']:6.2f}%  {t['frame']}")
        if v["classes"]:
            print("   symbol join (span class -> CPU share in profile):")
            # pair with span wall time
            wall = defaultdict(list)
            for n in nodes:
                if n.fq_class:
                    wall[n.fq_class].append(n.dur / 1000)
            rows = sorted(v["classes"].items(), key=lambda kv: -kv[1]["pct"])
            for cls, j in rows[:10]:
                w = wall.get(cls, [])
                med = sorted(w)[len(w) // 2] if w else 0
                print(f"   {j['pct']:6.2f}%  {cls.rsplit('.', 1)[-1]:<58} spans={len(w):<3} wall p50={med:.0f}ms")
            missing = [c for c in info_classes(info, k) if c not in v["classes"]]
            if missing:
                print(
                    f"   {len(missing)} span classes absent from the hot profile (off-CPU waits), e.g. "
                    + ", ".join(m.rsplit(".", 1)[-1] for m in missing[:4])
                )
        want_ops = info_ops(info, k)
        if want_ops:
            print("   symbol join (span method name -> CPU share of same-named frames):")
            wall = defaultdict(list)
            for n in nodes:
                for k in (n.op_key, n.op_alt):
                    if k:
                        wall[k].append(n.dur / 1000)
            for op, j in sorted(v["ops"].items(), key=lambda kv: -kv[1]["pct"])[:10]:
                w = wall.get(op, [])
                med = sorted(w)[len(w) // 2] if w else 0
                top_frame = j["frames"][0]["frame"] if j["frames"] else ""
                print(f"   {j['pct']:6.2f}%  {op:<28} spans={len(w):<3} wall p50={med:.0f}ms  {top_frame}")
            missing_ops = sorted(o for o in want_ops if o not in v["ops"])
            if missing_ops:
                print(
                    f"   {len(missing_ops)} span method names absent from the hot profile: "
                    + ", ".join(missing_ops[:6])
                )
    print(f"\nwrote {args.out}")
    return meta


def info_classes(info: Dict[str, Dict[str, Any]], target: str) -> Set[str]:
    out: Set[str] = set()
    for cl, c in info.items():
        if cl == target or c.get("proxy") == target:
            out |= c["classes"]
    return out


def info_ops(info: Dict[str, Dict[str, Any]], target: str) -> Set[str]:
    out: Set[str] = set()
    for cl, c in info.items():
        if cl == target or c.get("proxy") == target:
            out |= c["op_keys"]
    return out


REPORT_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
REPORT_OPTIONS = {
    "trace_id",
    "slowest",
    "root",
    "platform",
    "device",
    "sample",
    "hours",
    "min_age_min",
    "pick",
    "agg",
    "cluster",
    "op",
    "max_ms",
    "clip",
    "embed_hotspots",
    "span_min_pct",
    "profile_min_pct",
    "focus",
}


SAMPLE_BAND = 0.15
SAMPLE = re.compile(r"^(tail|p([1-9]\d?))$")


def check_sample(s: str) -> str:
    if not SAMPLE.match(str(s)):
        raise ValueError(f"sample {s!r} must be 'tail' or pNN (p1..p99)")
    return s


def sample_percentile(s: str) -> Optional[int]:
    m = SAMPLE.match(s)
    return int(m.group(2)) if m and m.group(2) else None


def parse_tags(items: List[str]) -> Dict[str, str]:
    out = {}
    for it in items:
        k, sep, v = it.partition("=")
        if not sep or not k:
            raise ValueError(f"tag {it!r} must be KEY=VALUE")
        out[k] = v
    return out


def report_args(ap: argparse.ArgumentParser, spec: Dict[str, Any], out_dir: str) -> argparse.Namespace:
    rid = spec.get("id", "")
    if not REPORT_ID.match(rid):
        raise ValueError(f"report id {rid!r} must match {REPORT_ID.pattern}")
    unknown = set(spec) - REPORT_OPTIONS - {"id", "label", "tags"}
    if unknown:
        raise ValueError(f"report {rid}: unknown options {sorted(unknown)}")
    if sum(1 for k in ("trace_id", "slowest", "root") if spec.get(k)) != 1:
        raise ValueError(f"report {rid}: exactly one of trace_id, slowest, root is required")
    tags = spec.get("tags") or {}
    if not isinstance(tags, dict) or not all(isinstance(k, str) and k and isinstance(v, str) for k, v in tags.items()):
        raise ValueError(f"report {rid}: tags must map tag names to string values")
    if "sample" in spec:
        check_sample(spec["sample"])
    args = ap.parse_args(["--out", os.path.join(out_dir, f"{rid}.html"), "--quiet"])
    for k in REPORT_OPTIONS & set(spec):
        setattr(args, k, spec[k])
    args.tag = [f"{k}={v}" for k, v in tags.items()]
    return args


def run_batch(
    cfg: Config,
    es: ZipkinES,
    src: Optional[StudioAPI],
    profiled: List[str],
    ap: argparse.ArgumentParser,
    out_dir: str,
    llm: Optional[trace_advisor.LLM] = None,
) -> int:
    """Render every configured report; manifest.json lists what was written (and what failed)."""
    os.makedirs(out_dir, exist_ok=True)
    started = dt.datetime.now(dt.timezone.utc)
    entries = []
    for spec in cfg.reports:
        entry: Dict[str, Any] = {"id": spec.get("id"), "label": spec.get("label") or spec.get("id")}
        try:
            args = report_args(ap, spec, out_dir)
            entry.update(render(cfg, es, src, profiled, args, llm))
            entry["file"] = os.path.basename(args.out)
        except Exception as ex:  # noqa: BLE001 -- one bad report must not stop the others
            entry["error"] = f"{type(ex).__name__}: {str(ex)[:300]}"
            print(f"report {entry['id']}: {entry['error']}", file=sys.stderr)
        entries.append(entry)
    manifest = {"generated_at": iso(started), "reports": entries}
    with open(os.path.join(out_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    ok = sum(1 for e in entries if "file" in e)
    print(f"{ok}/{len(entries)} reports written to {out_dir}", file=sys.stderr)
    return 0 if ok or not entries else 1


def main() -> int:
    ap = build_parser()
    args = ap.parse_args()
    cfg = Config(args.config)
    es = ZipkinES(cfg)
    src: Optional[StudioAPI] = None
    profiled: List[str] = []
    if not args.no_profiles:
        try:
            src = StudioAPI(cfg.profile_api_url, cfg.profile_timeout_s)
            profiled = src.services_with_data()
        except Exception as e:  # noqa: BLE001
            print(f"warning: profile source unavailable ({e}); links only", file=sys.stderr)
            src = None
    llm = None if args.no_llm else trace_advisor.LLM(cfg.advisor.get("llm"))
    if args.batch:
        if not args.out_dir:
            ap.error("--batch needs --out-dir")
        return run_batch(cfg, es, src, profiled, ap, args.out_dir, llm)
    if not (args.trace_id or args.slowest or args.root) or not args.out:
        ap.error("one of --trace-id, --slowest, --root, --batch is required, and --out for single traces")
    try:
        render(cfg, es, src, profiled, args, llm)
    except LookupError as e:
        print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
