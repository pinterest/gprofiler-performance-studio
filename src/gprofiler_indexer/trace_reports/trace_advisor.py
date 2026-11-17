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
"""Findings and recommendations for one trace <-> profile report.

Rules (thresholds from config "advisor.rules") turn the span tree and the joined profiles into
findings with evidence. An optional LLM step (config "advisor.llm": any OpenAI-compatible chat
completions endpoint) ranks and explains them. The prompt is always returned so the report can
offer it for pasting into another LLM.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_RULES: Dict[str, Dict[str, Any]] = {
    "critical_path": {"top": 6},
    "client_gap": {"min_ms": 150},
    "fan_out": {"min_calls": 5, "min_total_ms": 50},
    "sequential_calls": {"min_calls": 3, "min_each_ms": 20, "min_saving_ms": 100},
    "off_cpu_wait": {"min_excl_ms": 100, "max_cpu_pct": 0.5},
    "unprofiled": {"min_excl_ms": 100},
    "cpu_hotspot": {"min_pct": 5.0},
    "cross_trace": {"min_mean_excl_ms": 100, "min_presence": 0.5},
}
MAX_PER_RULE = 5
SLACK_US = 1000  # a child ending this close after the cursor still counts as on the critical path

SYSTEM_PROMPT = """You are a senior performance engineer. You get one end-to-end trace of a user-facing client flow
(client root span -> backend services), joined with cluster-wide CPU profiles, plus rule-based findings.
Recommend the changes most likely to cut the latency users see.

Rules:
- Only the critical path moves end-to-end latency; prefer findings on it. Fan-out and sequential calls under it are
  the usual wins (batch, parallelize, cache, drop). CPU hotspots matter when the cluster is on the critical path.
- "off_cpu_wait" means the time is waiting (I/O, locks, pools, untraced calls), so CPU tuning will not help it.
- One trace is one request; CPU shares are cluster-wide over the profile window, across all requests.
- Cite only finding ids that appear in the input as evidence. Do not invent services, spans or numbers.
- At most 5 recommendations, most valuable first. Be specific and short.

Reply with JSON only:
{"summary": "<one or two sentences>",
 "recommendations": [{"title": "...", "why": "...", "action": "...", "owner": "<a cluster name from the input, or path.platform for client-side work>",
                      "evidence": ["F1"], "est_savings_ms": <number or null>, "confidence": "high|medium|low"}]}"""


def rules_config(cfg: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Defaults merged with config overrides; a rule set to false is disabled."""
    over = (cfg or {}).get("rules") or {}
    out = {}
    for name, dflt in DEFAULT_RULES.items():
        o = over.get(name, {})
        if o is False:
            continue
        out[name] = {**dflt, **(o if isinstance(o, dict) else {})}
    return out


def _end(n: Any) -> int:
    return n.eff_start + n.eff_us


def _ms(us: float) -> int:
    return int(round(us / 1000))


def _union_us(iv: List[Tuple[int, int]]) -> int:
    tot, cur = 0, None
    for a, b in sorted(iv):
        if cur is None or a > cur[1]:
            if cur:
                tot += cur[1] - cur[0]
            cur = [a, b]
        else:
            cur[1] = max(cur[1], b)
    return tot + (cur[1] - cur[0] if cur else 0)


def _overlap(n: Any, lo: int, hi: int) -> int:
    return max(min(_end(n), hi) - max(n.eff_start, lo), 0)


def critical_path(root: Any) -> List[Tuple[Any, int]]:
    """Spans on the critical path with the time each holds it (us): walk back from the end of each
    span through the child that finished last before the cursor. A childless span mostly covered by
    a sibling with work under it is taken to be waiting on that sibling (client tracers often parent
    the request's server side to the page root instead of the request span)."""
    out: List[Tuple[Any, int]] = []

    def walk(n: Any, lo: int, hi: int) -> None:
        t, covered = hi, 0
        for c in sorted(n.children, key=_end, reverse=True):
            if t <= lo:
                break
            cs, ce = max(c.eff_start, lo), min(_end(c), t)
            if _end(c) > t + SLACK_US or ce <= cs:
                continue
            busy = None
            if not c.children and c.members is None:
                busy = max(
                    (s for s in n.children if s is not c and s.children),
                    key=lambda s: _overlap(s, cs, ce),
                    default=None,
                )
                if busy is not None and _overlap(busy, cs, ce) < (ce - cs) / 2:
                    busy = None
            if busy is not None:
                bs, be = max(busy.eff_start, cs), min(_end(busy), ce)
                walk(busy, bs, be)
                out.append((c, (ce - cs) - (be - bs)))
            else:
                walk(c, cs, ce)
            covered += ce - cs
            t = cs
        out.append((n, max(hi - lo - covered, 0)))

    walk(root, root.eff_start, _end(root))
    return out


