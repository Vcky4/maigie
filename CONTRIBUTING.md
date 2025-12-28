# Contributing to Maigie

Thanks for your interest in contributing to **Maigie**! 🎉
Your support helps us build a truly intelligent study assistant that empowers learners with AI‑driven guidance, organization, and productivity tools.

This guide explains how to contribute to the project, from setting up the repo to submitting high‑quality pull requests.

---

## 🧭 Table of Contents

* [Code of Conduct](#code-of-conduct)
* [Project Structure](#project-structure)
* [Getting Started](#getting-started)
* [Development Workflow](#development-workflow)
* [Commit Convention](#commit-convention)
* [Branching Model](#branching-model)
* [Pull Request Guidelines](#pull-request-guidelines)
* [Coding Standards](#coding-standards)
* [Testing](#testing)
* [AI Feature Development Rules](#ai-feature-development-rules)
* [Documentation](#documentation)
* [How to Request Features](#how-to-request-features)
* [How to Report Issues](#how-to-report-issues)
* [License](#license)

---

## 📜 Code of Conduct

This project follows a standard **Contributor Covenant**. By participating, you agree to:

* Provide a welcoming and inclusive environment
* Be respectful and constructive
* Avoid harassment or discrimination

---

## 📁 Project Structure

Maigie is a **monorepo** managed with **Nx**, containing:

```
/ apps
   / backend    → FastAPI backend

/ libs
   / ui         → Shared components
   / auth       → Shared auth helpers
   / types      → Shared TypeScript types
   / ai         → Shared prompts, schema for AI interactions
   / db         → Prisma schema + migrations
```

---

## 🚀 Getting Started

### 1. Fork & Clone the Repository

```
git clone https://github.com/vcky4/maigie.git
cd maigie
```

### 2. Install Dependencies

```
pm install
```

### 3. Setup Local Environment

Create a `.env` file in `/apps/backend` using the provided `.env.example` file.

### 4. Start Development

```
npx nx serve backend
```

---

## 🔁 Development Workflow

1. Find/open an issue
2. Create a feature branch
3. Make changes locally
4. Write tests (if applicable)
5. Open a Pull Request
6. Address comments
7. Merge when approved

---

## 🧱 Branching Model

Use the following structure:

* **main** → production
* **dev** → integration branch
* **feat/<feature-name>** → new features
* **fix/<bug-name>** → bug fixes
* **docs/<section>** → documentation updates
* **refactor/<area>** → code refactoring

Example:

```
git checkout -b feat/ai-intent-engine
```

---

## 📝 Commit Convention

Follow **Conventional Commits**:

```
feat: add new AI intent mapping engine
fix: handle crash when generating schedule
docs: update API reference
refactor: simplify auth flow
chore: update dependencies
```

---

## 📦 Pull Request Guidelines

A good PR includes:

* A clear title and description
* Linked issue or context
* Small, focused changes
* Passing tests
* Updated documentation (if needed)

Before submitting, ensure:

```
npm run lint
npm run test
```

---

## 🧩 Coding Standards

* Follow the TypeScript style guide
* Use ESLint and Prettier (preconfigured)
* Keep functions small and purposeful
* Always type your input/output
* Avoid magic strings — use enums or constants

---

## 🧪 Testing

We use:

* **Pytest** for FastAPI backend

Write tests for:

* Core logic
* AI intent routing
* API endpoints

---

## 🤖 AI Feature Development Rules

When modifying the AI:

* Do not change base system prompts without discussion
* Document new intents in `/apps/ai/intents` directory
* Ensure new flows are logged for transparency
* Test manually in the sandbox chat environment

---

## 📘 Documentation

All documentation lives in the `/docs` folder.
When changing:

* API → update `/docs/architecture/backend.md`
* AI → update `/docs/architecture/ai.md`
* Architecture → update `/docs/architecture/`

---

## 💡 Requesting New Features

Create an issue containing:

* Problem statement
* Why it matters
* Proposed solution
* (Optional) UI sketches

---

## 🐛 Reporting Issues

Include:

* Steps to reproduce
* Expected vs actual behavior
* Screenshots or logs
* Environment info

---

## 📄 License

By contributing, you agree that your contributions will be licensed under the project’s existing license.

---

Thank you for helping build **Maigie** — the AI-powered companion helping students study smarter, not harder! 🚀
