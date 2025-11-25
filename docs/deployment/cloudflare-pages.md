# Cloudflare Pages Deployment Guide

This guide explains how to deploy the Maigie web application to Cloudflare Pages.

## Prerequisites

- A Cloudflare account
- GitHub repository access
- Node.js 20.x (see `.nvmrc`)

## Deployment Methods

### Method 1: GitHub Actions (Recommended)

We use GitHub Actions to automatically deploy to Cloudflare Pages on push to `main` and `development` branches, and on pull requests.

#### Project Structure

The deployment uses **separate Cloudflare Pages projects** for different branches:
- **`maigie-web-prod`**: Production project for `main` branch
- **`maigie-web-dev`**: Development project for `development` branch

Each project has its own:
- Production URL
- Environment variables
- Custom domain settings
- Deployment history

#### Setup Steps:

1. **Get Cloudflare API Token:**
   - Go to [Cloudflare Dashboard](https://dash.cloudflare.com/profile/api-tokens)
   - Click "Create Token"
   - Use "Edit Cloudflare Workers" template or create custom token with:
     - Account: `Cloudflare Pages:Edit`
     - Zone: `Zone:Read` (if using custom domain)
   - Copy the token

2. **Get Cloudflare Account ID:**
   - Go to [Cloudflare Dashboard](https://dash.cloudflare.com/)
   - Select your account
   - Copy the Account ID from the right sidebar

3. **Add GitHub Secrets:**
   - Go to your GitHub repository → Settings → Secrets and variables → Actions
   - Add the following secrets:
     - `CLOUDFLARE_API_TOKEN`: Your API token from step 1
     - `CLOUDFLARE_ACCOUNT_ID`: Your Account ID from step 2
   - Note: `GITHUB_TOKEN` is automatically provided by GitHub Actions

4. **Create Cloudflare Pages Projects:**
   - The workflow will automatically create projects on first deployment
   - Or manually create them in Cloudflare Dashboard:
     - `maigie-web-prod` for production
     - `maigie-web-dev` for development

5. **Configure Workflow:**
   - The workflow file is located at `.github/workflows/cloudflare-pages.yml`
   - Project names are automatically determined based on branch
   - Push to `main` or `development` to trigger deployment

### Method 2: Cloudflare Dashboard (Manual Setup)

If you prefer to set up projects manually instead of using GitHub Actions:

1. **Create Cloudflare Pages Projects:**
   - Go to [Cloudflare Dashboard](https://dash.cloudflare.com/) → Pages
   - Create two projects:
     - `maigie-web-prod` (for main branch)
     - `maigie-web-dev` (for development branch)
   - Connect your GitHub repository to each project

2. **Configure Build Settings (for each project):**
   - **Framework preset**: None (or Vite if available)
   - **Build command**: `npm install && nx build web`
   - **Build output directory**: `dist/apps/web`
   - **Root directory**: (leave empty - uses workspace root)
   - **Node version**: `20`
   - **Production branch**: 
     - `maigie-web-prod`: Set to `main`
     - `maigie-web-dev`: Set to `development`

3. **Environment Variables:**
   - Add environment variables separately for each project
   - Production variables in `maigie-web-prod`
   - Development variables in `maigie-web-dev`
   - These will be available during build and runtime

4. **Custom Domain (Optional):**
   - Configure custom domains separately for each project
   - Go to each Pages project → Custom domains
   - Add your domain and follow DNS setup instructions
   - Example: `app.maigie.com` for prod, `dev.maigie.com` for dev

## Build Configuration

### Build Command
```bash
npm install && nx build web
```

### Output Directory
```
dist/apps/web
```

### Why Nx?
This is an Nx monorepo, so we use `nx build web` instead of direct Vite commands. Nx handles:
- Dependency graph resolution
- Build caching
- Workspace dependency management

## SPA Routing

The app uses React Router for client-side routing. The `_redirects` file in `apps/web/public/` ensures all routes are handled by the SPA:
```
/* /index.html 200
```

This file is automatically copied to the build output during the build process.

## Preview Deployments

- **Pull Requests**: Automatically create preview deployments
  - PRs targeting `main` → Preview in `maigie-web-prod` project
  - PRs targeting `development` → Preview in `maigie-web-dev` project
  - Preview URLs are automatically commented on PRs via GitHub integration
- **Branch Deployments**: 
  - `main` branch → Deploys to `maigie-web-prod` (production)
  - `development` branch → Deploys to `maigie-web-dev` (development)
  - Each branch maintains its own deployment history

## Troubleshooting

### Build Fails
1. Check Node version matches `.nvmrc` (Node 20)
2. Verify all dependencies are installed (`npm ci`)
3. Check build logs in Cloudflare Pages dashboard

### Routing Issues
- Ensure `_redirects` file exists in `apps/web/public/`
- Verify the file is copied to build output
- Check Cloudflare Pages redirect rules

### Environment Variables Not Working
- Verify variables are set in Cloudflare Pages dashboard
- Check variable names match code expectations
- Rebuild after adding new variables

## References

- [Cloudflare Pages Documentation](https://developers.cloudflare.com/pages/)
- [Nx Documentation](https://nx.dev)
- [Vite Deployment Guide](https://vitejs.dev/guide/static-deploy.html)