def owner_of(n: Any, parents: Dict[int, Any]) -> str:
    """Cluster of the nearest server span; its service when that span has no cluster tag (e.g. a CDN)."""
    cur = n
    while cur is not None:
        if cur.cluster:
            return cur.cluster
        if cur.kind == "SERVER":
            return cur.service
        cur = parents.get(id(cur))
    return n.service


def _walk(n: Any):
    yield n
    for c in n.children:
        yield from _walk(c)


def _is_call(c: Any, parent: Any) -> bool:
    return c.kind == "CLIENT" or (c.kind == "SERVER" and c.cluster and c.cluster != parent.cluster)


def find(
    root: Any,
    parents: Dict[int, Any],
    ranked: List[Dict[str, Any]],
    info: Dict[str, Dict[str, Any]],
    prof: Dict[str, Any],
    agg_rows: List[Dict[str, Any]],
    agg_n: int,
    client_services: List[str],
    rules: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Rule-based findings, most impactful first, with ids F1..Fn."""
    total_us = max(root.eff_us, 1)
    found: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    cp = critical_path(root)

    if "critical_path" in rules:
        by_owner: Dict[str, int] = defaultdict(int)
        for n, us in cp:
            by_owner[owner_of(n, parents)] += us
        top = sorted(by_owner.items(), key=lambda kv: -kv[1])[: rules["critical_path"]["top"]]
        spans = sorted((x for x in cp if x[1] > 0), key=lambda x: -x[1])[:8]
        found["critical_path"].append(
            {
                "title": (
                    f"Critical path: {top[0][0]} holds {_ms(top[0][1])} ms of {_ms(total_us)} ms"
                    if top
                    else "Critical path"
                ),
                "detail": "time on the critical path by owner: " + ", ".join(f"{o} {_ms(us)} ms" for o, us in top),
                "impact_ms": _ms(total_us),
                "cluster": top[0][0] if top else None,
                "evidence": [{"span": n.label(), "ms": _ms(us)} for n, us in spans],
            }
        )

    if "client_gap" in rules:
        r = rules["client_gap"]
        for n, us in cp:
            if n.service not in client_services or us < r["min_ms"] * 1000:
                continue
            remote = any(c.kind == "SERVER" for c in n.children)
            found["client_gap"].append(
                {
                    "title": f"{_ms(us)} ms of client time on the critical path in {n.label()}",
                    "detail": (
                        "network and queueing around the backend call (span time outside the server span)"
                        if remote
                        else "client-side time with no child span: rendering, parsing, scheduling, or waiting on "
                        "something that is not traced"
                    ),
                    "impact_ms": _ms(us),
                    "cluster": n.service,
                    "evidence": [{"span": n.label(), "ms": _ms(us)}],
                }
            )

    on_path = {id(n) for n, us in cp}
    for n in _walk(root):
        if "fan_out" in rules:
            r = rules["fan_out"]
            groups: Dict[Tuple[str, str], List[Any]] = defaultdict(list)
            for c in n.children:
                if _is_call(c, n) or c.members:
                    groups[(c.service, c.name)].append(c)
            for (svc, name), cs in groups.items():
                iv = [m for c in cs for m in (c.members or [(c.eff_start, _end(c))])]
                busy = sum(b - a for a, b in iv)
                if len(iv) < r["min_calls"] or busy < r["min_total_ms"] * 1000:
                    continue
                wall = _union_us(iv)
                found["fan_out"].append(
                    {
                        "title": f"{len(iv)}× {svc}: {name} under {n.label()}",
                        "detail": f"{_ms(busy)} ms of calls in {_ms(wall)} ms wall "
                        f"({'mostly parallel' if busy > 1.5 * wall else 'mostly serial'})"
                        f"{', on the critical path' if any(id(c) in on_path for c in cs) else ''}; "
                        "fewer, batched calls cut per-call overhead and tail latency",
                        "impact_ms": _ms(wall),
                        "cluster": owner_of(cs[0], parents),
                        "evidence": [{"span": cs[0].label(), "ms": _ms(wall)}],
                    }
                )
        if "sequential_calls" in rules:
            r = rules["sequential_calls"]
            calls = sorted(
                (c for c in n.children if _is_call(c, n) and c.eff_us >= r["min_each_ms"] * 1000),
                key=lambda c: c.eff_start,
            )
            run: List[Any] = []

            def flush() -> None:
                if len(run) >= r["min_calls"]:
                    saving = sum(c.eff_us for c in run) - max(c.eff_us for c in run)
                    if saving >= r["min_saving_ms"] * 1000:
                        found["sequential_calls"].append(
                            {
                                "title": f"{len(run)} sequential calls under {n.label()}",
                                "detail": "back to back: "
                                + ", ".join(f"{c.label()} {_ms(c.eff_us)} ms" for c in run[:6])
                                + f"; if independent, running them concurrently saves up to {_ms(saving)} ms"
                                + (", on the critical path" if id(n) in on_path else ""),
                                "impact_ms": _ms(saving),
                                "cluster": owner_of(n, parents),
                                "evidence": [{"span": c.label(), "ms": _ms(c.eff_us)} for c in run[:6]],
                            }
                        )

            for c in calls:
                if run and c.eff_start < _end(run[-1]) - SLACK_US:
                    flush()
                    run = []
                run.append(c)
            flush()

    for a in ranked:
        cl, c = a["cluster"], info.get(a["cluster"], {})
        target = c.get("proxy") or (cl if c.get("profiled") else None)
        p = prof.get(target) if target else None
        if (
            "unprofiled" in rules
            and a["excl_ms"] >= rules["unprofiled"]["min_excl_ms"]
            and not (p and p.get("outcome") == "ok")
        ):
            found["unprofiled"].append(
                {
                    "title": f"{cl}: {a['excl_ms']:.0f} ms exclusive with no CPU profile",
                    "detail": (
                        "no gProfiler data for the cluster or a proxy in the window"
                        if not target
                        else f"profile for {target} not available ({(p or {}).get('outcome', 'not fetched')})"
                    )
                    + "; profile it to see where the time goes",
                    "impact_ms": round(a["excl_ms"]),
                    "cluster": cl,
                    "evidence": [{"cluster": cl, "ms": round(a["excl_ms"])}],
                }
            )
        if (
            "off_cpu_wait" in rules
            and p
            and p.get("outcome") == "ok"
            and a["excl_ms"] >= rules["off_cpu_wait"]["min_excl_ms"]
        ):
            joined = [p["ops"][k]["pct"] for k in a["op_keys"] if k in p.get("ops", {})] + [
                p["classes"][k]["pct"] for k in a["classes"] if k in p.get("classes", {})
            ]
            if (a["op_keys"] or a["classes"]) and max(joined, default=0) <= rules["off_cpu_wait"]["max_cpu_pct"]:
                found["off_cpu_wait"].append(
                    {
                        "title": f"{cl}: {a['excl_ms']:.0f} ms exclusive but its handlers barely use CPU",
                        "detail": f"span ops {', '.join(a['ops'][:3])} join to ≤{max(joined, default=0):.2f}% of "
                        f"{target} CPU: the time is waiting (I/O, locks, thread pools, untraced calls), "
                        "so CPU tuning will not help",
                        "impact_ms": round(a["excl_ms"]),
                        "cluster": cl,
                        "evidence": [{"cluster": cl, "ms": round(a["excl_ms"])}],
                    }
                )

    if "cpu_hotspot" in rules:
        r = rules["cpu_hotspot"]
        for target, p in prof.items():
            if p.get("outcome") != "ok":
                continue
            for f in p.get("top_self", [])[:3]:
                if f["pct"] >= r["min_pct"]:
                    found["cpu_hotspot"].append(
                        {
                            "title": f"{f['pct']}% of {target} CPU in {f['frame']} (self)",
                            "detail": f"cluster-wide self CPU over the profile window; {p.get('app_pct')}% of samples are app code",
                            "impact_ms": None,
                            "cluster": target,
                            "evidence": [{"frame": f["frame"], "pct": f["pct"]}],
                        }
                    )
            for op, j in sorted(p.get("ops", {}).items(), key=lambda kv: -kv[1]["pct"])[:2]:
                if j["pct"] >= r["min_pct"]:
                    found["cpu_hotspot"].append(
                        {
                            "title": f"span op {op} accounts for {j['pct']}% of {target} CPU",
                            "detail": "frames named after this trace's span op, inclusive; top: "
                            + ", ".join(f"{x['frame']} {x['pct']}%" for x in j["frames"][:3]),
                            "impact_ms": None,
                            "cluster": target,
                            "evidence": [{"frame": x["frame"], "pct": x["pct"]} for x in j["frames"][:3]],
                        }
                    )

    if "cross_trace" in rules and agg_n:
        r = rules["cross_trace"]
        for row in agg_rows:
            if row["mean_excl_ms"] >= r["min_mean_excl_ms"] and row["traces"] / agg_n >= r["min_presence"]:
                found["cross_trace"].append(
                    {
                        "title": f"{row['cluster']}: {row['mean_excl_ms']:.0f} ms per trace across {row['traces']}/{agg_n} traces",
                        "detail": "consistently expensive on this path, not a one-off in the sampled trace; ops "
                        + ", ".join(row["ops"]),
                        "impact_ms": round(row["mean_excl_ms"]),
                        "cluster": row["cluster"],
                        "evidence": [{"cluster": row["cluster"], "ms": round(row["mean_excl_ms"])}],
                    }
                )

    out: List[Dict[str, Any]] = []
    for rule in rules:
        items = sorted(found.get(rule, []), key=lambda f: -(f["impact_ms"] or 0))[:MAX_PER_RULE]
        out += [dict(f, rule=rule) for f in items]
    head = [f for f in out if f["rule"] == "critical_path"]
    rest = sorted(
        (f for f in out if f["rule"] != "critical_path"), key=lambda f: (f["impact_ms"] is None, -(f["impact_ms"] or 0))
    )
    for i, f in enumerate(head + rest, 1):
        f["id"] = f"F{i}"
    return head + rest


def prompt(
    path_meta: Dict[str, Any],
    trace: Dict[str, Any],
    ranked: List[Dict[str, Any]],
    info: Dict[str, Dict[str, Any]],
    prof: Dict[str, Any],
    agg_rows: List[Dict[str, Any]],
    agg_n: int,
    findings: List[Dict[str, Any]],
) -> str:
    ctx = {
        "path": path_meta,
        "trace": trace,
        "clusters": [
            {
                "cluster": a["cluster"],
                "excl_ms": round(a["excl_ms"]),
                "self_ms": round(a["self_ms"]),
                "spans": a["spans"],
                "ops": a["ops"][:4],
                "profile": info[a["cluster"]]["proxy"] or (a["cluster"] if info[a["cluster"]]["profiled"] else None),
            }
            for a in ranked[:12]
        ],
        "across_traces": (
            {
                "n": agg_n,
                "clusters": [
                    {"cluster": r["cluster"], "mean_excl_ms": round(r["mean_excl_ms"]), "traces": r["traces"]}
                    for r in agg_rows[:10]
                ],
            }
            if agg_n
            else None
        ),
        "profiles": {
            t: {
                "app_pct": p.get("app_pct"),
                "top_inclusive": [[f["frame"], f["pct"]] for f in p.get("top", [])[:6]],
                "top_self": [[f["frame"], f["pct"]] for f in p.get("top_self", [])[:6]],
            }
            for t, p in list(prof.items())[:6]
            if p.get("outcome") == "ok"
        },
        "findings": [
            {k: f[k] for k in ("id", "rule", "title", "detail", "impact_ms", "cluster", "evidence")} for f in findings
        ],
    }
    return "Report context (JSON):\n" + json.dumps(ctx, indent=1, ensure_ascii=False)


def _text(v: Any, n: int) -> str:
    return str(v)[:n] if v is not None else ""


def parse_recommendations(content: str, ids: set) -> Dict[str, Any]:
    """LLM output is untrusted: keep known fields, cap lengths, keep only evidence ids we sent."""
    start, end = content.find("{"), content.rfind("}")
    d = json.loads(content[start : end + 1]) if start >= 0 else {}
    recs = []
    for r in (d.get("recommendations") or [])[:5]:
        if not isinstance(r, dict):
            continue
        s = r.get("est_savings_ms")
        recs.append(
            {
                "title": _text(r.get("title"), 200),
                "why": _text(r.get("why"), 800),
                "action": _text(r.get("action"), 800),
                "owner": _text(r.get("owner"), 120),
                "evidence": [e for e in (r.get("evidence") or []) if isinstance(e, str) and e in ids][:8],
                "est_savings_ms": round(s) if isinstance(s, (int, float)) and not isinstance(s, bool) else None,
                "confidence": r.get("confidence") if r.get("confidence") in ("high", "medium", "low") else None,
            }
        )
    return {"summary": _text(d.get("summary"), 600), "recommendations": recs}


class LLM:
    """OpenAI-compatible chat completions client; disabled when no URL is configured."""

    def __init__(self, cfg: Dict[str, Any]):
        c = cfg or {}
        self.url = os.environ.get(c.get("url_env", "TRACE_REPORTS_LLM_URL")) or c.get("url") or ""
        self.model = os.environ.get(c.get("model_env", "TRACE_REPORTS_LLM_MODEL")) or c.get("model") or ""
        self.headers = dict(c.get("headers") or {})
        key = os.environ.get(c.get("api_key_env", "TRACE_REPORTS_LLM_API_KEY"))
        if key:
            self.headers["Authorization"] = f"Bearer {key}"
        self.timeout = int(c.get("timeout_seconds", 120))
        self.calls_left = int(c.get("max_calls_per_run", 30))
        self.enabled = bool(self.url and self.model) and c.get("enabled", True) is not False

    def complete(self, system: str, user: str) -> str:
        self.calls_left -= 1
        body = {
            "model": self.model,
            "stream": False,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        req = urllib.request.Request(
            self.url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **self.headers}
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.load(r)["choices"][0]["message"]["content"]


def advise(
    cfg: Dict[str, Any],
    llm: Optional[LLM],
    root: Any,
    parents: Dict[int, Any],
    ranked: List[Dict[str, Any]],
    info: Dict[str, Dict[str, Any]],
    prof: Dict[str, Any],
    agg_rows: List[Dict[str, Any]],
    agg_n: int,
    client_services: List[str],
    path_meta: Dict[str, Any],
    trace: Dict[str, Any],
) -> Dict[str, Any]:
    findings = find(root, parents, ranked, info, prof, agg_rows, agg_n, client_services, rules_config(cfg))
    user = prompt(path_meta, trace, ranked, info, prof, agg_rows, agg_n, findings)
    out: Dict[str, Any] = {"findings": findings, "prompt": SYSTEM_PROMPT + "\n\n" + user}
    if llm and llm.enabled and findings:
        if llm.calls_left <= 0:
            out["llm_error"] = "skipped: max_calls_per_run reached"
        else:
            try:
                out["llm"] = dict(
                    parse_recommendations(llm.complete(SYSTEM_PROMPT, user), {f["id"] for f in findings}),
                    model=llm.model,
                )
            except Exception as ex:  # noqa: BLE001 -- recommendations are optional; the report still renders
                out["llm_error"] = f"{type(ex).__name__}: {str(ex)[:200]}"
                print(f"  warning: LLM recommendations failed ({out['llm_error']})", file=sys.stderr)
    return out


def summary(advice: Dict[str, Any]) -> Dict[str, Any]:
    """Small index.json entry: counts and the top titles."""
    recs = (advice.get("llm") or {}).get("recommendations") or []
    s: Dict[str, Any] = {
        "findings": len(advice["findings"]),
        "top": [r["title"] for r in recs[:3]] or [f["title"] for f in advice["findings"][:3]],
    }
    if advice.get("llm"):
        s["model"] = advice["llm"].get("model")
        s["recommendations"] = len(recs)
    if advice.get("llm_error"):
        s["llm_error"] = advice["llm_error"]
    return s
