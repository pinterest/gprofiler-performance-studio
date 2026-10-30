# GPU (nsys) adhoc flamegraphs — design (phases 0–4)

## Goal

Let operators capture **NVIDIA CUDA kernel** activity via **Nsight Systems (`nsys`)** on a
real GPU host, upload the resulting HTML through the existing adhoc profile path, and view it
in Performance Studio’s **Adhoc Profiling** iframe — without pretending Kind workers have GPUs.

## iaprof vs nsys (do not conflate)

| | [Doom GPU Flame Graphs / iaprof](https://www.brendangregg.com/blog/2025-05-01/doom-gpu-flame-graphs.html) | NVIDIA nsys (this work) |
|---|---|---|
| Hardware | Intel Xe / Battlemage / Lunar Lake | NVIDIA (e.g. A10G) |
| Method | EU-stall + CPU stacks → full-stack GPU flame + FlameScope | CUDA/API timelines, `.nsys-rep`, stats/export → HTML |
| On NVIDIA hosts | Will not run | Correct tool |

This feature is **nsys → Adhoc HTML**, inspired by the iaprof *UX* (pick a capture → browse a
flame-like view). Keep **iaprof as a later Intel-GPU backend** behind the same “GPU profiler”
checkbox/interface (phase 5).

## Topology

```
┌─ Kind (Studio control plane) ──────────────────┐
│  webapp / indexer / LocalStack / nginx :30443  │
└──────────────────────▲─────────────────────────┘
                       │ heartbeat + upload + profile_request
┌──────────────────────┴─────────────────────────┐
│  gProfiler agent on HOST (privileged)          │
│  --enable-heartbeat-server                     │
│  nsys on host PATH → GPU capture               │
│  optional: cuda_burn workload                  │
└────────────────────────────────────────────────┘
```

- **Kind** = Studio only (same as `Makefile.k8s` today).
- **DaemonSet agent** inside Kind cannot capture GPU: the node container receives
  `/dev/nvidia*` from the NVIDIA runtime, but has no CUDA driver libraries, so neither
  `nsys` nor a CUDA binary runs there.
- The GPU agent therefore runs as its **own container** on the host (`--gpus all`) rather
  than as a bare host process. gProfiler's mutex is an abstract socket in the **init network
  namespace**, so only one privileged agent can exist per host; a separate network namespace
  avoids fighting the DaemonSet or a pre-existing fleet agent for that slot.
- Studio is reached over a `kubectl port-forward` bound to all addresses, via
  `host.docker.internal`, because the agent is not on the host network.
- UI: `https://localhost:30443` (basic auth from `deploy/.htpasswd`).

## Packaging

Mirror PerfSpect: **detect host `nsys`, do not bundle** Nsight Systems into the agent image.
Optional path override on the agent; Studio only sends `additional_args.enable_nsys`.

## Phases

| Phase | Scope | Outcome |
|---|---|---|
| **0** | Host: install/detect `nsys`; smoke `nsys profile` on a tiny CUDA sample | Prove capture on real NVIDIA HW |
| **1** | Agent: honor `enable_nsys` from `combined_config` (PerfSpect-shaped); run capture; attach HTML + tag `perf_events` (`nsys` / `nsys-cuda`) | Same control plane as CPU adhoc |
| **2** | Studio Adhoc UI: Chip `GPU / nsys` when events/filename hint GPU; empty-state helper | Shows next to CPU adhoc |
| **3** | Console checkbox **GPU (nsys)** → `additional_args.enable_nsys` | Operator trigger (Adhoc recommended, not forced) |
| **4** | Sandbox `gpu-*` Makefile targets + `run_host_agent_adhoc.sh` | E2E without lying about Kind GPU |
| **5** (later) | Optional iaprof backend for Intel hosts | True Doom-style stacks where HW allows |

## Studio control-plane contract

1. UI sets `enable_nsys` in `additional_args` via `profilingRequestBuilder.mjs`.
2. Backend merges `additional_args` into top-level `combined_config` for the agent command.
3. Agent uploads `flamegraph_html`; indexer stores metadata (`perf_events` may include `nsys`).
4. `/api/metrics/adhoc_flamegraphs` already returns `FlamegraphFile.perf_events` — no new route.

## Security note

Adhoc / dynamic profiling metrics routes remain weakly authenticated in some deployments
(see `SECURITY-adhoc-dynamic-profiling-spec.md`). Running `nsys` as root on GPU hosts raises
the impact of an unauthenticated `profile_request` — treat sandbox demos as localhost-only
until control-plane auth lands.

## Sandbox make targets

See `gpu/README.md` and `Makefile.k8s` (`gpu-check`, `gpu-build`, `gpu-smoke`, `gpu-demo`).
