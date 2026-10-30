#!/usr/bin/env bash
# One-shot: build a torch-enabled GPU agent image, start it, and submit an
# adhoc profile_request whose nsys workload is the PyTorch script. The GPU
# flamegraph (real torch/cutlass kernels) then shows in Studio's Adhoc view,
# exactly like the cuda_burn demo.
#
# Run from deploy/k8s-sandbox:  bash gpu/run_torch_demo.sh
set -euo pipefail

SANDBOX_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GPU_DIR="${SANDBOX_DIR}/gpu"
MK="${SANDBOX_DIR}/Makefile.k8s"

BASE_IMAGE="${BASE_IMAGE:-gprofiler-e2e-agent:gpu}"
TORCH_IMAGE="${TORCH_IMAGE:-gprofiler-e2e-agent:gpu-torch}"
SERVICE="${SERVICE:-k8s-sandbox}"
AGENT_HOST="${AGENT_HOST:-gpu-torch-host}"
DURATION="${DURATION:-30}"

command -v docker >/dev/null || { echo "docker not found"; exit 1; }
docker image inspect "${BASE_IMAGE}" >/dev/null 2>&1 || {
  echo "Base image ${BASE_IMAGE} missing. Build it first:"
  echo "  make -f ${MK} gpu-agent-image"; exit 1; }

echo ">> [1/3] building torch agent image ${TORCH_IMAGE} (layers python+torch; slow first time)"
docker build -f "${GPU_DIR}/agent-torch.Dockerfile" \
  --build-arg BASE_IMAGE="${BASE_IMAGE}" \
  -t "${TORCH_IMAGE}" "${GPU_DIR}"

echo ">> [2/3] starting torch agent container"
AGENT_IMAGE="${TORCH_IMAGE}" AGENT_HOST="${AGENT_HOST}" SERVICE="${SERVICE}" \
  "${GPU_DIR}/run_gpu_agent.sh"

echo ">> [3/3] submitting adhoc profile_request (nsys workload = torch_workload.py)"
HOSTNAME_OVERRIDE="${AGENT_HOST}" DURATION="${DURATION}" SERVICE="${SERVICE}" \
  NSYS_WORKLOAD="python3 /gpu/torch_workload.py ${DURATION}" \
  "${GPU_DIR}/run_host_agent_adhoc.sh"

cat <<EOF

>> Wait ~${DURATION}s + upload lag, then open Adhoc Profiling:
     https://localhost:30443/profiles?service=${SERVICE}&time=1h&view=adhoc
   Look for host=${AGENT_HOST} with the GPU / nsys chip. The flamegraph frames
   will be real torch kernels (cutlass_*gemm* dominant, plus elementwise).
>> teardown: AGENT_IMAGE=${TORCH_IMAGE} ${GPU_DIR}/run_gpu_agent.sh stop
EOF
