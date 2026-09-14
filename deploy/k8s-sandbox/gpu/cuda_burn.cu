/*
 * CUDA busy-work binary for nsys GPU profiling demos.
 * Usage: ./cuda_burn [seconds]   (default 10)
 *
 * Launches four kernels that stress different GPU units with deliberately
 * uneven cost, so cuda_gpu_kern_sum (and the flamegraph built from it) shows
 * distinct frames with meaningful relative weights instead of a single bar.
 */

#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <cuda_runtime.h>

// FMA-heavy: saturates the FP32 pipes. Intended to dominate the profile.
__global__ void fma_burn(float *data, int n, int iters) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) {
        return;
    }
    float x = data[i];
    for (int k = 0; k < iters; ++k) {
        x = fmaf(x, 1.000001f, 0.000001f);
    }
    data[i] = x;
}

// Transcendentals run on the special function units, a different bottleneck.
__global__ void transcendental_burn(float *data, int n, int iters) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) {
        return;
    }
    float x = data[i];
    for (int k = 0; k < iters; ++k) {
        x = __sinf(x) + __cosf(x) * 0.5f;
    }
    data[i] = x;
}

// Strided access defeats coalescing, so this one is bound by memory, not math.
__global__ void memory_stride(float *data, int n, int iters, int stride) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) {
        return;
    }
    float acc = 0.0f;
    for (int k = 0; k < iters; ++k) {
        int idx = (i * stride + k * 97) % n;
        acc += data[idx];
    }
    data[i] = acc * 1e-6f;
}

// Shared-memory reduction: cheapest of the four, keeps the tail of the profile honest.
__global__ void reduce_shared(const float *in, float *out, int n) {
    extern __shared__ float tile[];
    int tid = threadIdx.x;
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    tile[tid] = (i < n) ? in[i] : 0.0f;
    __syncthreads();
    for (int s = blockDim.x / 2; s > 0; s >>= 1) {
        if (tid < s) {
            tile[tid] += tile[tid + s];
        }
        __syncthreads();
    }
    if (tid == 0) {
        out[blockIdx.x] = tile[0];
    }
}

static void check(cudaError_t err, const char *what) {
    if (err != cudaSuccess) {
        std::fprintf(stderr, "CUDA error in %s: %s\n", what, cudaGetErrorString(err));
        std::exit(1);
    }
}

static double now_sec() {
    timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec * 1e-9;
}

int main(int argc, char **argv) {
    const int seconds = argc > 1 ? std::atoi(argv[1]) : 10;
    if (seconds <= 0) {
        std::fprintf(stderr, "usage: %s [seconds]\n", argv[0]);
        return 2;
    }

    const int n = 1 << 20; // 1M floats
    const int threads = 256;
    const int blocks = (n + threads - 1) / threads;

    float *d = nullptr;
    float *out = nullptr;
    check(cudaMalloc(&d, n * sizeof(float)), "cudaMalloc");
    check(cudaMalloc(&out, blocks * sizeof(float)), "cudaMalloc out");
    check(cudaMemset(d, 0, n * sizeof(float)), "cudaMemset");

    std::printf("cuda_burn: %d blocks x %d threads, running ~%ds across 4 kernels\n",
                blocks, threads, seconds);

    const double deadline = now_sec() + seconds;
    long rounds = 0;
    while (now_sec() < deadline) {
        fma_burn<<<blocks, threads>>>(d, n, 40000);
        transcendental_burn<<<blocks, threads>>>(d, n, 6000);
        memory_stride<<<blocks, threads>>>(d, n, 4000, 1031);
        reduce_shared<<<blocks, threads, threads * sizeof(float)>>>(d, out, n);
        check(cudaGetLastError(), "launch");
        check(cudaDeviceSynchronize(), "sync");
        ++rounds;
    }

    check(cudaFree(d), "cudaFree");
    check(cudaFree(out), "cudaFree out");
    std::printf("cuda_burn: done (%ld rounds)\n", rounds);
    return 0;
}
