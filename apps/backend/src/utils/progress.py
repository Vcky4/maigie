"""
Helpers for normalizing completion percentages in API responses.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""


def round_progress_percent(value: float) -> float:
    """Round a 0–100 completion percentage to two decimal places for JSON/clients."""
    return round(float(value), 2)
