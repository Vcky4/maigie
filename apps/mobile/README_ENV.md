# Environment Configuration

This document explains how to configure environment variables for the mobile app.

## API Base URL Configuration

The API base URL can be configured in several ways:

### Option 1: Environment Variables (Recommended)

Create a `.env` file in the `apps/mobile` directory:

```bash
# Development
EXPO_PUBLIC_API_BASE_URL=http://localhost:8000

# Production
# EXPO_PUBLIC_API_BASE_URL=https://api.maigie.com

# Staging
# EXPO_PUBLIC_API_BASE_URL=https://staging-api.maigie.com
```

### Option 2: app.config.js

The `app.config.js` file automatically reads from environment variables and provides fallbacks:

- `EXPO_PUBLIC_API_BASE_URL` - Takes highest priority
- `API_BASE_URL` - Fallback if EXPO_PUBLIC_API_BASE_URL is not set
- Default values based on environment:
  - Development: `http://localhost:8000` (iOS) or `http://10.0.2.2:8000` (Android)
  - Production: `https://api.maigie.com`

### Option 3: Platform-specific Defaults

If no environment variable is set, the app uses platform-specific defaults:
- **Android**: `http://10.0.2.2:8000` (Android emulator)
- **iOS**: `http://localhost:8000`
- **Production**: `https://api.maigie.com`

## Usage

The API base URL is automatically used by the `apiRequest` utility function in `src/utils/api.ts`. You don't need to manually configure it in your API calls.

## Endpoints

All API endpoints are centralized in `lib/endpoints.ts` at the root of the project. Import and use them like this:

```typescript
import { endpoints } from '@maigie/endpoints';

// Use in API calls
await api.post(endpoints.auth.login, { email, password });
```

## Environment Files

- `.env` - Local development (not committed to git)
- `.env.example` - Example file showing available variables
- `app.config.js` - Expo configuration with environment variable support

