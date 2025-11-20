# Background Processing & Jobs

Use Celery or Dramatiq with Redis as broker (or serverless jobs):

* Embedding indexing
* Email & push notifications
* Async resource scraping (fetch metadata for resource URLs)
* Daily digest & forecast recalculation

---

# Deployment & Infrastructure

* Backend: containerized (Docker), deploy to Render/Fly.io/AWS ECS/GCP Cloud Run
* DB: Postgres (Neon / Supabase / RDS)
* Vector DB: Qdrant / Pinecone or Postgres+pgvector
* Redis: Upstash or managed Redis
* Storage: S3-compatible (Supabase Storage / AWS S3)
* Secrets: Vault / cloud secret manager

## CI/CD

* Nx-aware pipelines to test, build, and deploy only affected apps/libraries
* Unit & integration tests (pytest), e2e tests for web (Playwright) and mobile (Detox / Expo E2E)

---

# Observability & SLOs

* Logging: structured logs (JSON) -> centralized (Datadog / Loki)
* Tracing: OpenTelemetry for request traces (especially AI calls)
* Metrics: Prometheus / Grafana for API latency, error rate, queue lengths
* SLO example: 99% API availability, LLM latency under X ms (where feasible)

---

# Security & Privacy

* Encrypt sensitive fields at rest (where needed).
* GDPR-style controls: data export, delete account, explicit consent for storage of audio and AI logs.
* Use rate limiting, input sanitization, and prompt injection mitigations (sanitize retrieved docs, QA-checks on prompts).
* Use short-lived JWT access tokens + refresh tokens; store refresh tokens securely in DB

