# GPU Deployment Summary

## What GitHub Actions Does Automatically ✅

After the updates, GitHub Actions will:

1. **Build Docker images with CUDA support** (updated workflow)
   - Images include CUDA-enabled PyTorch
   - Works on both GPU and CPU systems (falls back to CPU if GPU unavailable)
   - Applied to: Production, Staging, and Preview deployments

2. **Deploy images to VPS**
   - Images are built and deployed automatically
   - No manual intervention needed for deployment

## What You Must Do Manually (One-Time Setup) 🔧

### On Your VPS (if you have a GPU):

1. **Install NVIDIA Drivers** (one-time):
   ```bash
   ssh user@your-vps-ip
   sudo apt-get update
   sudo apt-get install -y nvidia-driver-535  # or latest version
   sudo reboot
   ```

2. **Install NVIDIA Container Toolkit** (one-time):
   ```bash
   distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
   curl -s -L https://nvidia.github.io/nvidia-docker/gpgkey | sudo apt-key add -
   curl -s -L https://nvidia.github.io/nvidia-docker/$distribution/nvidia-docker.list | sudo tee /etc/apt/sources.list.d/nvidia-docker.list
   sudo apt-get update
   sudo apt-get install -y nvidia-container-toolkit
   sudo systemctl restart docker
   ```

3. **Enable GPU in Docker Compose** (after deployment):
   
   Option A: Use GPU compose file:
   ```bash
   cd /opt/maigie/production
   docker-compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
   ```
   
   Option B: Edit docker-compose.yml and add GPU section (see GPU_SETUP.md)

## Current Status

### ✅ Automated (GitHub Actions):
- Builds Docker images with CUDA-enabled PyTorch
- Deploys to VPS automatically
- Images can use GPU if available

### ⚠️ Manual (VPS Setup):
- Install NVIDIA drivers (if GPU available)
- Install nvidia-container-toolkit (if GPU available)
- Configure docker-compose for GPU access (if GPU available)

### 🎯 Result:
- **With GPU**: Service automatically detects and uses GPU (10-100× faster)
- **Without GPU**: Service falls back to CPU (still works, just slower)
- **No GPU on VPS**: Everything works on CPU, no action needed

## Quick Decision Tree

```
Do you have a GPU on your VPS?
├─ Don't know? → Run: lspci | grep -i nvidia (see CHECK_GPU.md)
├─ YES → Follow GPU_SETUP.md steps 1-2 (one-time), then enable GPU in docker-compose
└─ NO  → Do nothing! Everything works on CPU automatically
```

## Verification

After deployment, check logs:

```bash
docker logs maigie-backend | grep -i "device\|gpu\|cuda"
```

You should see:
- **With GPU**: `GPU detected: Using CUDA device (GPU: NVIDIA GeForce RTX 3090, Memory: 24.0 GB)`
- **Without GPU**: `No GPU detected: Using CPU device`
