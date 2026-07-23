# End-to-end test harness (Performance Studio + real gProfiler agent)

This harness runs the **full** Performance Studio stack **plus a real gProfiler
agent** locally, so the dynamic/workload-level profiling flow can be exercised
end to end — including the S3 → SQS → indexer → ClickHouse → flamegraph pipeline
that the studio-only setup never touches.

```
 e2e-sample-app (CPU workload)
        ▲ profiled by
 gprofiler-agent ──heartbeat/commands──► webapp (backend)  ──► Postgres
        │                                     │
        └──upload profile (/api/v2/profiles)──┘
                                              ▼
                                    S3 (LocalStack) ──► SQS (LocalStack)
                                                          ▼
                                             ch-indexer ──► ClickHouse (flamedb)
                                                          ▼
                                              UI / flamegraph API (nginx :4433)
```

## Files

| File | Purpose |
|------|---------|
| `docker-compose.e2e.yml` | Overlay adding `e2e-sample-app`, `gprofiler-agent`, `e2e-tests`, `e2e-ui-tests` |
| `Makefile.e2e` | `up / up-src / status / start / stop / flamegraph / test / ui-test / down` targets |
| `e2e/agent-glibc.Dockerfile` | Wraps a source-built (`--fast`) agent exe in a glibc base |
| `../src/tests/e2e/` | In-network pytest API acceptance runner (AT-S1..S15) |
| `../src/tests/playwright/` | Playwright UI acceptance runner |

## Container architecture

**It is not one container and not a Kubernetes pod.** It is a set of independent
Docker containers wired together by Docker Compose on a single user-defined
bridge network (`<project>_default`, i.e. `deploy_default`). Each service is its
own container running one process; they find each other by **service name** as a
DNS hostname on that network. There is no orchestrator (no k8s, no pod spec) —
just Compose.

| Container | Image / build | Role |
|-----------|---------------|------|
| `gprofiler-ps-webapp` | built from `../src` | FastAPI backend + built UI (heartbeat, profile ingest, status APIs) |
| `gprofiler-ps-nginx-load-balancer` | `nginx:1.23.3` | TLS + basic-auth edge; publishes `:4433`/`:8443` to the host |
| `gprofiler-ps-postgres` | `postgres` | heartbeat inventory, profiling requests/commands |
| `gprofiler-ps-clickhouse` | `clickhouse` | `flamedb` sample/metric storage (profile `with-clickhouse`) |
| `gprofiler-ps-ch-rest-service` | built | ClickHouse REST facade for the backend |
| `gprofiler-ps-ch-indexer` | built | **SQS consumer**: reads S3 profiles → writes ClickHouse |
| `gprofiler-ps-agents-logs-backend` | built | agent log ingest |
| `gprofiler-ps-periodic-tasks` | built | background jobs |
| `gprofiler-ps-localstack` | `localstack/localstack:3.0` | **one** container emulating **S3 + SQS** |
| `gprofiler-ps-e2e-sample-app` | `python:3.11-slim` | deterministic CPU workload to profile *(harness)* |
| `gprofiler-ps-e2e-agent` | source-built or `GPROFILER_IMAGE` | real gProfiler agent in heartbeat mode *(harness)* |
| `gprofiler-ps-e2e-tests` | built from `../src/tests/e2e` | pytest API acceptance runner, profile `test` *(harness)* |
| `gprofiler-ps-e2e-ui-tests` | official Playwright image | Playwright UI runner, profile `ui` *(harness)* |

Only nginx publishes host ports; everything else talks over the internal
network. The agent, for example, reaches the backend at `http://webapp` directly
(no host port, no nginx auth).

## S3 + SQS via LocalStack (no AWS account needed)

The `localstack` container runs the S3 and SQS emulators in-process
(`SERVICES=s3,sqs`) and exposes a single gateway on `:4566`. On startup it runs
every script in `deploy/localstack_init/` — `01_init_s3_sqs.sh` creates the
bucket and queue and wires S3 event notifications into SQS. A healthcheck gates
dependents until both services report `running`.

The pipeline then flows entirely inside the network:

1. agent uploads a profile to `webapp` (`/api/v2/profiles`)
2. `webapp` writes the object to **S3** (`s3://performance-studio-bucket/...`)
3. S3 emits an event to **SQS** (`performance-studio-queue`)
4. `ch-indexer` consumes the SQS message, reads the object, and writes rows to
   **ClickHouse** (`flamedb.samples`) plus the flamegraph HTML back to S3

