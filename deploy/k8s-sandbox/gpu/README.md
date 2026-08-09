# GPU (nsys) sandbox demo

Kind runs the **Performance Studio control plane**. NVIDIA **nsys** and the gProfiler agent
that can see the GPU run in a **separate GPU-capable agent container** on the host, not
inside the Kind node.

```
Kind (webapp / indexer / LocalStack / nginx)
        ▲
        │  heartbeat + profile upload + profile_request
        │  (via kubectl port-forward on the host gateway)
        │
GPU agent container (--gpus all, own netns) + nsys + cuda_burn
```

**Console:** [https://localhost:30443](https://localhost:30443) (basic auth from `deploy/.htpasswd`, typically `admin` / `admin`).

## Prerequisites (host)

- NVIDIA driver (`nvidia-smi`) and the NVIDIA container runtime
- NVIDIA Nsight Systems CLI (`nsys` on `PATH`)
- CUDA toolkit (`nvcc`) to build `cuda_burn`
- Kind sandbox up: `make -f Makefile.k8s k8s-up` (or `k8s-all`)

## Quick path

```bash
cd deploy/k8s-sandbox

make -f Makefile.k8s gpu-check        # nvidia-smi + nsys
make -f Makefile.k8s gpu-build        # compile cuda_burn
make -f Makefile.k8s gpu-smoke        # nsys profile cuda_burn + kern sum CSV head

make -f Makefile.k8s gpu-agent-image  # build the GPU-capable agent image (slow, once)
make -f Makefile.k8s gpu-demo         # start GPU agent + submit adhoc profile_request
make -f Makefile.k8s gpu-agent-down   # teardown
```

Then open Adhoc Profiling:
`https://localhost:30443/profiles?service=k8s-sandbox&time=1h&view=adhoc`

The GPU run shows a **GPU / nsys** chip and `nsys-cuda` in the PMU Events column.

## The workload

`cuda_burn.cu` launches four kernels with deliberately uneven cost so the flamegraph has
real relative weights rather than a single bar:

| Kernel | Bottleneck | Typical share |
| --- | --- | --- |
| `memory_stride` | uncoalesced global memory | ~87% |
| `transcendental_burn` | special function units | ~9% |
| `fma_burn` | FP32 FMA pipes | ~4% |
| `reduce_shared` | shared memory reduction | <1% |

It is built with `-cudart static` so it runs inside the agent container, where the NVIDIA
runtime injects the driver (`libcuda.so.1`) but not the CUDA toolkit.

## Why a separate agent container, and not the DaemonSet or a bare host process

Two constraints drive the topology, both found the hard way:

**The Kind DaemonSet cannot capture GPU.** The node container does get `/dev/nvidia*`
(Docker's default runtime here is `nvidia`), and those device nodes are even visible inside
the agent pod. But the node has no CUDA driver libraries, so neither `nsys` nor a CUDA
binary can actually run there.

**A bare host agent collides with any other agent on the box.** `grab_gprofiler_mutex()`
binds an abstract unix socket in the **init network namespace**, so exactly one privileged
gProfiler can run per host — and on a shared dev box that slot is often already taken (by
the Kind DaemonSet, or by a pre-existing fleet agent). Giving the GPU agent its own network
namespace, which is what a plain `docker run` does, sidesteps the mutex entirely while
`--gpus all` still exposes the GPU. This is also why the agent reaches Studio through
`host.docker.internal` rather than `localhost`.

The agent image uses an `ubuntu:24.04` base (`--build-arg BASE`) because host-compiled CUDA
binaries need a newer glibc than the 22.04 base used for the CPU e2e agent.

## Scripts

- `run_gpu_agent.sh` — builds nothing; starts the port-forward and the GPU agent container,
  waits for the first heartbeat. `run_gpu_agent.sh stop` tears both down.
- `run_host_agent_adhoc.sh` — POSTs `profile_request` with `continuous=false` and
  `additional_args.enable_nsys=true`, targeting a heartbeating host. Override the workload
  with `NSYS_WORKLOAD` (inside the container the directory is mounted at `/gpu`).

Design notes: [DESIGN.md](./DESIGN.md).
