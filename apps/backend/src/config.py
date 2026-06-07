"""
Application configuration management.
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from pydantic import BeforeValidator
from pydantic_settings import BaseSettings, SettingsConfigDict


def parse_list_value(value: Any) -> list[str]:
    """Parse list value from various formats."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return []
        if value.startswith("[") and value.endswith("]"):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return parsed
            except (json.JSONDecodeError, TypeError):
                pass
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


ListStr = Annotated[list[str], BeforeValidator(parse_list_value)]


class Settings(BaseSettings):
    # ... existing settings ...

    # --- Email ---
    SMTP_HOST: str | None = None
    SMTP_PORT: int | None = None
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    EMAILS_FROM_EMAIL: str | None = None
    EMAILS_FROM_NAME: str | None = None
    EMAIL_LOGO_URL: str = ""  # URL to logo image for email templates
    # Resend (https://resend.com) — used as fallback when primary SMTP (e.g. Brevo) fails or quota is hit
    RESEND_API_KEY: str = ""
    # Optional verified sender for Resend; defaults to EMAILS_FROM_EMAIL when unset
    RESEND_FROM_EMAIL: str | None = None
    # When Brevo SMTP accepts mail but you are over quota it often still fails as SMTP error/refused;
    # if SMTP "succeeds" without error you cannot auto-detect — use resend_then_smtp or resend_only until quota resets.
    # smtp_then_resend | resend_then_smtp | resend_only | smtp_only
    EMAIL_OUTBOUND_STRATEGY: str = "smtp_then_resend"

    # --- Application Info ---
    APP_NAME: str = "Maigie API"
    APP_VERSION: str = "0.1.0"
    APP_DESCRIPTION: str = "AI-powered student companion API"  # <--- THIS WAS MISSING
    DEBUG: bool = False
    ENVIRONMENT: str = "development"

    # --- API & URLs ---
    API_V1_STR: str = "/api/v1"  # Renamed from API_V1_PREFIX to match auth.py
    ALLOWED_HOSTS: ListStr = ["localhost", "127.0.0.1"]
    FRONTEND_BASE_URL: str = ""  # For OAuth redirects

    # --- CORS ---
    CORS_ORIGINS: ListStr = [
        "http://localhost:4200",
        "http://localhost:5173",
        "https://maigie.com",
        "https://www.maigie.com",
        "https://app.maigie.com",
        "https://admin.maigie.com",
        "https://dev-admin.maigie.com",
        "http://localhost:4201",
    ]
    CORS_ALLOW_CREDENTIALS: bool = True
    CORS_ALLOW_METHODS: ListStr = ["*"]
    CORS_ALLOW_HEADERS: ListStr = ["*"]

    # --- Security & Auth ---
    SECRET_KEY: str = "dev-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 43200  # 30 days (monthly)
    REFRESH_TOKEN_EXPIRE_DAYS: int = 90  # 90 days for refresh tokens

    # --- Database ---
    DATABASE_URL: str = ""  # Loaded from .env

    # --- Redis Cache ---
    REDIS_URL: str = "redis://localhost:6379/0"

    REDIS_KEY_PREFIX: str = "maigie:"
    REDIS_SOCKET_TIMEOUT: int = 5
    REDIS_SOCKET_CONNECT_TIMEOUT: int = 5

    # --- WebSocket ---
    WEBSOCKET_HEARTBEAT_INTERVAL: int = 30
    WEBSOCKET_HEARTBEAT_TIMEOUT: int = 120
    WEBSOCKET_MAX_RECONNECT_ATTEMPTS: int = 5

    # --- OAuth Providers ---
    OAUTH_GOOGLE_CLIENT_ID: str | None = None
    OAUTH_GOOGLE_CLIENT_SECRET: str | None = None
    # OAuth base URL for redirect URI (use deployed domain for both local and production)
    # If set, this will override the dynamically constructed base URL from request
    # Example: https://api.maigie.com or https://pr-51-api-preview.maigie.com
    # If not set, will fall back to get_base_url_from_request()
    OAUTH_BASE_URL: str | None = None

    # --- Celery (Background Workers) ---
    CELERY_BROKER_URL: str = ""
    # Optional: result backend is not required for enqueueing tasks.
    # If unset, we disable the result backend to avoid Redis backend failures
    # taking down request paths (e.g. chat websockets in preview envs).
    CELERY_RESULT_BACKEND: str | None = None
    CELERY_TASK_SERIALIZER: str = "json"
    CELERY_RESULT_SERIALIZER: str = "json"
    CELERY_ACCEPT_CONTENT: ListStr = ["json"]
    CELERY_TIMEZONE: str = "UTC"
    CELERY_ENABLE_UTC: bool = True
    CELERY_TASK_ALWAYS_EAGER: bool = False
    CELERY_TASK_ACKS_LATE: bool = True
    CELERY_TASK_REJECT_ON_WORKER_LOST: bool = True
    CELERY_WORKER_PREFETCH_MULTIPLIER: int = 1
    CELERY_TASK_DEFAULT_QUEUE: str = "default"
    CELERY_TASK_DEFAULT_EXCHANGE: str = "tasks"
    CELERY_TASK_DEFAULT_ROUTING_KEY: str = "default"
    CELERY_RESULT_EXPIRES: int = 3600

    # --- Logging & Sentry ---
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"
    SENTRY_DSN: str | None = None
    SENTRY_TRACES_SAMPLE_RATE: float = 0.1

    # WebSocket
    WEBSOCKET_HEARTBEAT_INTERVAL: int = 30  # seconds
    WEBSOCKET_HEARTBEAT_TIMEOUT: int = (
        120  # seconds — must exceed LLM_ADAPTER_TIMEOUT_SECONDS + fallback time
    )
    WEBSOCKET_MAX_RECONNECT_ATTEMPTS: int = 5

    # Brevo (formerly Sendinblue) CRM Integration
    BREVO_API_KEY: str = ""
    BREVO_ENABLED: bool = True

    # --- Stripe Subscription ---
    STRIPE_SECRET_KEY: str = ""
    STRIPE_PUBLISHABLE_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""  # Webhook signing secret (whsec_...) from webhook destination
    STRIPE_WEBHOOK_DESTINATION_ID: str = (
        ""  # Webhook destination ID (required when using destinations)
    )
    # Maigie Plus (7-day trial)
    STRIPE_PRICE_ID_MONTHLY: str = ""
    STRIPE_PRICE_ID_YEARLY: str = ""
    # Circle Plan (per-Circle subscription, 7-day trial on first purchase)
    STRIPE_PRICE_ID_CIRCLE_PLAN_MONTHLY: str = ""
    # Plus Seat add-on (per-seat, no trial)
    STRIPE_PRICE_ID_PLUS_SEAT_ADD_ON_MONTHLY: str = ""
    # DEPRECATED: Study Circle tier removed from active product catalog
    # (Requirements 1.8, 17.9). Price IDs retained only so historical
    # billing records and webhook lookups can continue to identify the
    # source tier; the active catalog excludes them.
    STRIPE_PRICE_ID_STUDY_CIRCLE_MONTHLY: str = ""
    STRIPE_PRICE_ID_STUDY_CIRCLE_YEARLY: str = ""
    # DEPRECATED: Squad tier removed from active product catalog
    # (Requirements 1.6, 1.8, 17.9). Same retention rationale as above.
    STRIPE_PRICE_ID_SQUAD_MONTHLY: str = ""
    STRIPE_PRICE_ID_SQUAD_YEARLY: str = ""
    # Trial days per plan (used when creating checkout sessions)
    TRIAL_DAYS_MAIGIE_PLUS: int = 7
    TRIAL_DAYS_CIRCLE_PLAN: int = 7
    TRIAL_DAYS_STUDY_CIRCLE: int = 3  # retained for historical webhook handling
    TRIAL_DAYS_SQUAD: int = 3  # retained for historical webhook handling
    # Catalog prices (cents, USD). Source of truth for `GET /plans/catalog`.
    # Per Requirement 1.3: Plus $4.99/mo or $39/yr, Circle Plan $14.99/mo,
    # Plus Seat add-on $4.99/seat/mo.
    PRICE_CENTS_PLUS_MONTHLY: int = 499
    PRICE_CENTS_PLUS_YEARLY: int = 3900
    PRICE_CENTS_CIRCLE_PLAN_MONTHLY: int = 1499
    PRICE_CENTS_PLUS_SEAT_ADD_ON_MONTHLY: int = 499
    FRONTEND_URL: str = "http://localhost:4200"  # For redirect URLs

    # --- Paystack (Nigeria) ---
    PAYSTACK_SECRET_KEY: str = ""
    PAYSTACK_PUBLIC_KEY: str = ""
    # Plan codes (create plans in Paystack Dashboard, amounts in NGN)
    PAYSTACK_PLAN_MAIGIE_PLUS_MONTHLY: str = ""
    PAYSTACK_PLAN_MAIGIE_PLUS_YEARLY: str = ""
    # Circle Plan / Plus Seat add-on
    PAYSTACK_PLAN_CIRCLE_PLAN_MONTHLY: str = ""
    PAYSTACK_PLAN_PLUS_SEAT_ADD_ON_MONTHLY: str = ""
    # DEPRECATED: retained only for historical webhook lookups; excluded
    # from the active product catalog (Requirements 1.8, 17.9).
    PAYSTACK_PLAN_STUDY_CIRCLE_MONTHLY: str = ""
    PAYSTACK_PLAN_STUDY_CIRCLE_YEARLY: str = ""
    PAYSTACK_PLAN_SQUAD_MONTHLY: str = ""
    PAYSTACK_PLAN_SQUAD_YEARLY: str = ""

    # --- Google Play Billing ---
    # Service account JSON for verifying purchases via Google Play Developer API.
    # Either provide a file path or the raw JSON string (for containerized environments).
    GOOGLE_PLAY_SERVICE_ACCOUNT_JSON: str = ""  # Raw JSON string (preferred in production)
    GOOGLE_PLAY_SERVICE_ACCOUNT_FILE: str = ""  # Path to service account JSON file
    # Package name of the Android app
    GOOGLE_PLAY_PACKAGE_NAME: str = "com.maigie"
    # Product IDs configured in Google Play Console (must match exactly)
    GOOGLE_PLAY_SKU_PLUS_MONTHLY: str = "maigie_plus_monthly"
    GOOGLE_PLAY_SKU_PLUS_YEARLY: str = "maigie_plus_yearly"

    # --- BunnyCDN Storage ---
    BUNNY_CDN_API_KEY: str | None = None
    BUNNY_STORAGE_ZONE: str | None = None
    BUNNY_CDN_HOSTNAME: str = "cdn.maigie.com"
    # Optional full public base for uploaded files, e.g. https://yourzone.b-cdn.net (no trailing slash).
    # Use when the custom CDN hostname has TLS issues but Bunny's default pull zone is valid.
    BUNNY_PUBLIC_URL_BASE: str | None = None

    # --- Pinecone (Vector Database for RAG) ---
    PINECONE_API_KEY: str | None = None
    PINECONE_INDEX_NAME: str = "maigie"
    PINECONE_CLOUD: str = "aws"
    PINECONE_REGION: str = "us-east-1"

    # --- ElevenLabs (Smart AI Tutor, Exam Prep voice, Conversational AI agent) ---
    ELEVENLABS_API_KEY: str | None = None
    ELEVENLABS_VOICE_ID: str = "56AoDkrOh6qfVPDXZ7Pt"  # Default voice
    ELEVENLABS_AGENT_ID: str = ""  # Conversational AI agent ID

    # --- LLM (Gemini primary; OpenAI / Anthropic reserved for multi-provider work) ---
    GEMINI_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    # Optional comma-separated model fallback chains (see llm_service rotation helpers)
    GEMINI_ROTATING_MODELS: str | None = None
    GEMINI_EXAM_PREP_MODELS: str | None = None
    GEMINI_SCHEDULE_AI_MODELS: str | None = None

    # --- Multi-Provider LLM Configuration ---
    # Default models per provider
    OPENAI_DEFAULT_MODEL: str = "gpt-4o-mini"
    ANTHROPIC_DEFAULT_MODEL: str = "claude-sonnet-4-20250514"

    # Circuit Breaker
    CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = 5
    CIRCUIT_BREAKER_COOLDOWN_SECONDS: float = 30.0
    CIRCUIT_BREAKER_ROLLING_WINDOW_SECONDS: float = 60.0

    # Router timeout — max seconds for the entire route_request pipeline (selection + execution).
    # Must be generous enough for streaming LLM responses (typically 10–60s).
    LLM_ROUTER_TIMEOUT_SECONDS: float = 90.0

    # Per-adapter request timeout — max seconds for a single adapter call before
    # the router treats it as a timeout failure and falls back to the next provider.
    LLM_ADAPTER_TIMEOUT_SECONDS: float = 60.0

    # Retry
    LLM_MAX_RETRIES: int = 2
    LLM_RETRY_BASE_DELAY_SECONDS: float = 1.0

    # Fallback chains (comma-separated provider:model pairs)
    FALLBACK_CHAT_DEFAULT: str = (
        "gemini:gemini-2.5-flash,openai:gpt-4o-mini,anthropic:claude-sonnet-4-20250514"
    )
    FALLBACK_CHAT_TOOLS: str = (
        "gemini:gemini-2.5-flash,openai:gpt-4o,anthropic:claude-sonnet-4-20250514"
    )

    # Feature flags — enabled providers (comma-separated)
    LLM_ENABLED_PROVIDERS: str = "gemini,openai"
    # Tier-based model allowlists (comma-separated provider:model pairs).
    # Only ``free`` and ``plus`` exist after Circle Reimagining; Circle-scoped
    # AI capabilities are derived from Seat_Tier and resolve to one of these
    # two keys via FeatureFlagService.effective_tier_for_request.
    LLM_TIER_ALLOWLIST_FREE: str = "gemini:gemini-2.5-flash,gemini:gemini-2.0-flash-lite"
    LLM_TIER_ALLOWLIST_PLUS: str = (
        "gemini:gemini-2.5-flash,gemini:gemini-2.0-flash-lite,openai:gpt-4o-mini"
    )

    # --- Gemini Live (voice) — was scattered os.getenv reads; keep in Settings ---
    GEMINI_LIVE_CREDITS_PER_MINUTE: float = 100.0
    GEMINI_LIVE_MIN_SESSION_CREDITS: int = 500
    GEMINI_LIVE_STANDBY_IDLE_SECONDS: float = 2.5
    GEMINI_LIVE_BILLING_TICK_SECONDS: float = 2.0
    GEMINI_LIVE_BILLING_MIN_CONSUME_CHUNK: int = 50
    GEMINI_LIVE_BILLING_FLUSH_INTERVAL_SECONDS: float = 60.0
    GEMINI_LIVE_MODEL: str = "models/gemini-2.5-flash-native-audio-preview-12-2025"
    GEMINI_LIVE_GREETING_PROMPT: str | None = None

    # --- Firebase Cloud Messaging (Push Notifications) ---
    # Path to Firebase service account JSON file (preferred for production)
    FIREBASE_SERVICE_ACCOUNT_PATH: str = ""
    # Alternatively, provide the JSON content directly (useful for Docker/env-based deploys)
    FIREBASE_SERVICE_ACCOUNT_JSON: str = ""

    # --- Background tasks (schedule AI batching) ---
    AI_SCHEDULE_REVIEW_MAX_USERS: int = 500

    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).parent.parent / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