All AWS SDK clients in the stack point at `AWS_ENDPOINT_URL=http://localstack:4566`
with dummy `test`/`test` credentials, so nothing touches real AWS.

## Prerequisites

- **Docker + Docker Compose v2** (Linux, or macOS/Windows via Docker Desktop).
- The **`gprofiler` agent repo** checked out as a sibling of this repo
  (`../../gprofiler`) — required for `e2e-up-src` (the portable agent path).
- **TLS cert + basic-auth file** for nginx (one-time, in `deploy/`):

```bash
cd deploy
mkdir -p tls
openssl req -x509 -nodes -days 365 -newkey rsa:2048 -keyout tls/key.pem -out tls/cert.pem
openssl req -x509 -nodes -days 365 -newkey rsa:2048 -keyout tls/ch_rest_key.pem -out tls/ch_rest_cert.pem
htpasswd -B -C 12 -c .htpasswd admin      # set the password to 'admin' to match the defaults
```

The harness assumes web creds `admin` / `admin` (override with `AUTH=` on the
Make targets and the `E2E_BASIC_AUTH_*` env vars).

## Quick start

```bash
cd deploy

# Bring up studio + sample app + agent (mints a valid profiler token for you).
make -f Makefile.e2e e2e-up

# See the agent's live inventory row (heartbeat, agent version, status).
make -f Makefile.e2e e2e-status

# Drive the control plane:
make -f Makefile.e2e e2e-start     # host-scope START (profiles the sample app)
make -f Makefile.e2e e2e-stop      # host-scope STOP  (no PIDs at host level)

# Inspect the artifacts the pipeline produced in (Local)S3.
make -f Makefile.e2e e2e-flamegraph

# Follow the agent.
make -f Makefile.e2e e2e-agent-logs

# Remove ONLY the harness services (studio keeps running).
make -f Makefile.e2e e2e-down
```

`SERVICE=<name>` overrides the target service (default `e2e-sample-app`) on the
`e2e-start` / `e2e-stop` / `e2e-flamegraph` targets.

## How the agent is wired

- **Endpoint / auth.** The agent points at the *internal* `webapp` service
  (`--server-host=http://webapp --api-server=http://webapp`), which bypasses the
  nginx basic-auth that guards the browser UI. Heartbeat/command endpoints don't
  validate the bearer token locally, but the upload/health-check path *does* — so
  `e2e-up` mints a real token via `GET /api/api_key` and injects it as
  `GPROFILER_TOKEN`.
- **Isolation.** The agent shares only the **sample app's** PID namespace
  (`pid: "service:e2e-sample-app"`), so it profiles that workload and never the
  host. `perf` is disabled (`--perf-mode=none`); py-spy profiles the Python
  workload.
- **Determinism.** `e2e-sample-app` runs a fixed hot loop, so flamegraphs have
  stable, recognizable frames.

## Agent image

There are two ways to get the agent image; pick per your goal.

### A) From source, fast (verify your agent changes) — recommended

Uses the agent repo's **`--fast`** executable build, which skips `staticx`
(the slow bundling step) and finishes in ~1-2 min on a warm Docker cache:

```bash
make -f Makefile.e2e e2e-up-src        # builds exe (--fast) + wraps + brings up
# or just rebuild the image:
make -f Makefile.e2e e2e-agent-build
```

