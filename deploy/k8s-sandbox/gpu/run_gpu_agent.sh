#!/usr/bin/env bash
# Run a GPU-capable gProfiler agent as a container alongside the Kind sandbox.
#
# Why a container and not a bare host process: gProfiler takes a system-wide
# mutex bound in the init network namespace, so a bare host agent collides with
# any other agent already running on the box (including the Kind DaemonSet and
# any pre-existing fleet agent). Giving the agent its own network namespace
# sidesteps the mutex while --gpus all still exposes the GPU for nsys.
#
# Usage (from deploy/k8s-sandbox):
#   ./gpu/run_gpu_agent.sh            # start agent + port-forward
#   ./gpu/run_gpu_agent.sh stop       # tear both down
#
# Env overrides:
#   NS            kubectl namespace          (default perf-studio)
#   SERVICE       Studio service name        (default k8s-sandbox)
#   AGENT_IMAGE   GPU agent image            (default gprofiler-e2e-agent:gpu)
#   AGENT_HOST    container hostname         (default gpu-nsys-host)
#   PF_PORT       host port for webapp       (default 8888)
#   NSYS_ROOT     host Nsight Systems root   (default /opt/nvidia)

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NS="${NS:-perf-studio}"
SERVICE="${SERVICE:-k8s-sandbox}"
AGENT_IMAGE="${AGENT_IMAGE:-gprofiler-e2e-agent:gpu}"
AGENT_HOST="${AGENT_HOST:-gpu-nsys-host}"
CONTAINER="${CONTAINER:-gprofiler-gpu-agent}"
PF_PORT="${PF_PORT:-8888}"
NSYS_ROOT="${NSYS_ROOT:-/opt/nvidia}"

stop_all() {
  docker rm -f "${CONTAINER}" >/dev/null 2>&1 && echo ">> removed container ${CONTAINER}"
  # bracket avoids pkill matching this script's own command line
  pkill -f "port-forward svc/[w]ebapp ${PF_PORT}" 2>/dev/null && echo ">> stopped port-forward"
  return 0
}

if [[ "${1:-}" == "stop" ]]; then
  stop_all
  exit 0
fi

command -v nvidia-smi >/dev/null || { echo "nvidia-smi not found; this host has no NVIDIA GPU"; exit 1; }
[[ -d "${NSYS_ROOT}/nsight-systems" ]] || { echo "Nsight Systems not found under ${NSYS_ROOT}"; exit 1; }
docker image inspect "${AGENT_IMAGE}" >/dev/null 2>&1 || {
  echo "Missing image ${AGENT_IMAGE}. Build it with: make -f Makefile.k8s gpu-agent-image"; exit 1; }
[[ -x "${SCRIPT_DIR}/cuda_burn" ]] || make -C "${SCRIPT_DIR}" >/dev/null

token="$(kubectl -n "${NS}" get secret gprofiler-agent-token -o jsonpath='{.data.token}' | base64 -d)"
[[ -n "${token}" ]] || { echo "no agent token in secret gprofiler-agent-token"; exit 1; }

stop_all
sleep 1

# The agent lives in its own netns, so reach Studio via the host gateway rather
# than localhost. Bind the forward on all addresses for that to resolve.
nohup kubectl -n "${NS}" port-forward --address 0.0.0.0 svc/webapp "${PF_PORT}:80" \
  > /tmp/pf_webapp_gpu.log 2>&1 &
sleep 4
curl -s -o /dev/null -w ">> webapp via port-forward: %{http_code}\n" "http://localhost:${PF_PORT}/"

docker run -d --name "${CONTAINER}" \
  --gpus all \
  --privileged \
  --hostname "${AGENT_HOST}" \
  --add-host=host.docker.internal:host-gateway \
  -v "${NSYS_ROOT}:${NSYS_ROOT}:ro" \
  -v "${SCRIPT_DIR}:/gpu:ro" \
  "${AGENT_IMAGE}" \
  --server-host="http://host.docker.internal:${PF_PORT}" \
  --api-server="http://host.docker.internal:${PF_PORT}" \
  --token="${token}" \
  --service-name="${SERVICE}" \
  --upload-results \
  --enable-heartbeat-server \
  --heartbeat-interval=10 \
  --perf-mode=none \
  --output-dir=/tmp/gprofiler_output \
  --dont-send-logs \
  --disable-pidns-check >/dev/null

echo ">> started ${CONTAINER} (hostname=${AGENT_HOST}, service=${SERVICE})"
echo ">> waiting for heartbeat to register..."

for _ in $(seq 1 24); do
  if docker logs "${CONTAINER}" 2>&1 | grep -qiE 'heartbeat|Running gProfiler'; then
    break
  fi
  sleep 5
done

docker logs "${CONTAINER}" 2>&1 | tail -8
echo
echo ">> next: ./gpu/run_host_agent_adhoc.sh   (HOSTNAME_OVERRIDE=${AGENT_HOST})"
