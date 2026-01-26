# Soprano TTS Service

A gRPC-based Text-to-Speech (TTS) microservice using the Soprano TTS model. This service provides high-quality speech synthesis capabilities for the Maigie platform, enabling real-time voice generation for study sessions and conversations.

## Overview

The Soprano TTS Service is a standalone microservice that converts text into natural-sounding speech audio. It's designed to be deployed as a separate container, allowing for independent scaling and resource management of the TTS functionality.

## Features

- **High-Quality Speech Synthesis**: Uses the Soprano TTS model for natural-sounding voice generation
- **Streaming Audio**: Streams audio chunks in real-time for low-latency playback
- **GPU Acceleration**: Automatically detects and uses CUDA-capable GPUs when available
- **CPU Fallback**: Gracefully falls back to CPU processing when GPU is unavailable
- **Multiple Backends**: Supports both `lmdeploy` (faster) and `transformers` (compatible) backends
- **gRPC Interface**: Efficient binary protocol for inter-service communication
- **Health Checks**: Built-in health monitoring endpoint
- **Docker Ready**: Containerized for easy deployment

## Architecture

```
┌─────────────────┐
│   Backend API   │
│   (FastAPI)     │
└────────┬────────┘
         │ gRPC
         ▼
┌─────────────────┐
│ Soprano TTS     │
│   Service       │
│  (gRPC Server)  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Soprano TTS    │
│     Model       │
│  (GPU/CPU)      │
└─────────────────┘
```

## Requirements

- Python 3.11+
- Poetry (for dependency management)
- CUDA-capable GPU (optional, but recommended for performance)
- Docker (for containerized deployment)

## Installation

### Local Development

1. **Install Poetry** (if not already installed):
   ```bash
   curl -sSL https://install.python-poetry.org | python3 -
   ```

2. **Install dependencies**:
   ```bash
   cd apps/soprano-tts-service
   poetry install
   ```

3. **Generate gRPC proto files**:
   ```bash
   poetry run python -m grpc_tools.protoc \
     --proto_path=src/proto \
     --python_out=src/proto \
     --grpc_python_out=src/proto \
     src/proto/tts.proto
   ```

4. **Run the service**:
   ```bash
   poetry run python -m src.main
   ```

### Docker Deployment

Build and run using Docker:

```bash
docker build -t soprano-tts-service .
docker run -p 50051:50051 soprano-tts-service
```

For GPU support:

```bash
docker run --gpus all -p 50051:50051 soprano-tts-service
```

## Usage

### Starting the Service

The service starts a gRPC server on port `50051` by default (configurable via `PORT` environment variable).

```bash
# Using Poetry
poetry run python -m src.main

# Using Docker
docker run -p 50051:50051 soprano-tts-service
```

### Environment Variables

- `PORT`: gRPC server port (default: `50051`)

## API Reference

### gRPC Service Definition

The service implements the following gRPC methods:

#### `GenerateSpeech`

Generates speech from text and streams audio chunks.

**Request:**
```protobuf
message GenerateSpeechRequest {
  string text = 1;
  optional string voice = 2;
}
```

**Response:** Stream of `AudioChunk` messages
```protobuf
message AudioChunk {
  bytes audio_data = 1;
  bool is_final = 2;
}
```

**Example:**
```python
import grpc
from src.proto import tts_pb2, tts_pb2_grpc

async with grpc.aio.insecure_channel('localhost:50051') as channel:
    stub = tts_pb2_grpc.TTSServiceStub(channel)
    request = tts_pb2.GenerateSpeechRequest(text="Hello, world!")
    
    async for chunk in stub.GenerateSpeech(request):
        # Process audio chunk
        audio_data = chunk.audio_data
        is_final = chunk.is_final
```

#### `HealthCheck`

Checks the health status of the service.

**Request:**
```protobuf
message HealthCheckRequest {}
```

**Response:**
```protobuf
message HealthCheckResponse {
  bool healthy = 1;
  string message = 2;
}
```

## Service Details

### Device Detection

The service automatically detects available hardware:
- **GPU**: Uses CUDA if available, with detailed GPU information logging
- **CPU**: Falls back to CPU processing if GPU is unavailable

### Backend Selection

The service supports two backends:
1. **lmdeploy** (preferred): Faster inference, used when available
2. **transformers** (fallback): More compatible, used when lmdeploy is not available

### Audio Format

- **Sample Rate**: 16 kHz
- **Channels**: Mono
- **Bit Depth**: 16-bit PCM
- **Chunk Size**: 8192 bytes (~0.25 seconds of audio)

## Development

### Project Structure

```
soprano-tts-service/
├── src/
│   ├── main.py              # gRPC server entry point
│   ├── proto/
│   │   ├── tts.proto        # gRPC service definition
│   │   └── tts_pb2*.py      # Generated gRPC code
│   └── services/
│       └── tts_service.py    # TTS model wrapper
├── Dockerfile
├── pyproject.toml
└── README.md
```

### Code Quality

The project uses:
- **Black**: Code formatting
- **Ruff**: Linting
- **Poetry**: Dependency management

Run linting:
```bash
poetry run black .
poetry run ruff check .
```

### Testing

Run tests:
```bash
poetry run pytest
```

## Docker

### Building the Image

```bash
docker build -t soprano-tts-service .
```

### Running with Docker Compose

The service is typically deployed alongside the backend service. See `apps/backend/docker-compose.yml` for an example configuration.

### Image Optimization

The Dockerfile includes optimizations to reduce image size:
- Multi-stage builds
- Cache cleanup
- Removal of unnecessary files (tests, docs, cache)

## Integration with Backend

The backend service connects to this TTS service via gRPC. The connection URL is configured via the `SOPRANO_TTS_SERVICE_URL` environment variable (default: `soprano-tts-service:50051`).

See `apps/backend/src/services/tts_client.py` for the client implementation.

## Performance Considerations

- **GPU**: Significantly faster inference (recommended for production)
- **CPU**: Slower but more compatible (suitable for development/testing)
- **Streaming**: Audio chunks are streamed to reduce latency
- **Model Loading**: Model is loaded lazily on first request

## Troubleshooting

### Model Not Loading

- Ensure `soprano-tts` package is installed
- Check GPU availability if using CUDA
- Review logs for initialization errors

### Audio Quality Issues

- Verify audio format conversion (16-bit PCM, 16kHz, mono)
- Check model initialization logs
- Ensure sufficient GPU memory if using CUDA

### Connection Issues

- Verify gRPC server is running on correct port
- Check network connectivity between services
- Review firewall rules

## License

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details. 
