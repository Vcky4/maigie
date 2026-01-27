# GPU Support Setup Guide

This guide explains how to enable GPU acceleration for the Live Voice Service, which significantly improves performance for Speech-to-Text (Kyutai STT) and Text-to-Speech (Soprano TTS) operations.

## Performance Benefits

- **Kyutai STT**: ~10-50× faster on GPU vs CPU
- **Soprano TTS**: ~100× faster on GPU vs CPU (2000× real-time vs 20× real-time)
- **Lower CPU usage**: Frees up CPU cores for other operations
- **Better scalability**: Handle more concurrent conversations

## Prerequisites

1. **NVIDIA GPU** with CUDA support (Compute Capability 7.0+)
2. **NVIDIA drivers** installed on the host system
3. **NVIDIA Container Toolkit** installed on the host

## Host System Setup

### 1. Install NVIDIA Drivers

```bash
# Check if NVIDIA drivers are installed
nvidia-smi

# If not installed, install them (Ubuntu/Debian example)
sudo apt-get update
sudo apt-get install -y nvidia-driver-535  # or latest version
sudo reboot
```

### 2. Install NVIDIA Container Toolkit

```bash
# Add NVIDIA package repositories
distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
curl -s -L https://nvidia.github.io/nvidia-docker/gpgkey | sudo apt-key add -
curl -s -L https://nvidia.github.io/nvidia-docker/$distribution/nvidia-docker.list | sudo tee /etc/apt/sources.list.d/nvidia-docker.list

# Install nvidia-container-toolkit
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit

# Restart Docker daemon
sudo systemctl restart docker

# Verify installation
docker run --rm --gpus all nvidia/cuda:11.8.0-base-ubuntu22.04 nvidia-smi
```

## Docker Build Options

### Option 1: Build with CUDA Support (Recommended)

Build the Docker image with CUDA-enabled PyTorch:

```bash
cd apps/backend
docker build \
  --build-arg PYTORCH_VERSION=cuda \
  --build-arg CUDA_VERSION=cu121 \
  -t maigie-backend:gpu .
```

**Note**: CUDA-enabled PyTorch works on both GPU and CPU systems (falls back to CPU if GPU unavailable).

### Option 2: Build with CPU-only PyTorch

```bash
cd apps/backend
docker build \
  --build-arg PYTORCH_VERSION=cpu \
  -t maigie-backend:cpu .
```

## Docker Compose Setup

### Using GPU-Enabled Compose File

```bash
# Start services with GPU support
docker-compose -f docker-compose.yml -f docker-compose.gpu.yml up -d

# Or set environment variables
PYTORCH_VERSION=cuda CUDA_VERSION=cu121 docker-compose up -d
```

### Manual GPU Configuration

Edit `docker-compose.yml` and uncomment the GPU section:

```yaml
backend:
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: all
            capabilities: [gpu]
```

## Runtime GPU Detection

The service automatically detects GPU availability at runtime:

- **With GPU**: Models use CUDA, logs show `Using device: cuda`
- **Without GPU**: Models fall back to CPU, logs show `Using device: cpu`

Check logs to verify:

```bash
docker logs maigie-backend | grep "Using device"
```

## CI/CD Configuration

### GitHub Actions

The CI workflow builds with CPU-only PyTorch by default. To build with CUDA support:

1. Add build args to the Docker build step in `.github/workflows/backend-ci.yml`:

```yaml
- name: Build Docker Image
  working-directory: ./apps/backend
  run: |
    docker build \
      --build-arg PYTORCH_VERSION=cuda \
      --build-arg CUDA_VERSION=cu121 \
      -t maigie-backend:latest .
```

**Note**: GitHub Actions runners don't have GPUs, but building with CUDA PyTorch allows the image to use GPU when deployed to a GPU-enabled VPS.

### VPS Deployment

When deploying to a GPU-enabled VPS:

1. Ensure NVIDIA Container Toolkit is installed on the VPS
2. Build or pull the GPU-enabled image
3. Use `docker-compose.gpu.yml` or configure GPU access manually

## Troubleshooting

### GPU Not Detected

1. **Check NVIDIA drivers**:
   ```bash
   nvidia-smi
   ```

2. **Verify Docker GPU access**:
   ```bash
   docker run --rm --gpus all nvidia/cuda:11.8.0-base-ubuntu22.04 nvidia-smi
   ```

3. **Check container logs**:
   ```bash
   docker logs maigie-backend | grep -i "device\|cuda\|gpu"
   ```

### CUDA Version Mismatch

If you see CUDA version errors:

1. Check your GPU's CUDA capability: `nvidia-smi`
2. Match `CUDA_VERSION` build arg to your CUDA version:
   - CUDA 11.8: `cu118`
   - CUDA 12.1: `cu121` (default)
   - CUDA 12.4: `cu124`

### Out of Memory Errors

If GPU runs out of memory:

1. Limit GPU access to specific devices:
   ```yaml
   environment:
     CUDA_VISIBLE_DEVICES: "0"  # Use only GPU 0
   ```

2. Reduce batch sizes in model inference (if configurable)

## Performance Monitoring

Monitor GPU usage:

```bash
# Real-time GPU monitoring
watch -n 1 nvidia-smi

# Check GPU memory usage
nvidia-smi --query-gpu=memory.used,memory.total --format=csv
```

## Cost Considerations

- **CPU-only**: Lower image size (~500MB smaller), works everywhere
- **CUDA-enabled**: Larger image (~2GB), but works on both GPU and CPU systems
- **Recommendation**: Use CUDA-enabled builds for production (flexibility + performance)