Because `--fast` skips staticx, the resulting `build/<arch>/gprofiler` is
**glibc-dynamic** and will *not* run on the alpine base in the repo's
`container.Dockerfile` (you'd get `exec /gprofiler: No such file or directory`).
The harness therefore wraps it in a glibc base — see `e2e/agent-glibc.Dockerfile`.
Override arch/repo with `AGENT_ARCH=` / `AGENT_REPO=` if needed.

### B) Prebuilt image (fastest, no build)

`make -f Makefile.e2e e2e-up` uses `GPROFILER_IMAGE` (default
`intel/gprofiler:latest`, the upstream public image) with the vanilla
`/gprofiler` entrypoint. Good for exercising the studio side when you don't need
agent-source changes. Override it to test a specific build:

```bash
GPROFILER_IMAGE=intel/gprofiler:1.53.1 make -f Makefile.e2e e2e-up
```

Agent-side pure-logic changes (heartbeat inventory, command queue) are also
covered by the fast unit suite in `gprofiler/tests_fast/`.

## Portability (will it work on any engineer's box?)

Yes for **Linux/Ubuntu** with Docker + Compose v2 — that's the primary target and
where it's verified. Keep these in mind:

- **Agent image.** `e2e-up` defaults to the upstream public `intel/gprofiler:latest`.
  To verify *your own* agent changes, use `make -f Makefile.e2e e2e-up-src`, which
  builds the agent from the sibling `../../gprofiler` checkout. Studio images all
  build from source here, so the studio side is portable as-is.
- **CPU architecture.** The source build defaults to `x86_64`
  (`build_x86_64_executable.sh`). On arm64 (Apple Silicon, Graviton) use
  `AGENT_ARCH=aarch64 make -f Makefile.e2e e2e-up-src` (drives
  `build_aarch64_executable.sh`); the glibc wrapper honors `AGENT_ARCH`.
- **Linux vs macOS/Windows.** On Linux the agent's `privileged: true` + shared
  PID namespace + py-spy `ptrace` work natively. On Docker Desktop
  (macOS/Windows) everything runs inside its Linux VM and works, but profiling
  fidelity depends on the VM; `--perf-mode=none` (already set) avoids kernel-perf
  issues. `pid: "service:..."` is a Compose feature and is portable.
- **Host ports.** Only nginx publishes ports (`4433`, `8443`). If those clash,
  remap them in `docker-compose.yml`. The DB ports are intentionally not
  published (tests run in-network), so there's nothing else to collide.
- **Compose network name.** Helper targets use `$(NETWORK)`, derived from the
  project dir (`deploy_default`). If you set `COMPOSE_PROJECT_NAME`, pass
  `NETWORK=<name>_default`.
- **Prerequisites** above (TLS + `.htpasswd`) are one-time and cross-platform
  (need `openssl` + `htpasswd`; `htpasswd` ships with `apache2-utils` on Ubuntu).

## Test layers

1. **Fast unit/spec** (no stack) — PR gate: `gprofiler/tests_fast/` and
   `gprofiler-performance-studio/src/tests/spec/` + the frontend `node --test`.
2. **API acceptance** (this harness) — `src/tests/e2e/`, run via
   `make -f Makefile.e2e e2e-test`. 15 in-network pytest cases covering the
   spec's **AT-S1 .. AT-S15** against the live stack: inventory/tab-counts (S1–S4),
   host/service/process resolution + command creation (S5–S7), empty-resolution
   422 (S8), PMU rejection (S9), continuous subscription auto-enrollment (S10–S13),
   and legacy/partial-inventory compatibility (S14–S15).
3. **Full-pipeline smoke** — the real agent path above proves S3/SQS/indexer/
   ClickHouse/flamegraph (AT-A1–A8). Verified with the source-built agent.
4. **Playwright UI e2e** — `src/tests/playwright/`, run via
   `make -f Makefile.e2e e2e-ui-test`. Drives the console through the internal
   nginx (basic auth + self-signed TLS) using the official Playwright browser
   image, asserting the start/stop confirmation flow (dry-run validation +
   submit). The STOP case is the direct UI regression test for the
   host-level-stop bug.

Run everything against a running stack:

```bash
make -f Makefile.e2e e2e-up        # or e2e-up-src for the source-built agent
make -f Makefile.e2e e2e-test      # API acceptance (AT-S1..S15)
make -f Makefile.e2e e2e-ui-test   # Playwright UI acceptance
```

## Optional: container/pod inventory

The agent logs `No container runtime found for heartbeat workload inventory`
because the Docker socket isn't mounted (keeps it isolated from the host). To
exercise namespace/pod/container scope tabs with a real agent, mount
`/var/run/docker.sock:/var/run/docker.sock:ro` into `gprofiler-agent` — note this
gives the agent visibility into all host containers.

## Notes

- `e2e-down` removes only the harness containers (agent, sample app, test/UI
  runners); use `e2e-down-all` to tear down the entire studio stack (destructive).
- The compose project name defaults to the directory (`deploy`), so the network
  is `deploy_default`. Helper targets read `$(NETWORK)`; override with
  `NETWORK=<project>_default` if you set `COMPOSE_PROJECT_NAME`.
