# How to Check if Your VPS Has a GPU

## Quick Check Methods

### Method 1: Check for NVIDIA GPU (Most Common)

SSH into your VPS and run:

```bash
# Check if NVIDIA GPU is present
lspci | grep -i nvidia
```

**Results:**
- **Has GPU**: You'll see output like `NVIDIA Corporation GP104 [GeForce GTX 1080]` or similar
- **No GPU**: No output or only unrelated devices

### Method 2: Check NVIDIA Drivers (If Installed)

```bash
# Check if NVIDIA drivers are installed
nvidia-smi
```

**Results:**
- **Has GPU + Drivers**: Shows GPU information, memory, processes, etc.
- **Has GPU, No Drivers**: Error like `NVIDIA-SMI has failed because it couldn't communicate with the NVIDIA driver`
- **No GPU**: Error like `No devices were found`

### Method 3: Check All Graphics Devices

```bash
# List all PCI devices (including GPUs)
lspci | grep -i vga
# or
lspci | grep -i display
```

**Results:**
- Shows all graphics/display adapters (including integrated graphics)
- Look for NVIDIA, AMD, or Intel graphics

### Method 4: Check System Hardware Info

```bash
# Detailed hardware information
sudo lshw -C display
```

**Results:**
- Shows detailed information about display adapters
- Requires sudo privileges

### Method 5: Check in /proc (Linux)

```bash
# Check for NVIDIA devices in system
ls -la /proc/driver/nvidia/ 2>/dev/null && echo "NVIDIA driver loaded" || echo "No NVIDIA driver"
```

## Interpreting Results

### ✅ You Have a GPU If:
- `lspci | grep -i nvidia` shows NVIDIA devices
- `nvidia-smi` works and shows GPU info
- `/proc/driver/nvidia/` directory exists

### ❌ You Don't Have a GPU If:
- `lspci | grep -i nvidia` shows nothing
- `nvidia-smi` says "No devices were found"
- No NVIDIA devices in hardware listings

### ⚠️ You Might Have a GPU But:
- Drivers aren't installed → `nvidia-smi` fails but `lspci` shows NVIDIA device
- GPU is disabled → Check BIOS/UEFI settings
- Virtualized GPU → Some cloud providers offer GPU instances (check your VPS plan)

## Cloud VPS Providers

### Common GPU VPS Providers:
- **AWS**: EC2 instances with GPU (g4dn, p3, etc.)
- **Google Cloud**: Compute Engine with GPU
- **Azure**: VM instances with GPU
- **DigitalOcean**: GPU Droplets (limited availability)
- **Linode**: GPU instances
- **Contabo**: Some plans offer GPU (check your plan)

### Check Your VPS Plan:
1. Log into your VPS provider's dashboard
2. Check your instance/server specifications
3. Look for "GPU", "CUDA", or "NVIDIA" in the plan details

## Quick One-Liner Check

Run this single command to check everything:

```bash
echo "=== Checking for GPU ===" && \
echo "1. PCI Devices:" && lspci | grep -i nvidia && \
echo "2. NVIDIA Driver:" && (nvidia-smi 2>/dev/null || echo "  No NVIDIA driver installed") && \
echo "3. Driver Directory:" && (ls /proc/driver/nvidia/ 2>/dev/null && echo "  NVIDIA driver loaded" || echo "  No NVIDIA driver")
```

## What to Do Next

### If You Have a GPU:
1. Follow `GPU_SETUP.md` to install drivers and toolkit
2. Enable GPU in docker-compose
3. Enjoy 10-100× faster performance! 🚀

### If You Don't Have a GPU:
1. **No action needed!** The service works perfectly on CPU
2. Everything will run automatically on CPU
3. Consider upgrading your VPS plan if you need GPU performance

## Example Outputs

### ✅ GPU Detected:
```bash
$ lspci | grep -i nvidia
01:00.0 VGA compatible controller: NVIDIA Corporation GP104 [GeForce GTX 1080] (rev a1)

$ nvidia-smi
+-----------------------------------------------------------------------------+
| NVIDIA-SMI 535.54.03    Driver Version: 535.54.03    CUDA Version: 12.2  |
+-------------------------------+----------------------+----------------------+
| GPU  Name        Persistence-M| Bus-Id        Disp.A | Volatile Uncorr. ECC |
| Fan  Temp  Perf  Pwr:Usage/Cap|         Memory-Usage | GPU-Util  Compute M. |
+===============================+======================+======================+
|   0  NVIDIA GeForce ...  Off  | 00000000:01:00.0 Off |                  N/A |
|  0%   35C    P8    10W / 180W |      0MiB /  8192MiB |      0%      Default |
+-------------------------------+----------------------+----------------------+
```

### ❌ No GPU:
```bash
$ lspci | grep -i nvidia
(no output)

$ nvidia-smi
NVIDIA-SMI has failed because it couldn't communicate with the NVIDIA driver.
Make sure that the latest NVIDIA driver is installed and running.
```
