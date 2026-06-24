# Workload-Level Profiling Spec for Performance Studio

## Purpose

This spec defines the backend and UI design for workload-level profiling in
Performance Studio. It also serves as the source-of-truth document for
spec-driven development of future workload-selection and heartbeat-inventory
changes in this repo.

The design extends the existing heartbeat control plane rather than replacing
it: the backend stores workload inventory from agent heartbeats, exposes
workload-aware status views, and resolves workload selections into host/PID
commands before dispatch.

## Problem Statement

The existing dynamic profiling flow is host-centric:

- the UI shows one row per host
- requests target `target_hosts`
- the backend persists host heartbeats and host commands
- the agent receives commands by host/service

This is insufficient for Kubernetes-heavy deployments where users want to start
profiling from the level they reason about operationally:

- namespace
- workload
- pod
- container
- process

## Motivation

Before this work, Performance Studio only supported **host-level** profiling:
the user picked individual hosts and profiling commands were issued per host.
Workload-level profiling exists to address two concrete operational pain points.

### 1. Cluster churn breaks host-pinned profiling

Hosts are constantly removed from and added to a cluster (autoscaling, spot
reclamation, rolling replacements). With host-pinned selection, every time the
fleet changes the user has to return to the UI and re-select hosts, and any
host added after the original selection is simply **not profiled**.

