"""Low-cardinality notification delivery metrics."""

from prometheus_client import Counter, Gauge

MOBILE_PUSH_OUTCOMES = Counter(
    "notification_mobile_push_outcomes_total",
    "Canonical mobile push lifecycle outcomes",
    ("stage", "outcome"),
)
MOBILE_PUSH_CLAIMED = Counter(
    "notification_mobile_push_claimed_total",
    "Canonical mobile push delivery rows claimed",
)
MOBILE_PUSH_STALE_RECOVERED = Counter(
    "notification_mobile_push_stale_recovered_total",
    "Canonical mobile push SENDING rows recovered",
)
MOBILE_PUSH_LAST_BATCH = Gauge(
    "notification_mobile_push_last_batch_size",
    "Rows handled by the latest canonical mobile push batch",
    ("kind",),
)
EMAIL_OUTCOMES = Counter(
    "notification_email_outcomes_total",
    "Canonical notification email lifecycle outcomes",
    ("stage", "outcome"),
)
EMAIL_CLAIMED = Counter(
    "notification_email_claimed_total",
    "Canonical notification email delivery rows claimed",
)
