"""
Application configuration management.
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit, urlunsplit

from pydantic import BeforeValidator, Field
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

    # Connection pool sizing, per API process.
    #
    # These must be set against the *tenant's* connection allowance, not against
    # what one process would like. Supabase session mode allows 15 concurrent
    # clients, and the arithmetic is multiplicative:
    #
    #     (API processes x (DB_POOL_SIZE + DB_MAX_OVERFLOW)) + Celery workers x 2
    #
    # The previous values (20 + 10) meant a single API process could claim 30 —
    # double the entire allowance — and both compose files run `--workers 2`, so
    # the real ceiling was 60. In practice one local dev server was enough to make
    # migrations fail with `EMAXCONNSESSION`, which is how this was found.
    #
    # At the defaults below, two API workers plus a Celery worker reserve 12 of 15,
    # leaving room for a migration or a psql session. Raise them only alongside a
    # raised allowance.
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 1
    # Recycled well inside PgBouncer's own idle timeout so a checked-out
    # connection is not one the pooler has already discarded.
    DB_POOL_RECYCLE_SECONDS: int = 300

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
    # Maigie Plus subscription (3-day trial on first purchase)
    STRIPE_PRICE_ID_MONTHLY: str = ""
    # DEPRECATED: yearly Plus is withdrawn from the active catalog. The price id is
    # retained so a renewal webhook for a grandfathered `PREMIUM_YEARLY` subscriber can
    # still be identified; `plus_yearly` is rejected with 410 on new purchases.
    STRIPE_PRICE_ID_YEARLY: str = ""
    # Plus passes — one-time prices (`mode: payment`), not subscriptions. A pass is a
    # consumable product: bought, held, activated, spent. Nothing renews.
    STRIPE_PRICE_ID_PLUS_PASS_5H: str = ""
    STRIPE_PRICE_ID_PLUS_PASS_7D: str = ""
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
    # Trial days per plan (used when creating checkout sessions).
    #
    # The Plus trial is **3 days**, not 7. A free 7-day trial sitting beside a $2.49
    # 7-day pass is the same product at two prices, and the one that costs money looks
    # like a trick to anyone who remembers the free one. Three days separates them: the
    # trial is a look, the pass is a study week.
    #
    # This number also lives in three places the server does not control — the Stripe
    # price's `trial_period_days`, the App Store Connect introductory offer, and the Play
    # Console base-plan free trial. All four must read 3, and no test can catch the two
    # that live in a console. It is carried in `GET /plans/catalog` as `trialDays` so no
    # client hardcodes a fourth copy.
    TRIAL_DAYS_MAIGIE_PLUS: int = 3
    # Space-scoped and deliberately unchanged at 7.
    TRIAL_DAYS_CIRCLE_PLAN: int = 7
    # Catalog prices (cents, USD). Source of truth for `GET /plans/catalog`.
    # Personal: 5-hour pass $0.99, 7-day pass $3.99, Plus $9.99/mo.
    # Space-scoped: Circle Plan $14.99/mo, Plus Seat add-on $4.99/seat/mo.
    #
    # `PRICE_CENTS_PLUS_MONTHLY` was 499 for four revisions, held there by one argument:
    # that raising it would force a Stripe price migration, a mandatory 7-day Google Play
    # notice, and on Apple an explicit consent prompt whose non-responders are cancelled at
    # renewal. **All three of those costs are borne by existing subscribers, and there are
    # none.** Phase 2b counted zero Stripe subscription ids, zero Paystack codes and zero
    # Play tokens, so this is a new price rather than a price change, and it costs nothing.
    #
    # It stops costing nothing the moment one person subscribes, which is why it moved now.
    # At $4.99 Maigie was the cheapest thing in its category by 2× against ChatGPT Plus at
    # $20 — a signal of "toy" rather than "value", against a paid case that is specifically
    # not cheap AI access. See MAIGIE_PLUS_COMMERCIAL_PLAN.md §6.1.
    #
    # The allowances moved with the price, in `entitlement_service`: raising the price
    # without raising the allowance would have made the subscription cost *more per unit*
    # than a $0.99 pass, inverting the value ladder. Do not change one without the other.
    PRICE_CENTS_PLUS_PASS_5H: int = 99
    PRICE_CENTS_PLUS_PASS_7D: int = 399
    PRICE_CENTS_PLUS_MONTHLY: int = 999
    # DEPRECATED alongside `STRIPE_PRICE_ID_YEARLY`: retained for grandfathered
    # subscribers' billing records, excluded from the catalog.
    PRICE_CENTS_PLUS_YEARLY: int = 3900
    PRICE_CENTS_CIRCLE_PLAN_MONTHLY: int = 1499
    PRICE_CENTS_PLUS_SEAT_ADD_ON_MONTHLY: int = 499
    FRONTEND_URL: str = "http://localhost:4200"  # For redirect URLs

    # --- Paystack (Nigeria) ---
    PAYSTACK_SECRET_KEY: str = ""
    PAYSTACK_PUBLIC_KEY: str = ""
    # NGN prices in kobo. Set for Nigeria, not converted from USD — at FX parity the $9.99 list is
    # about ₦13 800, twice Netflix Standard, for a study tool whose nearest substitute is free
    # Gemini on the same phone. See MAIGIE_PLUS_COMMERCIAL_PLAN.md §6.8.
    #
    # **These did not move when the USD prices rose**, and the widening ratio is correct rather than
    # a mistake to reconcile: a regional price is set against local substitutes, and Spotify at
    # ₦1 600 and Netflix Mobile at ₦2 500 did not change. The NGN ladder is now 17–27% of USD list
    # instead of 35–52% because the US price was corrected upward, not because Nigeria was
    # discounted downward.
    #
    # All three sit deliberately **under ₦2 500**, which is where Paystack's flat ₦100 fee starts
    # applying on top of the 1.5%. Pricing the subscription at ₦2 500 would turn a ₦36 fee into
    # ₦137 for one naira of extra revenue; at ₦900 the flat fee would have been 14% of a pass. Do
    # not round any of these up without re-reading that.
    #
    # `PRICE_NGN_PLUS_PASS_7D` was 180_000 (₦1 800) and is 150_000 (₦1 500). ₦1 800 was 75% of the
    # monthly price for 23% of the duration, which reads fine as a price ladder and is why it
    # survived four revisions — but allowances are what cost money, and once NGN allowances are
    # derived from NGN net revenue it forced the monthly to be ~4× more generous per day at 1.33×
    # the price. **Nothing overrides a wrong amount on a one-off charge**: for a subscription the
    # Paystack plan supplies the amount, but a pass charge sends this value.
    PRICE_NGN_PLUS_PASS_5H: int = 70_000  # ₦700
    PRICE_NGN_PLUS_PASS_7D: int = 150_000  # ₦1 500
    PRICE_NGN_PLUS_MONTHLY: int = 240_000  # ₦2 400
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
    # Subscription product ID (single subscription with multiple base plans)
    GOOGLE_PLAY_SUBSCRIPTION_ID: str = "maigie_plus"
    # Base plan IDs within the subscription
    GOOGLE_PLAY_BASE_PLAN_MONTHLY: str = "plus-monthly"
    GOOGLE_PLAY_BASE_PLAN_YEARLY: str = "plus-yearly"
    #: Audience configured on the Pub/Sub push subscription that delivers Google Play RTDN.
    #: Empty means RTDN ingestion is refused rather than trusted — the endpoint had no
    #: authentication at all before Phase 2a, and it is unauthenticated by construction, so the
    #: OIDC token Pub/Sub signs is the only thing distinguishing a notification from a `curl`.
    GOOGLE_PUBSUB_AUDIENCE: str = ""
    #: Service account email of that push subscription. A Google-signed token only proves Google
    #: minted it; this proves it was *our* subscription. Optional but strongly recommended.
    GOOGLE_PUBSUB_SERVICE_ACCOUNT_EMAIL: str = ""

    # The three `GOOGLE_PLAY_SKU_CREDIT_*` product IDs are gone with credit packs (§6.1). They must
    # not simply be renamed to pass SKUs: these were never created in the Play Console, and a pass
    # is a different kind of consumable — bought as inventory, activated later (Decision A). The
    # pass SKUs land with the purchase rails, alongside their App Store counterparts.

    # --- BunnyCDN Storage ---
    BUNNY_CDN_API_KEY: str | None = None
    BUNNY_STORAGE_ZONE: str | None = None
    BUNNY_CDN_HOSTNAME: str = "cdn.maigie.com"
    # Optional full public base for uploaded files, e.g. https://yourzone.b-cdn.net (no trailing slash).
    # Use when the custom CDN hostname has TLS issues but Bunny's default pull zone is valid.
    BUNNY_PUBLIC_URL_BASE: str | None = None
    # BunnyCDN storage region code (de/uk/ny/la/sg/se/syd/br/jh). Default: uk.
    BUNNY_STORAGE_REGION: str = "uk"

    # --- Auto Blog Pipeline ---
    BLOG_AUTOPILOT_ENABLED: bool = True
    BLOG_GOOGLE_DRIVE_FOLDER_ID: str = ""  # Folder containing cover images
    BLOG_GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON: str = ""  # Service account JSON for Drive API
    BLOG_GITHUB_TOKEN: str = ""  # PAT with repo write access to maigie-public
    BLOG_GITHUB_REPO: str = "Maigie-Ltd/maigie-public"
    BLOG_DEFAULT_AUTHOR_NAME: str = "Maigie Team"
    BLOG_DEFAULT_AUTHOR_ROLE: str = "Learning Science"

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
    # Chain order is shared across tiers; the allowlist is what turns it into a paywall. Plus starts
    # at `gemini-3.5-flash`; Free's first *allowed* entry is `gemini-3.1-flash-lite`, and both tiers
    # fall back to `gemini-3.5-flash-lite` after it.
    FALLBACK_CHAT_DEFAULT: str = (
        "gemini:gemini-3.5-flash,gemini:gemini-3.1-flash-lite,"
        "gemini:gemini-3.5-flash-lite,openai:gpt-4o-mini,"
        "anthropic:claude-sonnet-4-20250514"
    )
    FALLBACK_CHAT_TOOLS: str = (
        "gemini:gemini-3.5-flash,gemini:gemini-3.1-flash-lite,"
        "gemini:gemini-3.5-flash-lite,openai:gpt-4o,"
        "anthropic:claude-sonnet-4-20250514"
    )

    # Feature flags — enabled providers (comma-separated).
    #
    # **Gemini only: no OpenAI or Anthropic key is provisioned.** So this is not a cost preference, it
    # is a statement of what can actually answer. Listing an unkeyed provider buys a fallback that
    # cannot succeed — three timed attempts against a missing credential, added to the latency of
    # every failed generation, on a path the learner is waiting on.
    #
    # It was `"gemini,openai"`, and the default did real work: the switch is read by
    # `adapter_registry`, `feature_flags`, `router` and — since revision 11a of
    # MAIGIE_PLUS_COMMERCIAL_PLAN.md — `llm_resilient.enabled_providers`, so an environment that
    # simply did not set the variable enabled the OpenAI fallback everywhere. Off by default, on by
    # configuration, is the direction that fails safe.
    #
    # Two things to know before adding a provider back, because neither is visible from here. Only the
    # Gemini path carries the `thinking` bound (Phase 0) and the model-quality split (Decision P), so
    # a fallback provider is unbounded and untiered and can cost more than the Plus model it stands in
    # for. And `OPENAI_DEFAULT_MODEL` / `ANTHROPIC_DEFAULT_MODEL` are single models with no tier
    # variants, so there is nothing for the split to choose between even if it reached them.
    #
    # `LLM_TIER_ALLOWLIST_PLUS` still lists `openai:gpt-4o-mini` deliberately. An allowlist entry for a
    # disabled provider is inert rather than wrong — `is_model_allowed` checks this switch first — and
    # leaving it means re-enabling OpenAI is one variable rather than two.
    LLM_ENABLED_PROVIDERS: str = "gemini"
    # Tier-based model allowlists (comma-separated provider:model pairs).
    # Only ``free`` and ``plus`` exist after Circle Reimagining; Circle-scoped
    # AI capabilities are derived from Seat_Tier and resolve to one of these
    # two keys via FeatureFlagService.effective_tier_for_request.
    #
    # **Free does not get `gemini-3.5-flash`, and used to.** It was listed here *and* sat first in
    # `FALLBACK_CHAT_DEFAULT`, and `router._select_candidates` walks the chain in order keeping the
    # first allowed pair — so every free chat turn ran on the most expensive model in the chain. With
    # the corrected rate card that is $0.0174 a turn against $0.0029 on Flash-Lite: **6×**.
    #
    # It also meant the model allowlist gated nothing. MAIGIE_PLUS_COMMERCIAL_PLAN.md §6.3 argues the
    # free tier is "starved of voice and of model quality" rather than of conversation, and sizes the
    # Free window at 500 units on the strength of it. At the price actually being paid, 500 units
    # bought ~3 chat turns, not the ~16 the plan claims — which is the failure the same section says
    # the old design had and this one fixes.
    #
    # Scope worth knowing: this allowlist is consulted by `router.route_request`, whose only caller is
    # the Ask/chat turn. The 26 other LLM call sites reach providers through
    # `personal_learning.services.llm_resilient`, which selects a provider of its own and never asks
    # this question — so model quality is tier-gated on chat and nowhere else. See drift item 23.
    # Free carries **two** Flash-Lite models so a provider failure degrades rather than fails. The
    # second is dearer than the first ($0.30/$2.50 against $0.25/$1.50) and still 5× cheaper on input
    # than the Plus model, so the fallback costs a third more than the primary instead of six times.
    LLM_TIER_ALLOWLIST_FREE: str = "gemini:gemini-3.1-flash-lite,gemini:gemini-3.5-flash-lite"
    LLM_TIER_ALLOWLIST_PLUS: str = (
        "gemini:gemini-3.5-flash,gemini:gemini-3.1-flash-lite,"
        "gemini:gemini-3.5-flash-lite,openai:gpt-4o-mini"
    )

    # --- Gemini Live (voice) — was scattered os.getenv reads; keep in Settings ---
    #
    #: Usage units charged per minute of billable voice.
    #:
    #: **This was the largest single mispricing in the product.** It began at 100.0, which priced a
    #: voice minute as if it were 100 tokens of text. A conversational minute of
    #: `gemini-3.1-flash-live-preview` costs about **$0.023** — roughly $0.005/min of audio in and
    #: $0.018/min out — while a text token on `gemini-3.5-flash` ($1.50/$9.00 per 1M) costs about
    #: $0.0000020 at a realistic 8 000-in / 600-out mix. A voice minute therefore cost what ~11 400
    #: text tokens cost, and was billed as 100: **under-priced by about 100×.** Against a 5 000/day
    #: free cap and no tier gate on `study_voice` at all, a free learner could run ~250 minutes of
    #: live voice a day — about $170/month, at zero revenue.
    #:
    #: `study_voice/billing.py` had flagged it as "a pricing question flagged in the design document,
    #: not a bug here", which is how it survived: nothing was broken, no test failed, and the comment
    #: that noticed the problem also gave the reader permission to move on.
    #: **Now denominated in usage units, so this is a cost and not a coefficient.** A unit is
    #: $0.0001 of measured COGS (§6.2), a conversational minute of `gemini-3.1-flash-live-preview`
    #: costs about $0.023, and 230 would be the arithmetic. 200 is the §6.3 figure: still a little
    #: under, still erring towards the learner, and a round number the marketing can be checked
    #: against — a free window of 500 units is 2.5 minutes of voice, which is what §6.1's
    #: `usage_note` says.
    #:
    #: The 10 000 this replaces was the same correction expressed in the old token denomination,
    #: where it had to be 10 000 because a "credit" was a token and a voice minute cost what ~11 400
    #: text tokens cost. Nothing about the price changed here; the unit did, and 200 units of
    #: measured cost is the same money as 10 000 pre-multiplier tokens was meant to be. That the two
    #: figures differ by 50× is the whole argument for denominating in cost: a rate expressed in
    #: tokens has to be re-derived every time a model's price moves, and this one was not re-derived
    #: for the life of the feature.
    #: **Now a cost basis rather than a charging rate.** §6.3 moved voice onto its own
    #: seconds-denominated balance (`voiceSecondsRemaining`), so nothing converts voice time into
    #: units in order to charge for it any more. This figure survives because the *margin* tables and
    #: the catalogue copy still need to know what a voice minute costs — 200 units is $0.02 — and
    #: because it is what makes "40× a chat turn" a checkable claim rather than an assertion.
    GEMINI_LIVE_UNITS_PER_MINUTE: float = 200.0
    #: Wall-clock session floor **in seconds**, charged at settlement for FREE sessions only. One
    #: minute: a floor shorter than a minute stops being a floor, and a longer one charges a learner
    #: for time they did not get.
    #:
    #: Was `GEMINI_LIVE_MIN_SESSION_UNITS = 200`, which was the same minute expressed in the currency
    #: voice no longer spends. Renamed rather than reinterpreted — a quantity that changes
    #: denomination has to change name, or the next reader converts it twice.
    #:
    #: It no longer doubles as the pre-start affordability check. That check now asks the voice
    #: balance whether a session can be funded at all, which is a different and better question: the
    #: floor is about the minimum a session costs, not about whether the learner has voice.
    GEMINI_LIVE_MIN_SESSION_SECONDS: int = 60
    GEMINI_LIVE_STANDBY_IDLE_SECONDS: float = 2.5
    GEMINI_LIVE_BILLING_TICK_SECONDS: float = 2.0
    #: Accrued **seconds** to batch before writing, so a 2-second tick does not mean a write every
    #: 2 seconds per active session.
    #:
    #: 15 seconds, which is what the old `GEMINI_LIVE_BILLING_MIN_CONSUME_CHUNK = 50` units came to at
    #: 200 units/minute. Worth recording that this constant has now been wrong in both directions: at
    #: the pre-Phase-0 rate of 10 000 units/minute the same 50 was **0.3 seconds** of audio, so it
    #: flushed on essentially every tick and the batching it exists for never happened at all. A
    #: threshold expressed in a derived currency drifts whenever the derivation does; expressed in
    #: seconds it cannot.
    GEMINI_LIVE_BILLING_MIN_CONSUME_SECONDS: int = 15
    GEMINI_LIVE_BILLING_FLUSH_INTERVAL_SECONDS: float = 60.0
    #: Silence this long ends a voice session, in seconds. Ten minutes.
    #:
    #: Distinct from `GEMINI_LIVE_STANDBY_IDLE_SECONDS`, which is 2.5 seconds and decides whether a moment
    #: counts as billable speech. This is abandonment: nobody has spoken for long enough that the sitting is
    #: over. It matters for two reasons — a FREE learner is billed wall-clock, so an empty room costs them
    #: money, and the end-of-session note is written at teardown, so a session that never ends never
    #: produces the note the learner switched on.
    GEMINI_LIVE_ABANDONED_AFTER_SECONDS: float = 600.0
    GEMINI_LIVE_MODEL: str = "models/gemini-3.1-flash-live-preview"
    GEMINI_LIVE_GREETING_PROMPT: str | None = None

    # --- Firebase Cloud Messaging (Push Notifications) ---
    # Path to Firebase service account JSON file (preferred for production)
    FIREBASE_SERVICE_ACCOUNT_PATH: str = ""
    # Alternatively, provide the JSON content directly (useful for Docker/env-based deploys)
    FIREBASE_SERVICE_ACCOUNT_JSON: str = ""

    # --- Notification channel and intelligence rollouts ---
    # Every optional external channel and intelligence path has an independent,
    # deterministic user gate. Denylists win; explicit/internal allowlists bypass
    # percentage cohorts. Keep not-yet-implemented channels default-off.
    NOTIFICATION_EMAIL_ENABLED: bool = False
    NOTIFICATION_EMAIL_DENYLIST: ListStr = []
    NOTIFICATION_EMAIL_ALLOWLIST: ListStr = []
    NOTIFICATION_EMAIL_INTERNAL_ALLOWLIST: ListStr = []
    NOTIFICATION_EMAIL_ROLLOUT_PERCENT: int = Field(default=0, ge=0, le=100)

    WEB_PUSH_ENABLED: bool = False
    WEB_PUSH_DENYLIST: ListStr = []
    WEB_PUSH_ALLOWLIST: ListStr = []
    WEB_PUSH_INTERNAL_ALLOWLIST: ListStr = []
    WEB_PUSH_ROLLOUT_PERCENT: int = Field(default=0, ge=0, le=100)
    #: VAPID identity for Web Push (RFC 8292). The public key is the `applicationServerKey`
    #: a browser passes to `pushManager.subscribe`, as base64url of the raw 65-byte
    #: uncompressed P-256 point; the private key is base64url of the raw 32-byte scalar.
    #: Generate a pair with `scripts/generate_vapid_keys.py`.
    #:
    #: These keys are an identity, not a rotating secret. Every existing subscription is
    #: bound to the public key it was created with, so replacing the pair silently
    #: invalidates every subscription in the field — browsers reject the mismatch and the
    #: only repair is for each learner to resubscribe. Rotate only with a resubscribe plan.
    WEB_PUSH_VAPID_PUBLIC_KEY: str = ""
    WEB_PUSH_VAPID_PRIVATE_KEY: str = ""
    #: `mailto:` or `https:` contact a push service can use to reach us about our traffic.
    #: RFC 8292 requires the claim, and some services drop pushes signed without it.
    WEB_PUSH_VAPID_SUBJECT: str = ""
    #: Hosts a Web Push endpoint may point at. An entry beginning with `.` matches any
    #: subdomain of it; anything else must match exactly.
    #:
    #: This is an SSRF control, not a compatibility list. A subscription endpoint is a URL
    #: chosen by the client, and a background worker POSTs to it, so without this an attacker
    #: could register an internal address and use the worker as a request forwarder it cannot
    #: see the response from. The default covers the push services of every browser Phase 4
    #: targets — Chrome, Edge, Firefox, Safari — and an unusual browser is added here rather
    #: than by allowing arbitrary hosts.
    WEB_PUSH_ALLOWED_ENDPOINT_HOSTS: ListStr = [
        "fcm.googleapis.com",  # Chrome, and Chromium forks
        "android.googleapis.com",  # older Chrome endpoints still in the field
        "web.push.apple.com",  # Safari, macOS and installed iOS PWAs
        ".push.services.mozilla.com",  # Firefox
        ".notify.windows.com",  # Edge, via Windows Notification Service
    ]
    WEB_PUSH_MAX_ATTEMPTS: int = Field(default=5, ge=1, le=10)
    WEB_PUSH_BATCH: int = Field(default=50, ge=1, le=500)
    #: How long a push service may hold an undelivered message, in seconds. Six hours:
    #: long enough to survive a closed laptop, short enough that a study reminder does not
    #: arrive after the session it was reminding about.
    WEB_PUSH_TTL_SECONDS: int = Field(default=21600, ge=0, le=2419200)

    NOTIFICATION_INTELLIGENCE_ENABLED: bool = False
    NOTIFICATION_INTELLIGENCE_DENYLIST: ListStr = []
    NOTIFICATION_INTELLIGENCE_ALLOWLIST: ListStr = []
    NOTIFICATION_INTELLIGENCE_INTERNAL_ALLOWLIST: ListStr = []
    NOTIFICATION_INTELLIGENCE_ROLLOUT_PERCENT: int = Field(default=0, ge=0, le=100)
    NOTIFICATION_INTELLIGENCE_SHADOW_ONLY: bool = True

    # --- LLM-assisted digest copy (Phase 6, Level 2 — bounded LLM assistance) ---
    # A separate gate from INTELLIGENCE on purpose: the timing/channel decision engine and the
    # digest-copy writer are different risks with different blast radii, and one must be able to run
    # without arming the other. Default-off, and shadow-only when on, so the first thing this ever
    # does in a deployment is propose copy that is recorded and discarded — never sent — until a human
    # turns SHADOW_ONLY off. Same fail-closed cohort machinery as every other capability.
    NOTIFICATION_DIGEST_LLM_ENABLED: bool = False
    NOTIFICATION_DIGEST_LLM_DENYLIST: ListStr = []
    NOTIFICATION_DIGEST_LLM_ALLOWLIST: ListStr = []
    NOTIFICATION_DIGEST_LLM_INTERNAL_ALLOWLIST: ListStr = []
    NOTIFICATION_DIGEST_LLM_ROLLOUT_PERCENT: int = Field(default=0, ge=0, le=100)
    NOTIFICATION_DIGEST_LLM_SHADOW_ONLY: bool = True

    # --- Notification data retention (Phase 7) ---
    # Deleting learner data is irreversible, so this is fail-closed: nothing is pruned until an
    # operator sets `ENABLED=True` after agreeing the windows below with whatever retention policy
    # applies. The sweep only ever removes *operational and evidence* rows past their window —
    # delivery attempts, interactions, decisions, digest runs, provider webhook events — and never the
    # `Notification` rows a learner sees in their history. In-flight deliveries (PLANNED/QUEUED/SENDING)
    # are never pruned regardless of age, so a stuck row is preserved for investigation rather than
    # silently deleted. Windows are generous by default and expressed in days.
    NOTIFICATION_RETENTION_ENABLED: bool = False
    NOTIFICATION_RETENTION_DELIVERY_DAYS: int = Field(default=90, ge=1)
    NOTIFICATION_RETENTION_INTERACTION_DAYS: int = Field(default=365, ge=1)
    NOTIFICATION_RETENTION_DECISION_DAYS: int = Field(default=365, ge=1)
    NOTIFICATION_RETENTION_DIGEST_DAYS: int = Field(default=180, ge=1)
    NOTIFICATION_RETENTION_EMAIL_EVENT_DAYS: int = Field(default=90, ge=1)
    #: Rows deleted per statement, so a sweep of a large backlog holds only short locks and can be
    #: interrupted between batches without leaving a half-done transaction.
    NOTIFICATION_RETENTION_BATCH: int = Field(default=2000, ge=1, le=50000)

    # --- Expo mobile push (staged rollout) ---
    # The code fallback is fail-closed when no environment is loaded. The deployment
    # template intentionally enables the sender at a 0% cohort, which still makes no
    # user eligible until an allowlist or percentage rollout is approved.
    EXPO_PUSH_URL: str = "https://exp.host/--/api/v2/push"
    EXPO_ACCESS_TOKEN: str = ""
    MOBILE_PUSH_ENABLED: bool = False
    MOBILE_PUSH_DENYLIST: ListStr = []
    MOBILE_PUSH_ALLOWLIST: ListStr = []
    MOBILE_PUSH_INTERNAL_ALLOWLIST: ListStr = []
    MOBILE_PUSH_ROLLOUT_PERCENT: int = Field(default=0, ge=0, le=100)
    MOBILE_PUSH_MAX_ATTEMPTS: int = Field(default=5, ge=1, le=10)
    NOTIFICATION_EMAIL_MAX_ATTEMPTS: int = Field(default=3, ge=1, le=10)
    NOTIFICATION_EMAIL_BATCH: int = Field(default=50, ge=1, le=500)
    #: Public origin of this API, used for the one-click unsubscribe URL a mail provider POSTs
    #: to. It cannot be derived from a request, because the header is written by a worker.
    PUBLIC_API_BASE_URL: str = ""
    #: Resend webhook signing secret (`whsec_…`). Empty means webhook ingestion is refused
    #: rather than trusted, so an unconfigured deployment cannot be fed forged events.
    RESEND_WEBHOOK_SECRET: str = ""
    MOBILE_PUSH_RECEIPT_DELAY_SECONDS: int = Field(default=900, ge=60, le=86400)
    MOBILE_PUSH_STALE_SENDING_SECONDS: int = Field(default=600, ge=60, le=86400)

    # --- Background tasks (schedule AI batching) ---
    AI_SCHEDULE_REVIEW_MAX_USERS: int = 500

    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).parent.parent / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


def _get_redis_url_with_db(redis_url: str, db_number: int) -> str:
    """Return a Redis URL targeting one logical DB without corrupting its authority."""

    parsed = urlsplit(redis_url)
    return urlunsplit(
        (parsed.scheme, parsed.netloc, f"/{db_number}", parsed.query, parsed.fragment)
    )


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