Workload-level profiling fixes this by letting the user select an entire
**service** (and, in future, broader scopes). When a service is selected for
continuous profiling, the selection is treated as a durable **subscription**:
as new hosts for that service register via heartbeat, they are **immediately
and automatically enrolled** in profiling — no manual re-selection. See
[Continuous Service Subscriptions & Auto-Enrollment](#continuous-service-subscriptions--auto-enrollment).

### 2. Users often want a specific process/container/pod, not whole hosts

A host can run many workloads, but the user frequently cares about one
container, pod, or process (e.g. a single Java service in a shared node). Whole-
host profiling is both noisier and more expensive than necessary.

Workload-level profiling lets the user target the precise scope they reason
about (`namespace`, `workload`, `pod`, `container`, `process`) and the backend
resolves that selection down to the exact `hostname -> [pid, ...]` mapping the
agent executes. See [Resolution Model](#resolution-model).

## Goals

1. Add workload-aware inventory without breaking the current heartbeat protocol.
2. Keep command dispatch backward compatible with host-based agent execution.
3. Let the UI present workload tabs and workload-aware confirmation summaries.
4. Create a spec that future work can evolve first, before code changes.

## Non-Goals

- redesigning profiling commands around pod-native execution
- adding a brand-new command queue model
- guaranteeing globally stable Kubernetes workload IDs in v1
- solving historical inventory retention beyond current heartbeat freshness

## Design Principles

- **Additive schema changes only** for heartbeat inventory fields.
- **Backend resolution, agent execution**: workload targeting resolves to
  host/PID mappings in the backend.
- **Best-effort metadata**: missing namespace/pod/container fields must not
  break host-level behavior.
- **Freshness over history**: UI tabs represent active inventory from recent
  heartbeats.

## Data Model Changes

`HostHeartbeats` is extended with:

- `agent_version`
- `run_mode`
- `namespace`
- `pod_name`
- `containers jsonb`

The `containers` payload stores the flattened workload inventory reported by the
agent. Each container entry may include:

- container identity
- namespace/pod/workload metadata
- process list

`ProfilingRequests` remains API-level intent storage. Workload selectors are
stored in `additional_args` as part of the request contract so the existing
table does not need a full relational redesign in v1.

## API Contract

### Heartbeat Ingress

The existing `POST /api/metrics/heartbeat` endpoint now accepts optional
workload inventory fields:

```json
{
  "hostname": "node-a",
  "service_name": "checkout",
  "namespace": "observability",
  "pod_name": "gprofiler-abcde",
  "agent_version": "1.2.3",
  "run_mode": "k8s",
  "containers": [
    {
      "container_name": "checkout",
      "namespace": "shop",
      "pod_name": "checkout-7f8d9",
      "workload_name": "checkout",
      "workload_kind": "k8s",
      "processes": [
        { "pid": 1234, "process_name": "java" }
      ]
    }
  ]
}
```

### Profiling Request Ingress

The request contract is extended with:

- `target_scope`
- `target_entities`
- optional `target_hosts` for pure host targeting

Supported `target_scope` values:

- `host`
- `service`
- `namespace`
- `workload`
- `pod`
- `container`
- `process`

Each entry in `target_entities` may include service/namespace/host/pod/container
and process selectors.

## Resolution Model

Before creating commands, the backend resolves workload selectors against the
fresh heartbeat inventory:

1. fetch recent host heartbeats for the service
2. flatten `containers -> processes` into inventory records
3. filter records by the requested scope and selectors
4. convert the selection into:
   - `hostname -> null` for host-wide execution
   - `hostname -> [pid, ...]` for process-scoped execution

The command queue remains unchanged after this resolution step.

## Continuous Service Subscriptions & Auto-Enrollment

This realizes [Motivation #1](#1-cluster-churn-breaks-host-pinned-profiling):
a service-wide continuous profiling request behaves as a standing subscription
so that hosts which register *after* the request still get profiled.

### Subscription definition

A service is **actively subscribed** when its most recent service-scoped
(`additional_args.target_scope == "service"`) continuous (`continuous == true`)
`start` request in `ProfilingRequests` is newer than any service-scoped `stop`
request for that service, and the start request was not cancelled.
Implemented by `DBManager.get_active_service_subscription(service_name)`.

### Auto-enrollment on heartbeat

On every `POST /api/metrics/heartbeat`, after the host row is upserted, the
backend calls `DBManager.auto_subscribe_host_to_service(hostname, service_name)`
(see `receive_heartbeat`). The logic is:

1. Look up the active service subscription. If none, do nothing.
2. If the reporting host already has a current command, do nothing (so explicit
   per-host actions — including stops — are preserved).
3. Otherwise create a `start` command for the host, rebuilt from the
   subscription request's stored configuration (frequency, duration, mode,
   profiler configs). The command is created *before* the command lookup in the
   same heartbeat, so the new host receives it on the very next response.

### Behavior summary

| Situation | Result |
|-----------|--------|
| New host heartbeats for a service with an active subscription | Auto-enrolled (start command created immediately) |
| New host heartbeats for a service with no subscription | No command |
| New host heartbeats after a service-wide stop | No command (stop is newer than start) |
| Existing host already has a command | Left untouched |

### Edge cases / limitations (v1)

- Auto-enrollment only fires when a host has **no** current command. A host
  whose previous command reached a terminal state (`completed`/`failed`) is not
  re-enrolled in v1; continuous commands remain in `sent` state, so this is rare.
- Subscriptions are scoped to `service` only. Namespace/pod/container/process
  subscriptions are intentionally deferred (see Future Extensions).
- A host-level stop issued while a service subscription is active will keep that
  host stopped only until its command state is cleared; durable per-host opt-out
  within a subscription is future work.

## UI Design

The profiling console exposes tabs for:

- Services
- Namespaces
- Hosts
- Pods
- Containers
- Processes

The same page also supports:

- scope-aware filters
- tab counts derived from active inventory
- scope-aware selection summaries in the confirmation dialog
- reuse of the existing bulk start/stop workflow

## Freshness Rules

Workload inventory is based on recent heartbeats only. The initial design uses
the same active-host time window already used by the status page, so stale pods
and containers naturally disappear when agents stop reporting them.

## Failure Handling

If workload resolution finds no active targets:

- the API should reject the request with a clear validation error
- no commands should be created

If workload inventory is incomplete:

- host-level targeting must continue to work
- workload tabs may show partial data
- the UI should not invent missing workload relationships

## Backward Compatibility

This design preserves compatibility because:

- old heartbeats can omit new fields
- old host-level requests still work
- commands sent to agents remain host/PID based
- the UI can still represent host-only rows

## Spec-Driven Development Workflow

All future workload-level backend or UI changes should follow:

1. update this spec first
2. describe contract/schema changes explicitly
3. describe rollback and compatibility behavior
4. implement code afterward
5. keep the implementation aligned with the repo’s spec-driven guidance

## Future Extensions

Likely follow-up specs include:

- durable workload identifiers and richer workload kinds
- workload-level stop semantics that survive pod churn
- historical inventory snapshots
- workload-level flamegraph pivots and deep links
- stronger validation for mixed host and workload selections
