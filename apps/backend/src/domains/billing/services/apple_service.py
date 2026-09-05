"""Apple App Store rail: verify a StoreKit transaction, grant the pass, honour refunds.

MAIGIE_PLUS_COMMERCIAL_PLAN.md Phase 5, §5.7.5, Decision G. The iOS counterpart to the Google Play
rail. StoreKit 2 hands the app a JWS-signed transaction; the app sends it here, and the server verifies
the signature against Apple's root CA before granting anything — a receipt we cannot verify grants
nothing.

**Verification is delegated to Apple's own `app-store-server-library`.** Validating the x5c certificate
chain up to Apple's root CA by hand is exactly the security-sensitive code a library exists to get
right, so `SignedDataVerifier` does it. This module maps Apple's product ids to ours, applies the
voice-pack entitlement gate, and funnels the result into the same `purchase_service` seam every rail
uses — so idempotency, the cross-account guard, and refund-by-revocation are shared, not re-implemented.

**Fails closed.** Without the root certificates (a public, deploy-time artifact) or the In-App Purchase
key, the verifier cannot run and the rail refuses rather than trusting an unverified payload.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.config import get_settings

logger = logging.getLogger(__name__)

# Apple's App Store product ids → our internal catalogue ids. The subscription
# `com.maigie.plus.monthly.sub` is deliberately absent: this rail verifies one-time passes and the
# voice pack, the products that grant a `PlusPass` or voice seconds.
APPLE_PRODUCT_MAP = {
    "com.maigie.plus.pass5h": "plus_pass_5h",
    "com.maigie.plus.pass7d": "plus_pass_7d",
    "com.maigie.plus.passterm": "plus_pass_term",
    "com.maigie.plus.voice30": "plus_voice_30",
}


def _environment():
    from appstoreserverlibrary.models.Environment import Environment

    return (
        Environment.PRODUCTION
        if get_settings().APPLE_ENVIRONMENT.lower() == "production"
        else Environment.SANDBOX
    )


def _root_certificates() -> list[bytes]:
    """Apple's public root CA certs, read from the configured directory. Raises if none are present.

    The verifier needs these to validate the x5c chain. They are public certificates rather than
    secrets, but they are a deploy-time artifact this repo does not carry, so an unset or empty
    directory is a configuration error that must fail closed rather than skip verification.
    """
    settings = get_settings()
    directory = settings.APPLE_ROOT_CA_DIR
    if not directory:
        raise ValueError("Apple root CA directory is not configured (APPLE_ROOT_CA_DIR).")
    root = Path(directory)
    certs = [p.read_bytes() for p in sorted(root.glob("*")) if p.suffix.lower() in (".cer", ".der")]
    if not certs:
        raise ValueError(f"No Apple root CA certificates (*.cer/*.der) found in {directory}.")
    return certs


def _verifier():
    from appstoreserverlibrary.signed_data_verifier import SignedDataVerifier

    settings = get_settings()
    return SignedDataVerifier(
        _root_certificates(),
        # Online checks ask Apple whether a certificate in the chain has been revoked. Worth the call
        # on a money path.
        True,
        _environment(),
        settings.APPLE_BUNDLE_ID,
        settings.APPLE_APP_APPLE_ID,
    )


def _amount_and_currency(product_id: str, txn) -> tuple[int, str]:
    """The charged amount in minor units and its currency, from the transaction where possible.

    Apple's `price` is in **milliunits** (major units × 1000), so minor units are `price / 10` for the
    two-decimal currencies we sell in (USD, NGN, and the rest of Apple's territories). When the price is
    absent, fall back to the configured store price, which §5.7.6's parity check keeps equal to Apple's.
    """
    from src.domains.billing.services import purchase_service

    price = getattr(txn, "price", None)
    currency = getattr(txn, "currency", None)
    if price and currency:
        return int(price) // 10, str(currency)
    region = "NG" if (currency or "").upper() == "NGN" else "US"
    return purchase_service.configured_store_amount(product_id, region)


async def verify_transaction(user_id: str, signed_transaction_info: str) -> dict:
    """Verify a StoreKit 2 signed transaction and grant the pass or credit the voice pack.

    `signed_transaction_info` is the JWS the app reads from `Transaction.jwsRepresentation`. It is
    verified against Apple's root CA (that signature *is* the authentication — there is no shared
    secret), then mapped to our product and funnelled into `purchase_service.fulfill_purchase`, keyed
    on Apple's `transactionId` for idempotency: a replay grants nothing, a transaction bound to another
    learner is `409 PURCHASE_ALREADY_CLAIMED`, and a reinstalled app restores inventory by re-presenting
    the same JWS rather than from StoreKit (which does not return finished consumables).

    The voice pack requires an active Plus entitlement to buy (Decision R): a learner with none is
    refused `403 VOICE_PACK_REQUIRES_PLUS` and nothing is credited.
    """
    from src.domains.billing.services import entitlement_service, purchase_service
    from src.shared.exceptions import MaigieError

    txn = _verifier().verify_and_decode_signed_transaction(signed_transaction_info)

    product_id = APPLE_PRODUCT_MAP.get(txn.productId)
    if product_id is None:
        raise ValueError(f"Unknown Apple product id: {txn.productId}")

    if product_id == purchase_service.VOICE_PACK_PRODUCT_ID:
        entitlement = await entitlement_service.resolve(user_id)
        if entitlement.tier != "plus":
            raise MaigieError(
                message=(
                    "The voice pack is an add-on to Maigie Plus. Start a subscription or activate a "
                    "pass first, then top up your voice minutes."
                ),
                status_code=403,
                code="VOICE_PACK_REQUIRES_PLUS",
            )

    amount_minor, currency = _amount_and_currency(product_id, txn)

    purchase = await purchase_service.fulfill_purchase(
        user_id=user_id,
        product_id=product_id,
        provider="apple",
        provider_reference=txn.transactionId,
        amount_minor=amount_minor,
        currency=currency,
        raw_payload={
            "transactionId": txn.transactionId,
            "originalTransactionId": txn.originalTransactionId,
            "appleProductId": txn.productId,
        },
    )
    logger.info(
        "Apple transaction verified: user=%s product=%s purchase=%s",
        user_id,
        product_id,
        purchase.id,
    )
    return {"verified": True, "productId": product_id, "purchaseId": purchase.id}


async def handle_notification(signed_payload: str) -> None:
    """Process an App Store Server Notification V2. Revokes a pass on REFUND/REVOKE.

    The notification is a JWS; verifying it against Apple's root CA is the authentication. A refund or
    revocation is funnelled into `purchase_service.refund_purchase`, keyed on the transaction id — the
    same `providerReference` the verify path stored — so it finds the pass and revokes it without
    needing to identify the learner. Renewal and expiry types belong to the subscription rail and are
    logged rather than acted on here; consumption requests are acknowledged by logging for now.
    """
    from appstoreserverlibrary.models.NotificationTypeV2 import NotificationTypeV2

    from src.domains.billing.services import purchase_service

    verifier = _verifier()
    notification = verifier.verify_and_decode_notification(signed_payload)
    ntype = notification.notificationType

    if ntype in (NotificationTypeV2.REFUND, NotificationTypeV2.REVOKE):
        data = notification.data
        signed_txn = getattr(data, "signedTransactionInfo", None) if data else None
        if not signed_txn:
            logger.warning("Apple %s notification carried no signed transaction", ntype)
            return
        txn = verifier.verify_and_decode_signed_transaction(signed_txn)
        await purchase_service.refund_purchase(provider_reference=txn.transactionId)
        logger.info("Apple %s: revoked purchase for transaction %s", ntype, txn.transactionId)
        return

    logger.info("Apple notification %s not handled by the one-time rail", ntype)
