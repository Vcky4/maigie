# Environment Variable Scripts

This directory contains scripts for managing environment variables from `.env.example` and GitHub secrets.

## Scripts

### `generate-env-from-secrets.sh`

Main script that generates `.env` file from `.env.example` by checking for corresponding GitHub secrets.

**Usage:**
```bash
./generate-env-from-secrets.sh [output_file] [template_file]
# Example:
./generate-env-from-secrets.sh .env .env.example
```

**How it works:**
1. Reads `.env.example` line by line
2. For each `KEY=VALUE` line, checks if an environment variable with that key name exists
3. If the environment variable exists (from GitHub secrets), uses that value
4. Otherwise, uses the default value from `.env.example`
5. Outputs debug information showing which secrets were used vs defaults

### `extract-env-keys.sh`

Extracts all environment variable keys from `.env.example`.

**Usage:**
```bash
./extract-env-keys.sh [.env.example path]
# Example:
./extract-env-keys.sh .env.example
```

**Output:** List of all variable names (one per line)

### `load-all-secrets.sh`

Helper script that checks which GitHub secrets are available for keys in `.env.example`.

**Usage:**
```bash
source ./load-all-secrets.sh [.env.example path]
# Example:
source ./load-all-secrets.sh .env.example
```

**Note:** This script is meant to be sourced (not executed directly) to check secret availability.

## How It Works in GitHub Actions

1. **Workflow passes secrets as environment variables:**
   ```yaml
   env:
     SENTRY_DSN: ${{ secrets.SENTRY_DSN }}
     SECRET_KEY: ${{ secrets.SECRET_KEY }}
     # ... all other possible secrets
   ```

2. **Script reads `.env.example` and checks each key:**
   - If environment variable exists → uses secret value
   - If environment variable doesn't exist → uses default from `.env.example`

3. **Result:** `.env` file with secrets where available, defaults otherwise

## Adding New Secrets

1. Add the variable to `.env.example` with a default value
2. Add the corresponding secret to GitHub repository secrets
3. Add the secret to the workflow's `env:` section (both GitHub Actions and VPS steps)
4. The script will automatically use it!

## Single Source of Truth

**`.env.example` is the single source of truth** for all environment variables:
- All variable names come from `.env.example`
- Default values come from `.env.example`
- GitHub secrets override defaults when available
- No need to maintain separate lists of variables

