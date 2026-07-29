# Kubernetes sandbox for workload-level profiling

Architecture and rationale for the local Kubernetes sandbox that validates
workload-level (namespace / pod / container / process) profiling against a
**real cluster topology**.

> **Operational runbook** (prerequisites, `make` targets, quick start) lives next
> to the code: [`deploy/k8s-sandbox/K8S_SANDBOX.md`](../deploy/k8s-sandbox/K8S_SANDBOX.md).
> This page is the conceptual/architecture companion.

## Why a Kubernetes sandbox (and not just docker-compose)

The compose harness ([`deploy/E2E_HARNESS.md`](../deploy/E2E_HARNESS.md)) runs the
full stack fast and exercises the S3→SQS→indexer→ClickHouse→flamegraph pipeline,
but it **cannot** produce real namespaces/pods/containers. Workload-level
profiling resolves those scopes, and the agent builds that inventory in
`gprofiler/metadata/heartbeat_metadata.py` by asking `granulate_utils`'
`ContainersClient` to enumerate the node's containers via the **container-runtime
socket** (CRI at `/run/containerd/containerd.sock`, or Docker) — **not** the
Kubernetes API server. It reads the kubelet labels
`io.kubernetes.pod.namespace`, `io.kubernetes.pod.name`,
`io.kubernetes.container.name` off each container.

Under docker-compose there is no such socket for the agent, so it logs
`No container runtime found for heartbeat workload inventory` and the
pod/container/namespace tabs are only exercised with *synthetic* heartbeats. On a
real node the socket exists, so the inventory is **real**. That gap is the reason
this sandbox exists.

| Layer | Orchestrator | Proves | Can't do |
|-------|--------------|--------|----------|
| `deploy/Makefile.e2e` (compose) | Docker Compose | fast API + full artifact pipeline | no real namespaces/pods/containers |
| `deploy/k8s-sandbox` (this) | kind / minikube | **real namespace/pod/container/process inventory + scope resolution** | heavier, slower inner loop |

## What is a kind cluster?

**kind = "Kubernetes IN Docker."** The entire cluster *node* is itself a single
Docker container running the `kindest/node` image. Inside that container, an init
system (systemd) boots a container runtime and the kubelet, and Kubernetes then
runs all workloads as pods *inside* the node container. It is nested containers:

```
Your Linux host
└─ Docker
   └─ kind node  (container: kindest/node:v1.30.0)   <- "the cluster"
      ├─ systemd (PID 1)
      │   ├─ containerd.service     ← the CRI runtime that runs every pod
      │   ├─ kubelet.service        ← node agent: talks to the API server, drives containerd
      │   └─ systemd-journald
      └─ pods (run by containerd, orchestrated by Kubernetes)
```

minikube is the same idea using a VM (or a container). kind is lighter and
containerd-native, which is why it is the default here and recommended for CI.
Both give a **hermetic, disposable** cluster: create → test → destroy, nothing
touches real infrastructure.

## Topology: what runs as systemd vs. as pods

This is the key mental model. Inside the kind node, **only three things run under
systemd** — everything else (including Kubernetes' own control plane) runs as
pods managed by the kubelet + containerd:

**systemd services (inside the node container):**

| Unit | Role |
|------|------|
| `containerd.service` | container runtime (CRI) that actually runs all pods |
| `kubelet.service` | node agent; `--container-runtime-endpoint=unix:///run/containerd/containerd.sock` |
| `systemd-journald.service` | logging |

Notably, the kubelet's CRI endpoint is the **exact socket the agent DaemonSet
reads** (via `/proc/1/root`, see below) to enumerate workloads.

**Everything else is a pod:**

- **Kubernetes control plane** (`kube-system`, run as *static pods*):
  `kube-apiserver`, `etcd`, `kube-scheduler`, `kube-controller-manager`, plus
  `kube-proxy`, `kindnet` (CNI), `coredns`, and `local-path-provisioner`.
- **Performance Studio stack** (`perf-studio` namespace): `webapp`, `postgres`,
  `clickhouse`, `ch-rest-service`, `ch-indexer`, `logs-backend` (2 containers),
  `nginx`, `localstack`, and the **`gprofiler-agent` DaemonSet**.
- **Tenant workloads**: `team-a` (`checkout` ×2, `web`) and `team-b`
  (`payments`, `search` — a 2-container pod).

So even etcd and the API server are pods — a kind design choice. Contrast
docker-compose, where each service is a bare container on a bridge network with
**no kubelet, no containerd-as-CRI, and no pods**, which is exactly why the agent
finds "no container runtime" there but discovers real pods/namespaces/containers
here.

## How the agent DaemonSet reaches the CRI socket

`granulate_utils` resolves the socket path under `HOST_ROOT_PREFIX = /proc/1/root`
(`granulate_utils/linux/ns.py`). The mechanism is two pod settings, not a
bind-mount:

- **`hostPID: true`** → PID 1 in the pod is the host (node) init, so
  `/proc/1/root` is the node root filesystem and
  `/proc/1/root/run/containerd/containerd.sock` is the node's real CRI socket.
  `hostPID` also lets the agent see every node PID to map processes → containers.
- **`privileged: true`** → grants access to that socket and lets py-spy `ptrace`
  the target workloads.

> **Use the source-built agent.** The container-inventory heartbeat is a fork
> feature, so the public `intel/gprofiler:latest` image will **not** populate the
> pod/container tabs.

## Data flow

```
 kind node (containerd)
   ns team-a: checkout(x2), web       ns team-b: payments, search(app+sidecar)
          ▲ enumerated via CRI (/proc/1/root/run/containerd/...)
   gprofiler-agent DaemonSet ─ heartbeat/commands ─► webapp ─► postgres
    (hostPID, privileged)               │
                                        └─ upload ─► S3 (LocalStack)
                                                       ▼
                                             SQS ─► ch-indexer ─► ClickHouse
                                                       ▼
                                             nginx NodePort :30443 (UI)
```

## Verified end-to-end

Brought up on a single-node **kind v1.30** cluster (containerd 1.7) and confirmed:

- Agent DaemonSet connects and heartbeats with **no** `No container runtime
  found` error (contrast compose) — it reached the node CRI socket.
- `workload_status` reported real topology — `tabCounts` `{service:1, host:1,
  namespace:5, pod:23, container:25, process:93}` — including tenant namespaces
  `team-a`/`team-b` and the 2-container `search` pod resolved as distinct
  `search-app` + `search-sidecar`.
- Acceptance suite `AT-K1..K5` passed (5/5).
- **Full artifact pipeline closed in-cluster**: a host-scope start made the agent
  profile the real workloads and upload; the webapp wrote collapsed stacks to S3
  (`products/k8s-sandbox/stacks/...gz`) and enqueued SQS; the indexer consumed it,
  inserted 27 rows into `flamedb.samples`, and wrote the rendered
  `..._adhoc_flamegraph.html` back to S3 — all against LocalStack, no real AWS.

Two k8s-specific fixes came out of that run:

- **Non-root low-port bind** (webapp, agents-logs-backend run as non-root and
  bind port 80): Docker allows this via its default
  `net.ipv4.ip_unprivileged_port_start=0`; Kubernetes does not, so those pods set
  that (safe) sysctl explicitly.
- **Indexer startup ordering**: compose gated it on `localstack: service_healthy`;
  k8s has no `depends_on` and the indexer resolves the SQS URL once without retry,
  so it can boot before LocalStack and never consume. An init-container now waits
  for LocalStack's SQS to report running.
