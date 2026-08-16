#!/usr/bin/env bash
# Submit an adhoc profile_request with enable_nsys against Performance Studio,
# intended for a HOST gProfiler agent heartbeating into the Kind control plane.
#
# Usage (from deploy/k8s-sandbox):
#   ./gpu/run_host_agent_adhoc.sh
#
# Env overrides:
#   STUDIO_URL          default https://localhost:30443
#   SERVICE             default k8s-sandbox
#   DURATION            default 30
#   BASIC_AUTH          default admin:admin  (nginx edge)
#   HOSTNAME_OVERRIDE   skip discovery; target this hostname
#   NS                  kubectl namespace (default perf-studio)
#   SKIP_REQUEST=1      only print host-agent instructions

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SANDBOX_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
NS="${NS:-perf-studio}"
STUDIO_URL="${STUDIO_URL:-https://localhost:30443}"
SERVICE="${SERVICE:-k8s-sandbox}"
DURATION="${DURATION:-30}"
BASIC_AUTH="${BASIC_AUTH:-admin:admin}"
CURL_OPTS=(-sk -u "${BASIC_AUTH}" -H "Content-Type: application/json")

echo "== GPU (nsys) host-agent adhoc helper =="
echo "Studio URL : ${STUDIO_URL}"
echo "Service    : ${SERVICE}"
echo "UI         : ${STUDIO_URL}  (basic auth from deploy/.htpasswd)"
echo

# --- mint / read agent token (same secret as Makefile k8s-token) -------------
token=""
if kubectl -n "${NS}" get secret gprofiler-agent-token >/dev/null 2>&1; then
  token="$(kubectl -n "${NS}" get secret gprofiler-agent-token -o jsonpath='{.data.token}' | base64 -d)"
fi
if [[ -z "${token}" || "${token}" == "bootstrap" ]]; then
  echo ">> minting profiler token (secret missing or still bootstrap)..."
  token="$(kubectl -n "${NS}" run "tokfetch-$RANDOM" --image=curlimages/curl:8.7.1 \
    --restart=Never -i --rm --quiet --command -- \
    curl -s http://webapp/api/api_key \
    | python3 -c "import sys,json;print(json.load(sys.stdin)['apiKey'])")"
  kubectl -n "${NS}" create secret generic gprofiler-agent-token \
    --from-literal=token="${token}" --dry-run=client -o yaml | kubectl apply -f -
fi
echo ">> agent token (first 8): ${token:0:8}..."

# --- host agent instructions -------------------------------------------------
# Skipped when the caller already manages an agent (gpu-demo / run_gpu_agent.sh),
# where printing a manual command line would just be misleading.
if [[ -z "${HOSTNAME_OVERRIDE:-}" ]]; then
cat <<EOF

>> Start a HOST agent (not the Kind DaemonSet) so nsys can see the GPU:

  # Point at Kind's nginx NodePort; use the minted token above.
  # Adjust the gprofiler binary path for your checkout.
  sudo ./gprofiler \\
    --server-host=${STUDIO_URL} \\
    --api-server=${STUDIO_URL} \\
    --token=${token} \\
    --service-name=${SERVICE} \\
    --upload-results \\
    --enable-heartbeat-server \\
    --heartbeat-interval=10 \\
    --output-dir=/tmp/gprofiler_output \\
    --dont-send-logs

  # Optional: generate CUDA load while profiling
  make -C ${SCRIPT_DIR} && ${SCRIPT_DIR}/cuda_burn ${DURATION}

EOF
fi

if [[ "${SKIP_REQUEST:-0}" == "1" ]]; then
  echo ">> SKIP_REQUEST=1: not submitting profile_request."
  exit 0
fi

# --- resolve target hostname -------------------------------------------------
host="${HOSTNAME_OVERRIDE:-}"
if [[ -z "${host}" ]]; then
  echo ">> discovering host via Studio host_status..."
  hosts_json="$(curl "${CURL_OPTS[@]}" \
    "${STUDIO_URL}/api/metrics/profiling/host_status?service_name=${SERVICE}" || true)"
  host="$(python3 -c "