def _get_redis_url_with_db(redis_url: str, db_number: int) -> str:
    if "/" in redis_url:
        base_url = redis_url.rsplit("/", 1)[0]
        return f"{base_url}/{db_number}"
    return f"{redis_url}/{db_number}"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()

    # Auto-generate Celery URLs
    if not settings.CELERY_BROKER_URL:
        settings.CELERY_BROKER_URL = _get_redis_url_with_db(settings.REDIS_URL, 1)
    # Leave result backend disabled unless explicitly configured

    # Ensure production domains are always included in CORS origins
    # This prevents CORS issues when environment variables override defaults
    required_production_origins = [
        "https://maigie.com",
        "https://www.maigie.com",
        "https://app.maigie.com",
    ]

    # Local dev frontends often call staging/prod API (e.g. VITE_API_BASE_URL=staging-api).
    # If CORS_ORIGINS is set only to deployed web origins in env, merge these back in.
    required_local_origins = [
        "http://localhost:4200",
        "http://127.0.0.1:4200",
        "http://localhost:4201",
        "http://127.0.0.1:4201",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

    # Merge environment-provided origins with required production origins
    # Use a set to avoid duplicates, then convert back to list
    all_origins = set(settings.CORS_ORIGINS)
    all_origins.update(required_production_origins)
    all_origins.update(required_local_origins)
    settings.CORS_ORIGINS = list(all_origins)

    return settings


# Create the instance
settings = get_settings()
