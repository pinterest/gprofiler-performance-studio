# Build the tiny CUDA burn binary used by gpu-smoke / gpu-demo.
NVCC ?= nvcc
CXXFLAGS ?= -O2
# Static cudart keeps the binary runnable inside the agent container, where the
# NVIDIA runtime injects only the driver (libcuda.so.1), not the CUDA toolkit.
NVCCFLAGS ?= -cudart static
TARGET := cuda_burn

.PHONY: all clean

all: $(TARGET)

$(TARGET): cuda_burn.cu
	$(NVCC) $(CXXFLAGS) $(NVCCFLAGS) -o $@ $<

clean:
	rm -f $(TARGET) *.nsys-rep *.sqlite *.csv
