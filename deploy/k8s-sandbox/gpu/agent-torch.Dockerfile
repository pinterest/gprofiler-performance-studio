# GPU agent image + PyTorch, so nsys can profile a real torch workload inside
# the container and the flamegraph flows through the normal agent->Studio->UI
# adhoc pipeline (same path cuda_burn uses).
#
# Build FROM the already-built GPU agent image so we just layer python+torch on
# top (the base already has the gprofiler exe as ENTRYPOINT and a glibc >= 24.04).
#
#   docker build -f gpu/agent-torch.Dockerfile -t gprofiler-e2e-agent:gpu-torch .
ARG BASE_IMAGE=gprofiler-e2e-agent:gpu
FROM ${BASE_IMAGE}

USER root
# python3 + pip, then CPU-free torch wheel (bundles its own CUDA runtime; the
# host driver is injected by --gpus all, so no CUDA toolkit needed here).
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m pip install --no-cache-dir --break-system-packages torch \
    || python3 -m pip install --no-cache-dir torch

# The gprofiler exe entrypoint is inherited from the base image.