import json,sys
raw=sys.stdin.read().strip()
if not raw:
    sys.exit(0)
try:
    data=json.loads(raw)
except Exception:
    sys.exit(0)
hosts=data.get('hosts') or []
# Prefer a recently heartbeating host; fall back to first.
print(hosts[0]['hostname'] if hosts else '')
" <<<"${hosts_json}")"
fi

if [[ -z "${host}" ]]; then
  cat <<EOF
!! No host found for service=${SERVICE}.
   Start the host agent (command printed above), wait for a heartbeat, then re-run:
     ${SCRIPT_DIR}/run_host_agent_adhoc.sh
   Or set HOSTNAME_OVERRIDE=\$(hostname).
EOF
  exit 1
fi

echo ">> submitting adhoc profile_request (enable_nsys) for host=${host} duration=${DURATION}s"

CUDA_BURN="${SCRIPT_DIR}/cuda_burn"
if [[ ! -x "${CUDA_BURN}" ]]; then
  echo ">> building cuda_burn..."
  make -C "${SCRIPT_DIR}"
fi
# The path is resolved by the agent, not here: run_gpu_agent.sh mounts this
# directory at /gpu inside the agent container, so override accordingly.
export NSYS_WORKLOAD="${NSYS_WORKLOAD:-${CUDA_BURN} ${DURATION}}"
echo ">> nsys workload: ${NSYS_WORKLOAD}"
export _GPU_SERVICE="${SERVICE}"
export _GPU_HOST="${host}"
export _GPU_DURATION="${DURATION}"
# NSYS_TIMELINE=1 -> upload the CPU/GPU timeline view instead of the flamegraph
export _GPU_TIMELINE="${NSYS_TIMELINE:-0}"
[[ "${_GPU_TIMELINE}" == "1" ]] && echo ">> nsys timeline view: enabled"
# NSYS_TIMELINE_STACKS=1 -> also record CPU backtraces per kernel launch
# (--cudabacktrace; heavier) for click-for-stack in the timeline
export _GPU_TIMELINE_STACKS="${NSYS_TIMELINE_STACKS:-0}"
[[ "${_GPU_TIMELINE_STACKS}" == "1" ]] && echo ">> nsys timeline stacks: enabled"

req="$(python3 <<'PY'
import json, os
print(json.dumps({
  "service_name": os.environ["_GPU_SERVICE"],
  "request_type": "start",
  "continuous": False,
  "duration": int(os.environ["_GPU_DURATION"]),
  "frequency": 11,
  "profiling_mode": "cpu",
  "target_scope": "host",
  "target_hosts": {os.environ["_GPU_HOST"]: []},
  "target_entities": [{
    "service_name": os.environ["_GPU_SERVICE"],
    "hostname": os.environ["_GPU_HOST"],
  }],
  "additional_args": {
    "enable_nsys": True,
    "nsys_timeline": os.environ.get("_GPU_TIMELINE") == "1",
    "nsys_timeline_stacks": os.environ.get("_GPU_TIMELINE_STACKS") == "1",
    "nsys_workload": os.environ["NSYS_WORKLOAD"],
  },
}))
PY
)"

resp="$(curl "${CURL_OPTS[@]}" -X POST \
  "${STUDIO_URL}/api/metrics/profile_request" \
  -d "${req}")"
echo "${resp}" | python3 -m json.tool 2>/dev/null || echo "${resp}"

cat <<EOF

>> Request submitted. While the host agent runs the session:
   - keep cuda_burn (or your GPU workload) busy
   - wait ~ duration + upload lag
   - open Adhoc Profiling in the UI, or:
       make -f ${SANDBOX_DIR}/Makefile.k8s k8s-flamegraph
EOF
