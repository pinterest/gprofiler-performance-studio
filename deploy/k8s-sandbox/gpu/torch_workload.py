#!/usr/bin/env python3
"""PyTorch GPU workload for the nsys adhoc demo. Mounted into the GPU agent
container at /gpu/torch_workload.py and run under nsys as the profile workload.

No torch.profiler here on purpose: nsys owns CUPTI during the capture.
Runs relu(x @ W + b) in a loop so the flamegraph shows real transformer-style
kernels (a GEMM on Tensor Cores + elementwise add/relu).

Usage: python torch_workload.py [seconds]
"""
import os
import sys
import time

# The gProfiler agent is a PyInstaller bundle that prepends its own lib dir
# (/tmp/_MEIxxxx) to LD_LIBRARY_PATH; inherited by this child it shadows the
# system libstdc++ and torch fails to import (CXXABI_1.3.8 not found). Scrub any
# _MEI bundle path from LD_LIBRARY_PATH and re-exec once with a clean env before
# importing torch. Guarded by a sentinel so we re-exec at most once.
if os.environ.get("_TORCH_WORKLOAD_CLEANED") != "1":
    ld = os.environ.get("LD_LIBRARY_PATH", "")
    cleaned = os.pathsep.join(p for p in ld.split(os.pathsep) if p and "/_MEI" not in p)
    new_env = dict(os.environ)
    new_env["_TORCH_WORKLOAD_CLEANED"] = "1"
    if cleaned:
        new_env["LD_LIBRARY_PATH"] = cleaned
    else:
        new_env.pop("LD_LIBRARY_PATH", None)
    new_env.pop("LD_PRELOAD", None)
    os.execve(sys.executable, [sys.executable] + sys.argv, new_env)

try:
    import torch
except ModuleNotFoundError:
    sys.exit("torch not installed in this image")

if not torch.cuda.is_available():
    sys.exit("no CUDA GPU visible to torch (run the container with --gpus all)")

dev = torch.device("cuda")
seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 30

x = torch.randn(512, 4096, device=dev, dtype=torch.float16)
W = torch.randn(4096, 4096, device=dev, dtype=torch.float16)
b = torch.randn(4096, device=dev, dtype=torch.float16)

for _ in range(10):                    # warm up cuBLAS/cutlass kernel selection
    torch.relu(x @ W + b)
torch.cuda.synchronize()

print(f"torch_workload: {torch.cuda.get_device_name(0)}, running ~{seconds}s")
deadline = time.monotonic() + seconds
rounds = 0
while time.monotonic() < deadline:
    torch.relu(x @ W + b)              # GEMM + bias-add + relu per round
    rounds += 1
torch.cuda.synchronize()
print(f"torch_workload: done ({rounds} rounds)")
