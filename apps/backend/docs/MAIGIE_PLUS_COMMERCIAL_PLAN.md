# Maigie Plus Commercial Plan

> Status: **Phases 1 and 2 done. Phase 0 open and verified open. The money path is reachable on Stripe and Google Play, and not on Paystack.**
>
> The `billing` router is mounted, the personal catalogue is the four new products, credit packs and rewarded ads are gone, and the trial is 3 days. **Entitlement is now one resolver**: `entitlement_service.resolve` is the only thing that decides whether a learner is Plus, `require_premium` is deleted, and the two defects the four disagreeing mechanisms were causing — retired tiers denied every capability while the meter granted them millions of credits (drift 10), and trials granted Plus features with free-tier models (drift 11) — are closed. Two blockers remain before this is a money path anyone can actually use: **`paystack_service` is Prisma-removed**, so the NGN rail — the launch market's rail — is still unreachable and its two routes are deliberately unmounted rather than serving 500s (Phase 2b, next); and there is still no way to buy a pass, which needs the one-time checkout in Phase 5.
>
> **Phase 0 is unstarted, not merely unmarked.** Checked in code on 2026-09-01: `cost_calculator._EXACT_MODEL_PRICING:26` still prices `gemini-3.5-flash` at `(0.50, 3.00)` (Question 3), `ask_service.HISTORY_LIMIT:383` is still `12` (Question 2), and `narrative_cache.py:237` still passes `max_tokens=8192` — as does the `generate_content_json` default at `llm/__init__.py:136` (Question 1). The unticked boxes were accurate. Every COGS figure below still rests on an unverified rate card, so **no allowance is tuned in Phase 3 until Question 3 is answered.**
>
> **The Paystack port is now Phase 2b**, an explicit phase with its own checklist, rather than a paragraph inside Phase 1's list of deliberate absences. It was a launch blocker recorded as prose, which is how launch blockers get missed.
>
> **iOS ships with Android.** Open Question 6 is resolved: there is no Android-first phase and the Apple work in Phase 5 does not defer. §5.6 and Phase 5 are rewritten accordingly, and the store-product creation timeline — the thing with the longest external lead time in this plan — is now stated in one place, §5.7.
>
> **The subscriber count is in, and it is stronger than the claim it was checking: there is no payment relationship anywhere in the database.** Zero Stripe subscription ids, zero Paystack codes, zero Play tokens, zero users on a retired tier, zero completed credit purchases; 1 205 `FREE` users and one hand-set `PREMIUM_MONTHLY` with no subscription id against it. Recorded in Phase 2b with the read-only script that produced it, so it is re-runnable rather than remembered. `LEGACY_PLUS_TIERS` is therefore deleted again — and this time the writers were narrowed in the same commit, because the resolver and the writers were only ever wrong apart.
>
> **Phase 2a is done: the four findings below are closed, plus three of the smaller ones.** All three webhooks fail closed and are covered by a new `tests/test_billing_webhook_auth.py`; `LEGACY_PLUS_TIERS` is restored so the resolver and the writers agree; `schedule_reminders` reads the one resolver, so a trialling learner gets reminders; and a subscription now expires lazily on read like a pass and a trial. Remaining in Phase 2a: the `usage_note` figures, the drift-10 wording, and memoising `resolve()`. **587 focused tests pass; the full suite's 17 failures are pre-existing and byte-identical before and after** (verified by stashing). `openapi.json` regenerated for `PlanItem.purchasable` and the two pass ids — **the clients need a types regen.**
>
> **Reviewed 2026-09-01, verdict NEEDS_CHANGES, and Phase 2a is the response.** Phases 1 and 2 were reviewed behaviourally against this plan. The entitlement collapse is structurally sound — `_compose` is pure, precedence is tested before `PlusPass` exists, the deletions are asserted rather than assumed — and **Decision F holds without qualification**: the diff over `learning_spaces/**`, `credit_consumption_service.py`, `test_circle_billing.py` and `test_seat_service.py` is empty, and in `feature_flags.py` only the personal branch moved. 177 tests pass, Ruff is clean, `openapi.json` is current. Four findings needed action and are now Phase 2a, which **runs before Phase 2b** because two of them are live defects rather than gaps:
>
> 1. **Mounting the billing router mounted `webhooks.py` too, turning three unauthenticated endpoints live for the first time, and all three fail open.** `webhooks.py:40-42` parses an unverified Stripe body when `STRIPE_WEBHOOK_SECRET` is empty — and that handler writes `User.tier`, so an unset secret lets any caller grant themselves `PREMIUM_MONTHLY`. `:87` skips Paystack HMAC when the key is empty rather than refusing. `:113` verifies nothing on the Google Play RTDN endpoint under any configuration. All three swallow exceptions into a `200`. The notification work merged in the same range chose the opposite default for `RESEND_WEBHOOK_SECRET` and said so in a comment. **This is the most urgent item in the plan**, ahead of the voice repricing.
> 2. **Grandfathering was dropped in one decider and kept in every writer.** `entitlement_service.PLUS_TIERS` resolves `PREMIUM_YEARLY` to `free`, while `stripe_service._price_id_to_tier:294-295` still writes it on renewals, `:975` uses it in the webhook handler, and `:70` states in prose that yearly subscribers "are grandfathered and keep renewing". A yearly renewal would be charged and entitled to nothing — a fail-open→fail-closed flip on a payer, since `_personal_tier_to_effective` previously mapped any non-FREE tier to `plus`. Two green tests encode opposite intentions.
> 3. **A fifth entitlement decider arrived in the same range.** `schedule_reminders.py:78` gates on `tier != "FREE"`, admitting the five tiers the resolver denies and excluding the trialling and pass-holding learners it grants; its docstring cites `plus_yearly` as a live plan. It came in with the notification merge at `ecaa81b`, so a commercial policy decision shipped inside notification plumbing. It also enforces drift item 4's reminder gate early, with the wrong decider.
> 4. **Subscription entitlement never expires while pass and trial do.** `_compose` grants `plus` on `PREMIUM_MONTHLY` without comparing `subscription_period_end` to now, though it already holds the value. Defensible only if webhooks are reliable, and the Paystack handler currently converts failures into a `200`.
>
> Also recorded, lower severity: the catalogue advertises both passes with no purchase rail and no availability field on `PlanItem`; `CheckoutRequest.plan_id` omits the pass ids so the hand-written "a pass is not a subscription" refusal is unreachable behind a `422`, which is the exact failure the comment at `models.py:22` says the Literal exists to prevent; and every `usage_note` ships chat-turn and voice-minute promises derived from the rate card Phase 0 records as wrong by ~3×, enforced by nothing until Phase 3 — §1's rule pointing back at this plan for the first time.
>
> **There are no paying subscribers, so nothing is grandfathered.** Confirmed as a product fact by the owner, to be confirmed against the database by the first item of Phase 2b. Consequences throughout: `LEGACY_PLUS_TIERS` is never written, `PREMIUM_YEARLY` is removed rather than carried, the retired `STUDY_CIRCLE_*` / `SQUAD_*` price and plan settings are deleted rather than kept for webhook archaeology, and `CreditPack` / `CreditPurchaseTransaction` are dropped rather than kept as read-only history. Store-side, the existing `plus-yearly` base plan and the three `credit_pack_*` consumables are deleted rather than left in place for RTDN lookups that can no longer arrive. New products are created clean; nothing is repurposed.
>
> Owners: Backend (catalogue, entitlement, usage windows, store verification) + Web client + Mobile client + Public site
> Scope: the **personal** product catalogue, purchase rails on three surfaces, the pass activation model, the rolling usage window that replaces daily and monthly credit caps, the earned-points ledger that redeems into passes, and one entitlement resolver that every personal-scope gate reads.
> **Out of scope: Learning Spaces, entirely.** Not the Space feature, not Circle Plan, not the Plus Seat add-on, not `SpaceMember.seat_tier`, not `seat_impl.py`, not `Space.credits`, not the space branch of `consume_credits`, not the space branch of `feature_flags.effective_tier_for_request`. Nothing in this plan reads or writes anything space-scoped. See Decision F.
> Companion documents: [`../../../maigie-client/docs/PREPARE_API_INTEGRATION_PLAN.md`](../../../maigie-client/docs/PREPARE_API_INTEGRATION_PLAN.md) (§4 defers checkout to this document), [`../../../maigie-client/docs/REFLECT_API_INTEGRATION_PLAN.md`](../../../maigie-client/docs/REFLECT_API_INTEGRATION_PLAN.md) (Decision Z, the locked-read convention)
> Source of authority for pricing intent: the Maigie Book — `business/ch36-pricing-philosophy`, `business/ch37-personal-learning`, `philosophy/ch04-product-principles`. Where this plan and the book disagree, the book wins and this plan is wrong. Decision N and §6.7 are derived from them directly.
> Last reviewed: 2026-09-01 (revision 5 — Phase 2 implemented: one resolver, `require_premium` deleted, `personal_tier` removed from the model router's signature, drift 10 and 11 closed. Revision 4 — Phase 0 verified open against code; Paystack port promoted to Phase 2b; iOS committed alongside Android and Open Question 6 resolved; store-product creation timeline consolidated into §5.7; all grandfathering machinery removed on the zero-subscriber fact. Revision 3 implemented Phase 1; revision 2 shortened the trial to 3 days, removed the referral cap, withdrew rewarded ads and introduced earned points as a pass-only currency: §6.9, Decision O)

## 1. Purpose

Three products replace the current **personal** catalogue: two **consumable Plus passes** and one **$5/month subscription**. Credit packs go, and the retired Study Circle and Squad personal tiers are finished off. Daily and monthly credit caps are replaced by a **rolling usage window** that resets on a clock the learner can see.

Alongside the money path there is one earned path: **points**, granted for referring learners who actually stay, redeemable for passes and for nothing else. Rewarded ads are withdrawn.

Then one resolver decides whether a learner is Plus right now, so every personal-scope gate in the codebase agrees.

The two space-scoped products — Circle Plan and the Plus Seat add-on — are not in scope and are not changed.

This document is the implementation checklist and contract reference. Update the checkboxes and decision records as work progresses rather than starting a parallel plan.

It inherits the rule the three surface plans state verbatim:

> Every feature on the frontend must have a backing schema or backend functionality. Nothing ships against a fixture.

And adds one of its own, which is the reason §5.4 exists:

> **Nothing is sold that is not enforced, and nothing is enforced that is not sold.** A price is a promise about behaviour. If the code does not implement the promise, either the code or the price is wrong, and this plan says which.

## 2. Headline finding

Four findings, in descending order of how much they cost.

**The billing domain is unreachable.** `src/app.py:375-379`:

```python
# --- Billing (pending SQLAlchemy migration) ---
# from src.domains.billing.routes import router as billing_router
# from src.domains.billing.webhooks import router as webhooks_router
```

The OpenAPI tag metadata for `billing` and `webhooks` is still declared (`app.py:254-266`), so `/docs` advertises endpoints that are not served. Meanwhile `credit_consumption_service` — imported directly by `study_voice`, `personal_learning` and `knowledge` — *is* running, and enforces caps. So today's production state is: **the meter runs, and there is no way to pay it.** A learner who exhausts 15 000 credits has no reachable endpoint that would sell them more.

**There is no entitlement layer. There are four, and they disagree.** ✅ **Closed by Phase 2.** The finding is kept as written because it is the argument for Decision B and the reason `resolve()` has the shape it does; what follows describes the state before Phase 2, not the state now. Three of the four mechanisms are thin callers of `entitlement_service.resolve`, the fourth (`require_premium`) is deleted, and the credit meter is repointed in Phase 3 — the last of the four, and the only one still reading `CREDIT_LIMITS[User.tier]`.

| Mechanism | Where | Reads | Knows about trials? |
| --- | --- | --- | --- |
| `feature_tier_service` | `personal_learning/services/feature_tier_service.py:190-226` | `User.tier.startswith("PREMIUM")`, else `LearningProfile.trial_ends_at` | yes |
| `require_premium` / `PremiumUser` | `shared/auth/dependencies.py:229-239` | `User.tier in PAID_TIERS` (6 tiers) | no |
| Credits | `billing/services/credit_consumption_service.py:74-107` | `CREDIT_LIMITS[User.tier]` (7 tiers) | no |
| LLM tier resolver | `intelligence/reasoning/llm/feature_flags.py:455-501` | `personal_tier` + `SpaceMember.seat_tier` | **no** |

Consequences already live in the code:

- A `STUDY_CIRCLE_*` or `SQUAD_*` subscriber does not match `startswith("PREMIUM")`, so `feature_tier_service` resolves them to **`"free"`** and denies every matrix capability — while `CREDIT_LIMITS` grants them 500k–12M credits and `PAID_TIERS` would have admitted them. Three incompatible notions of "paid".
- A learner **on a trial** gets Plus quiz modes and Plus documents, and free-tier LLM models, because the model router never consults the trial.
- `require_premium` and `PremiumUser` are wired to **zero endpoints**. A working gate that gates nothing.

Adding passes to this is not viable. A pass is a fifth notion of "paid", and it is the one that changes minute to minute.

**The credit meter measures the wrong period.** Today: a monthly hard cap (`creditsUsed` vs `creditsHardCap`), plus a daily cap for FREE only, plus an 80%-of-month soft warning, plus a purchased-balance fallback, plus a referral daily-limit increase. Five interacting quantities across `check_credit_availability:319-455`. The failure mode a learner actually hits is "I ran out on the 9th and have three weeks of nothing", and the message they get is a wall of formatted numbers. Section 6.2 replaces all of it with one window and one reset time.

**"Apple Pay for in-app purchase" is not the right rail, and the distinction costs money.** For digital content consumed inside the app, Apple and Google require **In-App Purchase / Play Billing** — StoreKit and Play Billing Library, not the Apple Pay or Google Pay wallet APIs. Apple Pay is a payment *method* for physical goods and for web checkout; using it for Plus inside the iOS app is a guaranteed rejection under App Review Guideline 3.1.1. Where Apple Pay and Google Pay *are* correct is **web checkout**, as wallet payment methods on the Stripe payment sheet — a dashboard checkbox, not an integration. So:

| Surface | Rail | Store cut |
| --- | --- | --- |
| Web (`app.maigie.com`) | Stripe Checkout, Apple Pay + Google Pay + card wallets enabled; Paystack for NGN | ~2.9% + 30¢ |
| iOS app | StoreKit 2 in-app purchase | 15–30% |
| Android app | Google Play Billing | 15–30% |

On a $0.99 pass the store keeps 15¢ (Small Business Program) to 30¢. Net is ~$0.69–0.84 versus ~$0.66 on Stripe after fixed fees — at $0.99 the 30¢ Stripe fixed fee makes web *worse* than the store. At $4.99/month web is clearly better ($4.55 vs $4.24). That inversion is worth knowing before anyone builds steering logic; it also means the $0.99 pass is the one product where store distribution costs nothing extra. Full table in §6.4.

## 3. Outcomes

When complete:

- `GET /api/v1/billing/plans/catalog` returns exactly four `scope: "personal"` products — Free, the 5-hour pass, the 7-day pass, Plus Monthly — alongside the two existing `scope: "circle"` / `scope: "add_on"` entries, unchanged. It is the only place any client learns what exists or what it costs.
- A learner can buy any pass or the subscription on web, iOS and Android, and the purchase is verified server-side before anything is granted.
- Buying a pass grants an **inactive** pass. A learner can hold as many as they like, indefinitely. Exactly one runs at a time, and only when they say so.
- An activated pass grants **full Maigie Plus** — every capability, every quality tier, the Plus usage allowance — for its duration, and nothing after it. It does not renew. It is a product, not a subscription.
- There is no daily cap and no monthly cap. Usage is bounded by a **5-hour rolling window** whose allowance depends on tier and whose reset time is on screen before it is reached.
- One function — `entitlement_service.resolve(user_id)` — answers "is this learner Plus right now, why, and until when". Every personal-scope gate in the codebase calls it. Space-scoped resolution is untouched.
- A trialling learner, a pass holder and a subscriber get **identical** capabilities, including LLM model selection.
- A learner can earn **points** by referring learners who stay, with **no monthly cap**, and spend them on passes. Points never touch the subscription, and they expire 60 days after they are earned.
- Nothing in the product asks a learner to watch an advertisement.
- Every price, product id and store SKU in all four repos derives from one server response. No client ships a price literal, and no client converts a currency.
- A Nigerian learner sees NGN prices set for Nigeria (§6.8), not a converted dollar figure.
- No screen shows a learner a number about their own account that the server did not produce.
- Replaying a store purchase token, or presenting one already bound to another account, grants nothing.
- A refund or chargeback revokes what it paid for, including mid-pass.
- Every item on the §5.4 drift list is either enforced or removed from the marketing copy. None survives as a claim.

## 4. Non-goals

- **Yearly Plus.** Withdrawn outright. There are no `PREMIUM_YEARLY` subscribers, so there is nothing to grandfather: the plan id is refused on both doors (Phase 1), the Stripe price is archived, the Play `plus-yearly` base plan is deleted, and `PREMIUM_YEARLY` is not carried in any tier set. Consistent with retiring every other multi-tier product, and cheaper than the alternative — grandfathering is a permanent tax on every tier comparison in the codebase, and it is only worth paying for people who are actually paying us.
- **Redesigning what a credit costs.** `TOKEN_MULTIPLIER = 0.2` and `CREDIT_COSTS` (`credit_consumption_service.py:106-131`) are untouched, as are the per-operation call sites in `study_voice`, `personal_learning` and `knowledge`. Only the *period* a learner draws against changes, and the *size* of the allowance.
- **Rewarded ads, in any form.** Withdrawn rather than re-pointed. `credit_service.claim_ad_reward`, `AD_REWARD_CREDITS`, `MAX_ADS_PER_DAY`, the `/billing/ads/*` routes and both client screens go; the `AdRewardClaim` table stays, empty and unread, because dropping it forecloses the redesign at no saving. When ads return they will be designed as a product decision, not inherited as a credit top-up. See Decision O.
- **Referral reward mechanics are in scope, not deferred**, because a currency that redeems into a sellable product is a commercial design and not a bonus. §6.9 and Decision O replace `referral_rewards_service`'s token grants outright rather than re-pointing them.
- **Learning Spaces, in every respect.** See Decision F for the full boundary and for the one place the boundary is load-bearing rather than merely respected.
- **Circle Plan and the Plus Seat add-on.** Space-scoped products. They keep their catalogue entries, Stripe prices, Paystack plan codes, config settings, seat pool accounting and marketing sections exactly as they are.
- ~~**Migrating legacy tiers off their grandfathered subscriptions in this phase.**~~ **Withdrawn — there is nobody to migrate.** Earlier revisions carried `LEGACY_PLUS_TIERS` so that live `STUDY_CIRCLE_*` / `SQUAD_*` / `PREMIUM_YEARLY` subscribers kept Plus until Phase 8 moved them. There are none. The frozenset is therefore never written, drift 10 is closed by deleting the tiers rather than by admitting them, and the Phase 8 migration step is deleted. **This is the one assumption in the plan that must be checked against the database before any of it is acted on** — it is the first item of Phase 2b, and if it turns out to be false the correct response is to restore `LEGACY_PLUS_TIERS`, not to break a payer.

## 5. Current state

### 5.1 Backend: written, complete, unreachable

`src/domains/billing/` is a full domain — `db_models.py` (343 lines), `models.py` (321), `repository.py` (304), `routes.py` (320), `webhooks.py` (120) and twelve services including a 1029-line `stripe_service` and a 942-line `credit_consumption_service`.

Endpoints that exist and are not mounted:

| Method | Path (intended) | Fate under this plan |
| --- | --- | --- |
| GET | `/billing/plans/catalog` | **rewritten** — new catalogue |
| POST | `/billing/subscriptions/checkout` | kept, `plan_id` set shrinks to one |
| POST | `/billing/subscriptions/sync-checkout` | kept |
| POST | `/billing/subscriptions/portal` | kept |
| POST | `/billing/subscriptions/cancel` | kept |
| POST | `/billing/subscriptions/paystack/initialize` | kept |
| GET | `/billing/subscriptions/paystack/verify` | kept |
| POST | `/billing/subscriptions/google-play/verify` | kept (subscription only) |
| POST | `/billing/subscriptions/google-play/verify-product` | **replaced** by `/billing/purchases/google-play/verify` |
| GET | `/billing/credit-packs` | **removed** |
| POST | `/billing/credit-packs/purchase` | **removed** |
| GET | `/billing/credits/purchases` | **replaced** by `/billing/purchases` |
| POST | `/billing/admin/credits/adjust` | kept (support tool), re-pointed at the window |
| GET/POST | `/billing/referrals/*` | **rewritten** onto the points ledger (§6.9) |
| GET/POST | `/billing/ads/*` | **removed** (Decision O) |
| POST | `/webhooks/stripe`, `/paystack`, `/google-play/rtdn` | kept; **`/webhooks/apple` is new** |

Also unmounted: the `admin` router (`app.py:382-383`).

Untouched, per Decision F: `POST /billing/subscriptions/checkout` keeps accepting `circle_plan_monthly` and `plus_seat_add_on_monthly`, `models.SeatAddonPurchaseRequest`, `repository.py:292-298, 393-401`, all four Circle/seat settings in `config.py`, `TRIAL_DAYS_CIRCLE_PLAN`, and the `"plus_seat_add_on_monthly"` display name at `shared/infrastructure/email.py:540`.

Deleted with the retired *personal* tiers: `TRIAL_DAYS_STUDY_CIRCLE`, `TRIAL_DAYS_SQUAD` (done in Phase 1).

**Also deleted, revised in revision 4:** `STRIPE_PRICE_ID_STUDY_CIRCLE_MONTHLY` / `_YEARLY`, `STRIPE_PRICE_ID_SQUAD_MONTHLY` / `_YEARLY` (`config.py:198-203`) and `PAYSTACK_PLAN_STUDY_CIRCLE_*` / `_SQUAD_*` (`config.py:248-251`). Revision 3 kept all eight so that Phase 8 could identify a grandfathered subscriber's source tier from an incoming webhook. With no subscribers on those tiers, no such webhook can arrive, and eight empty-string settings whose only purpose is to decode events that will never be received are exactly the kind of workaround this revision removes. `_price_id_to_tier` and `_assert_price_id_is_active` (`stripe_service.py:214-266`) lose their Study Circle and Squad branches with them; `DEPRECATED_PLAN_IDS` keeps the six ids, because a learner or a stale client presenting a retired plan id still deserves `410` rather than `422` (Phase 1 established this and it does not change).

### 5.2 What is actually enforced today, by surface

Read this before deciding what a pass should unlock. The response convention, stated at `progress/services/goal_insight_service.py:8-11` and `maigie-mobile/src/utils/api.ts:121-128`, is: **locked reads answer `200` with a `LockedNotice`; locked mutations answer `403` with an `UpgradeRequiredDetail`.**

| Surface | Gate | Site | Free gets | Shape |
| --- | --- | --- | --- | --- |
| Prepare | `PAST_PAPER_SIM`, `ADAPTIVE` quiz modes | `quiz_engine.py:132-150` | 3 of 5 modes | **403** |
| Prepare | adaptive study plan scheduler | `study_plan_service.py:69-72, 166-184` → `prep_plan_adaptive.py` | even distribution | **200, different plan** |
| Prepare | *nothing else* | — | unlimited practice | — |
| Reflect | growth trend range (30d/90d) | `growth_service.py:71-95, 159-165` | 7d only; longer range returns empty series | **200 + notice** |
| Reflect | growth narrative, drivers | `growth_service.py:398-410, 447-455`, `narrative_cache.py:78-100` | every figure, no prose | **200 + notice** |
| Reflect | subject insight | `growth_service.py:~470` | figures only | **200 + notice** |
| Reflect | monthly reflection | `reflection_service.py:362-380` | weekly only | **403** |
| Reflect | reflection depth | `reflection_service.py:173-174` | shallower prompt | **200, silent** |
| Reflect | behaviour profile depth | `behaviour_service.py:41-45` | basic profile | **200, silent** |
| Reflect | value summary | `personal_learning/routes.py:2694-2698` | nothing | **403, untyped string** |
| Goals | goal insight prose | `goal_insight_service.py:335-346` | numbers, no panel | **200 + notice** |
| Learn | 2 courses/month | `knowledge/services/course_service.py:223-260` | 2 | **403** |
| Learn | DOCX/PPTX, report/minimal styles | `document_impl.py:1187-1195` | pdf + academic | **403** |
| Learn | 5 vs 10 flashcards/note, card types | `flashcard_service.py:180-186` | 5, basic Q&A | **200, silent** |
| Voice | credits only | `study_voice/routes.py:363-366` | full feature | `402`-style |
| Voice | billing mode | `study_voice/billing.py:34-39` | **billed for silence**; paid billed for audio only | silent |
| Ask | 30 turns / 60s, tier-blind | `ask_service.py:582-584, 605-660` | same as paid | **429** |
| Ask | credit caps | `ask_service.py:882-924` | 5k/day, 15k/month | in-band refusal |
| Everywhere | LLM model allowlist | `feature_flags.py:455-501`, `config.py:310-315` | flash-lite | silent |

`check_capability` — the function the whole matrix is built around — is called from **exactly two places**: `quiz_engine.py` and `document_impl.py`. Everything else reads `get_quality_tier` and degrades silently.

### 5.3 What the marketing says is premium

`maigie-public/src/components/pricing/plan-data.ts:110-127` is the comparison matrix on the pricing page. Cross-referenced against §5.2:

| Marketed row | Free | Plus | Enforced? |
| --- | --- | --- | --- |
| AI chat | 25k/day, 75k/mo | Unlimited | **numbers are wrong** — code says 5k/day, 15k/mo, and Plus is 300k/mo, not unlimited |
| AI courses & goals | up to 2 | Unlimited | yes, `course_service.py:223-260` |
| File uploads | 5/month | Unlimited | **no** — see §5.4 |
| AI summaries | 10/month | Unlimited | **no** — see §5.4 |
| Study plan creation | Basic | Advanced | yes, silently |
| 15-minute schedule reminders | ✗ | ✓ | **no** |
| Advanced scheduling & insights | ✗ | ✓ | **no** |
| AI Voice Assistant | ✗ | ✓ | **no** — free gets the full feature |
| Live voice tutor | ✗ | ✓ | **no** — free gets the full feature |
| Exam Prep mode | ✗ | ✓ | **no** — free gets Prepare entirely |
| Create Circles / chat groups / Group sessions | 1 group / up to 3 | "10 with Circle Plan" / "unlimited with Circle Plan" | **out of scope** — space-scoped, not audited, not changed |
| Support | Community | Priority | out of code's hands |

`maigie-public/src/content/faq/pricing-and-plans.yaml` is worse: it still sells **Study Circle $9.99** and **Squad $14.99**, both already retired in code at `stripe_service.py:53-79`.

Five of eleven marketed differentiators are not implemented, and three of them (voice assistant, voice tutor, Exam Prep) are the headline items on the pricing card.

### 5.4 The drift list

Every item here is either enforced in Phase 5 or deleted from the copy in Phase 7. Nothing is carried forward.

1. **File uploads, 5/month.** Columns `fileUploadsCount` / `fileUploadsPeriodStart` exist (`identity/db_models.py:109-114`). Nothing reads them, nothing increments them, no limit constant exists. → **enforce**.
2. **AI summaries, 10/month.** `summaryGenerationsCount` / `summaryGenerationsPeriodStart` (`identity/db_models.py:115-120`). Same. → **enforce**.
3. **`predictive_scheduling`, `optimal_time_suggestions`, `dropout_prevention`.** In `PLUS_ONLY_CAPABILITIES` (`feature_tier_service.py:125-131`) and sold by the `upgrade_value` string at `:119`. Never passed to `check_capability`. No feature behind any of them. → **delete from copy**; the honest Plus claim here is the deeper behaviour profile, which is real.
4. **15-minute reminders / advanced scheduling.** No gate anywhere. Notification cadence is a tier-independent daily allowance (`goal_lifecycle_service.py:102-106, 354-357`) with a `time_critical` bypass that is not a paid feature. → **enforce** the lead-time gate (it is one comparison), **delete** "advanced scheduling & insights".
5. **AI Voice Assistant / Live voice tutor as Plus-only.** `study_voice` contains no `check_capability`, no `require_premium`, no tier read except `billing_mode_for_tier`. Free gets the whole feature. → **delete the ✗**; the real difference is wall-clock versus audio-only billing plus the window allowance, which is worth stating plainly because it is genuine and defensible.
6. **Exam Prep mode as Plus-only.** Prepare is entirely free apart from two quiz modes. → **delete the ✗**, replace with "3 of 5 practice modes".
7. **"Unlimited" AI.** Plus was 300 000 credits/month and is now a window allowance. → **stop saying unlimited**; state the window and the allowance, which §6.2 makes possible to state honestly for the first time.
8. ✅ **Closed in Phase 2 — deleted.** **`require_premium` / `PremiumUser`.** A gate with no callers. → **delete**, replaced by Decision B.
9. **`check_capability("study_plan", "adaptive")`.** The branch exists at `feature_tier_service.py:322-333`; nothing calls it. → **wire it** so a Free learner asking for an adaptive plan gets a truthful `200 + notice` instead of a silently different plan.
10. ✅ **Closed in Phase 2.** **`STUDY_CIRCLE_*` / `SQUAD_*` tiers resolve to `"free"`.** `startswith("PREMIUM")` at `feature_tier_service.py:216`. These are personal `User.tier` values, so this is in scope. → **revised in revision 4: fixed by deleting the four tier values, not by admitting them.** Revision 3 fixed this with a `LEGACY_PLUS_TIERS` frozenset so grandfathered subscribers kept what they paid for until Phase 8 moved them. There are no subscribers on these tiers, so the bug has no victim and the fix has no beneficiary — Decision B resolves them to `free`, which is now the correct answer rather than a bug. Precondition checked in Phase 2b.
11. ✅ **Closed in Phase 2.** **Trials invisible to the LLM router.** `feature_flags.py:455-501`. → fixed by Decision B.
12. **Value summary is the only outright-locked read** (`routes.py:2694-2698`), with a plain-string `403`. It contradicts both the "no feature is entirely locked" principle at `feature_tier_service.py:6-9` and the locked-read convention. → **convert to `200 + LockedNotice`**.
13. **`progress.daily_credit_reset` is registered and never scheduled** (`workers/progress_tasks.py:182-185`). → **delete it.** §6.2 removes the daily counter it was written to reset, and `billing.reset_credit_periods` (`workers/billing_tasks.py:15`) goes with it for the same reason. The pass sweep (Decision E) replaces both.
14. **`WEB/features/classrooms` is mock end to end**, with no entitlement check anywhere in it. Noted, not fixed — classrooms are not a commercial surface yet. (`MOB/src/app/circles/settings.tsx:430`, a hardcoded `Upgrade — $14.99/mo` button, is space-scoped and out of scope per Decision F.)
15. **`WEB/pages/settings/UsageSettings.tsx` is a live, routed settings tab showing entirely invented usage.** `:25-58` is a `USAGE_DATA` literal — `creditsUsed: 62 400`, `hardCap: 80 000`, `creditsUsedToday: 3 200`, `dailyLimit: 5 000`, `tier: 'FREE'`, a 14-day synthetic history — with the comment "Static dummy data — always rendered", and `:61` assigns it directly with no query. It renders "Limit reached", "62,400 / 80,000 tokens", "17,600 remaining", a daily-limit bar, and a "Start free trial" CTA promising "4x more tokens and no daily limits". A learner reading this tab is being told a number about their own account that is fiction. It is reachable from `SettingsPage.tsx:19, 29`. → **rewrite onto `GET /billing/usage`** (percentage + reset time, §6.3), or delete the tab until it can tell the truth.
16. **The document studio is the one hard-gated feature with no gate in the UI at all.** `WEB/features/documents/pages/DocumentsPage.tsx:68` lists `pdf` / `docx` / `pptx` as a flat `FORMATS` array — no `requiresPlus`, no lock, no badge — and `:339-360` renders them as plain buttons. Then `:267-269` calls `getApiError(...)` and `:292` renders the result as a generic amber banner, **never reading `.upgrade`**, even though `lib/apiError.ts:88` has already extracted it. So the Plus format 403 from `document_impl.py:1187-1195` degrades to an anonymous error. `CourseCreatePage.tsx:445` and `PreparePracticePage.tsx:835` both render `UpgradeRequiredPanel` correctly from the same helper — documents is the outlier. → fix in Phase 7 alongside Decision J.
17. **There is a fifth hand-rolled locked card.** `WEB/features/surfaces/HomeSurface.tsx:1570-1595` duplicates the same markup as the four in §5.5 (`driversLocked.upgradeValue`, `navigate(driversLocked.upgradeUrl || '/subscription')`, button flipping on `trialAvailable`). Five copies, not four. Also at `:453, 1047-1052` a `TrialBanner` whose dismissal is component-local state defaulting to `true`, so it returns on every reload — a learner cannot dismiss it, which is the opposite of what Decision N requires.
18. **Ask Maigie's credit UI is real and server-driven, but reports a billing state as a generic error.** `maigieStore.ts:616-641` handles `credit_limit_error` by writing to the same `error` slot as a network failure, so the red bubble at `MaigieConversation.tsx:532-541` is visually indistinguishable from a dropped connection. It does store `failureCode: 'CREDIT_LIMIT'` — and nothing in the UI branches on it. There is no upgrade CTA and no reset time. Separately, `:602-614` drops a `credit_info` frame entirely unless `warning` or `notice` is set, so `purchasedCreditsRemaining` never reaches the screen. → under §6.3 these frames carry `windowResetsAt`, and the limit state needs its own rendering with the reset time and, per Decision J, an activation action when a pass is owned.
19. **Study Mode maps a payment failure to a bare sentence.** `WEB/features/courses/components/StudyMode.tsx:69` turns HTTP `402` on the diagram endpoint into `'Not enough credits to generate a diagram.'`, and `:885-886` turns the live-session `credit_limit_error` frame into `'Usage limit reached.'` — both into the generic error banner with no CTA. The voice study experience has no Plus indication of any kind, which matches drift item 5: free gets the whole feature.
20. **Six unrouted credit pages with working API clients, and live links pointing at them.** `CreditPacksPage`, `CreditPurchaseHistoryPage`, `CreditPurchaseSuccessPage`, `EarnPage`, `EarnContributePage`, `EarnReferralsPage` are all unreferenced by `app/app.tsx`, which routes only `/subscription*`. Meanwhile the live `SubscriptionPage.tsx:149, 159, 168` links to `/credits/history` and `/credits/buy` — dead routes — and prints a hardcoded `From $1.99 · Never expires` plus `purchasedCreditsBalance: 250` at `:23`. `EarnPage.tsx:154` also hardcodes `Starting at $1.99`, and `UploadResourceModal.tsx:298` hardcodes "Earn 1,500 credits when your resource is approved!". → all deleted in Phase 7 with the rest of the credit-pack surface.
21. **Four mocked upsell cards sit inside otherwise server-driven pages.** `CommercialConversionCard` is mounted at `StudyPlanCreatePage.tsx:54, 416` (`adaptive-plan-ready`), `WeeklyGrowthReflectionPage.tsx:25, 334` (`weekly-reflection-value`), `GrowthTrendsPage.tsx:24, 201` (`growth-history-value`) and `DocumentsPage.tsx:38, 332` (`document-creation-momentum`), all backed by `commercial/mocks/conversionMomentsMock.ts`. `SidebarCommercialExperience` is mounted globally at `LearningLayout.tsx:23, 441`, on every learning route. These are exactly the moments Decision N wants to be real — the mount points are already in the right places, and the content behind them is invented. → Phase 6 replaces the mock with `conversion_engine` output rather than removing the cards.
22. **`WEB/pages/settings/AiModelSettings.tsx:13-25` lets a learner pick a model from a hardcoded list**, while the backend allowlists models by tier (`config.py:310-315`, `feature_flags.is_model_allowed`). Nothing reconciles the two, so the picker can offer a model the server will refuse. Small, but it is a tier gate with a UI that does not know it exists.

### 5.5 Clients

**Web** (`maigie-client/apps/web`). Server-authoritative gating, presentation-only client — the right shape. `lib/apiError.ts` extracts `UpgradeRequiredDetail` from the `403`; `features/commercial/components/UpgradeRequiredPanel.tsx` renders the server's `reason` and `upgradeValue` verbatim to avoid drift. But the locked panels are **five hand-rolled copies** of the same upsell card (`ReflectSurface.tsx:252-261`, `GrowthTrendsPage.tsx:104-140`, `SubjectActivityDetailPage.tsx:208-211`, `ReflectGoalDetailPage.tsx:177-180`, `HomeSurface.tsx:1570-1595`) rather than one component. `features/credits/` and six credit/earn pages are dead on arrival — and `SubscriptionPage.tsx:149-168` links to two of the dead routes. `pages/SubscriptionPage.tsx:18` hardcodes `tierLabel = 'Maigie Plus Monthly'` and `:182` hardcodes `currentTier="PREMIUM_MONTHLY"` — the page cannot show a learner any other state.

A second sweep of the personal-learning surfaces found four things the first pass missed, recorded as drift items 15–22: a **live settings tab showing fabricated token balances**, the **document studio having no gate in its UI at all** despite being hard-gated on the server, **Ask Maigie reporting a billing limit as a generic error**, and **four mocked upsell cards already mounted at exactly the moments Decision N wants to make real**. Surfaces confirmed clean: lessons, flashcards, notes, collections, resources, schedule, exam-prep, onboarding, discovery, learn, notifications, knowledge base, course analytics, and the Ask Maigie history panel and overlays.

**Mobile** (`maigie-mobile`). Expo SDK 55, `react-native-iap ^15.3.1` already installed and plugin-registered, `com.android.vending.BILLING` declared. `src/hooks/usePlayBilling.ts` is a complete Play Billing implementation (357 lines) — and `Platform.OS !== 'android'` early-returns at `:88-93`, before `initConnection`, so **iOS has no purchase path at all**. The iOS jobs in `.eas/workflows/deploy-production.yml:72-108` are commented out and `eas.json` has no `submit.production.ios` block. **Correction to revision 3, which said there is no `ios/` directory:** there is one — a gitignored Expo prebuild with `Maigie.xcworkspace` and installed Pods, including `react-native-iap`'s `NitroIap`. The iOS native integration is done; the JavaScript refuses the platform anyway. See §5.6. `MOB/src/app/prepare/launch.tsx:90` is the only client anywhere that pre-empts a gate rather than waiting for the `403`.

**Public site** (`maigie-public`). `plan-data.ts` is the catalogue; `CreditPacks.tsx` and `landing/Pricing.tsx` each duplicate the credit-pack prices independently. `CircleProductsSection.tsx` is space-scoped and untouched.

**Price literals: at minimum 9 places per price change, 12+ per SKU change**, with no shared source. `WEB/features/subscription/data/plan-data.ts:50-56`, `MOB/src/screens/SubscriptionScreen.tsx:72-79`, and `stripe_service.py:113-155` are three hand-maintained copies of the same table.

### 5.6 Store configuration

| | Google Play | App Store |
| --- | --- | --- |
| Console | live, `submit.production.android.track: production` | **no app record** |
| Bundle id | `com.maigie` | `com.maigie` (`app.config.js:32`), builds locally, never submitted |
| Products | subscription `maigie_plus` with base plans `plus-monthly` / `plus-yearly`; consumables `credit_pack_starter/value/power` | none |
| Server verification | `google_play_service.py`, service account, `purchases.subscriptions.get` | none |
| Server notifications | RTDN endpoint written (`webhooks.py`) | none |
| EAS submit config | `eas.json` `submit.production.android.track: production` | **absent** — no `appleId`, `ascAppId` or `appleTeamId` |

**iOS is less greenfield than revision 3 claimed, and the correction changes the estimate.** Revision 3 said "there is no `ios/` directory". There is: `expo prebuild` has been run and `ios/` contains `Maigie.xcodeproj`, `Maigie.xcworkspace`, a `Podfile.lock` and installed `Pods`. It is **gitignored** (`.gitignore:12`, alongside `android/` at `:11`), which is the normal Expo CBA arrangement and the reason a source-only reading missed it — the directory is a build artifact regenerated from `app.config.js`, not committed state.

More usefully: **`react-native-iap`'s iOS pod is already linked.** `ios/Podfile.lock` carries `NitroIap` from `../node_modules/react-native-iap`, so the StoreKit native module is present and building. The iOS purchase gap is JavaScript and console configuration, not native integration — `usePlayBilling.ts:88-93` early-returns on `Platform.OS !== 'android'` before `initConnection`, so the hook refuses the platform its own dependency already supports.

Two further corrections to revision 3's Phase 7 checklist:

- **`expo prebuild` is not a task.** It has been run and it will be re-run by EAS on every build. What is actually missing from version control is nothing; what is missing from EAS is the `submit.production.ios` block.
- **"StoreKit capability" is not an entitlements change.** `ios/Maigie/Maigie.entitlements` carries `aps-environment` and `applinks:app.maigie.com`, and correctly carries nothing for in-app purchase: In-App Purchase is enabled on the **App ID in the Apple Developer portal**, and modern StoreKit adds no entitlement key. The checklist item was asking for a file edit that would be wrong to make. What is required is the App ID capability plus the App Store Connect records in §5.7.

What genuinely does not exist for iOS: the App Store Connect app record, the three in-app purchase products, the App Store Server API key (`APPLE_ISSUER_ID` / `APPLE_KEY_ID` / `APPLE_PRIVATE_KEY`), the `POST /webhooks/apple` endpoint and its JWS verification, the StoreKit branch of the purchase hook, and the EAS iOS submit configuration. That is Phase 5 and §5.7, and none of it is deferred.

### 5.7 Store product creation: the longest lead time in the plan

The question "at what point do we create the Play and App Store products" was not answered in one place in revision 3 — Google Play products sat in Phase 5 and Apple products sat in a Phase 7 mobile bullet, which is both a split ownership and the wrong order, because the store consoles are the only part of this plan with an external dependency measured in days.

**Answer: create every store product at the start of Phase 5, and create the App Store Connect app record before that — it gates everything else on iOS and it costs nothing to do early.**

The dependency that decides the order is this: an in-app purchase product cannot be submitted for review on its own. Apple reviews IAP products **attached to a build**, and the first submission of a paid app with IAP is the single most rejection-prone submission a project makes. Google is more forgiving — Play in-app products go live from the console without review — but a Play subscription still needs an **active** app with a published build on some track before purchases can be tested, and Play's 7-day price-change notice applies later.

| When | Google Play | App Store Connect | Blocks |
| --- | --- | --- | --- |
| **Now, in parallel with Phase 2b** | — | Create the app record for `com.maigie`; enable In-App Purchase on the App ID; generate the App Store Server API key; complete the Paid Apps agreement and banking/tax | Everything Apple. The agreement in particular: **no IAP product can even be created until Paid Apps is active**, and it involves banking details that are not an engineer's to supply |
| **Phase 5, first task** | Create `plus_pass_5h` and `plus_pass_7d` as **consumable** in-app products; set the `plus-monthly` base plan free trial to **3 days**; set NGN prices per §6.8; **delete `plus-yearly` and the three `credit_pack_*` products** | Create `com.maigie.plus.pass5h` and `com.maigie.plus.pass7d` as **Consumable**; create `com.maigie.plus.monthly` in subscription group `maigie_plus` with a **3-day introductory free trial**; set NGN prices per §6.8 | Server verification work has nothing to verify against until the ids exist |
| **Phase 5, after the products exist** | `purchases.products.get` verification; RTDN voided-purchase revocation | `apple_service.py`, JWS verification, `POST /webhooks/apple` | Phase 7's client purchase flows |
| **Phase 7, iOS** | — | Uncomment the iOS EAS jobs; add `submit.production.ios` (`appleId`, `ascAppId`, `appleTeamId`); submit the first build **with the three IAP products attached** | App Review, 1–2 weeks, first-submission rejection risk |

**Sandbox testing does not wait for review.** Both stores let you test purchases against products in a pre-approved state — Play from an internal-testing track, Apple with a StoreKit configuration file or a Sandbox Apple Account — so Phase 5's verification code and Phase 7's purchase flows can be built and tested end to end while the products are still unreviewed. Nothing in the engineering plan is blocked by review; only the public launch is.

**Deleting rather than repurposing is the rule here too.** The live Play catalogue holds a `plus-yearly` base plan and three `credit_pack_*` consumables. Revision 3 kept `GOOGLE_PLAY_BASE_PLAN_YEARLY` and the three `GOOGLE_PLAY_SKU_CREDIT_*` settings (`config.py:264-268`) so historical RTDN events could still be decoded, and `google_play_service.py:65, 191-193` reads all four. With nobody having bought any of them, there is no history: the four settings, both `google_play_service` branches, and the four store products are deleted. A new `plus_pass_5h` is created as a new consumable — no existing SKU is renamed into the role, because a renamed SKU carries its old purchase history and its old type, and a non-consumable repurposed as a pass is unbuyable twice.

## 6. The new model

### 6.1 The catalogue

Four **personal** products. Two are **consumable, non-renewing products**. One is a subscription. One is Free. The catalogue also keeps its two space-scoped entries (`circle_plan_monthly`, `plus_seat_add_on_monthly`) unchanged — six entries in total, and the `scope` field already on `PlanItem` is what separates them.

| Product id | Display | Type | USD | Grants |
| --- | --- | --- | --- | --- |
| `free` | Free | — | 0 | baseline capabilities, Free window allowance |
| `plus_pass_5h` | 5-Hour Plus Pass | **consumable product** | **0.99** | full Maigie Plus for 5 hours from activation, then nothing |
| `plus_pass_7d` | 7-Day Plus Pass | **consumable product** | **2.49** | full Maigie Plus for 7 days from activation, then nothing |
| `plus_monthly` | Maigie Plus | auto-renewing subscription | **4.99/mo** | full Maigie Plus while active, **3-day trial** on first purchase |

These are **US/UK list prices**. The launch market is Nigeria, where FX parity would price the product above Netflix Standard; §6.8 sets NGN independently at ₦700 / ₦1 800 / ₦2 400 and is the table that matters for launch.

**$4.99, not $5.00.** Identical revenue to the cent, better psychologically, an existing store price point, and — decisively — unchanged from today's price, so no subscriber ever sees a price-increase flow. A $0.01 rise would require a Stripe price migration, a mandatory 7-day Google Play notice, and on Apple an **explicit consent prompt where non-responders are cancelled at renewal**. Real churn risk for one cent of nothing.

**The trial is 3 days, not 7.** A free 7-day trial sitting beside a $2.49 7-day pass is the same product at two prices, and the one that costs money looks like a trick to anyone who remembers the free one. Three days separates them cleanly: the trial is a look, the pass is a study week. It costs nothing to shorten because **no trial has ever converted to a paying subscriber** — there has never been a reachable checkout to convert into. Three days is also long enough to be honest at a 5-hour window: ~14 windows, several study sessions, every Plus capability.

The number lives in `config.TRIAL_DAYS_MAIGIE_PLUS` (currently `7`) **and in three store configurations that the server does not control** — the Stripe price's `trial_period_days`, the App Store Connect introductory offer, and the Play Console base-plan free-trial period. All four must agree, and the two store values are set by hand in a console. `TRIAL_DAYS_CIRCLE_PLAN` stays at 7; it is space-scoped (Decision F).

A pass is a product. It does not renew, it cannot be cancelled, there is no billing relationship to manage, and it has no grace period. It is bought, it is held, it is activated, it runs out. That is the entire lifecycle, and it is the reason passes are cheap: nothing about them has to be serviced.

**Passes grant every Plus capability, in the learner's personal workspace.** Every entry in `FEATURE_TIER_MATRIX["*"]["plus"]`, `get_quality_tier() == "plus"`, the Plus LLM allowlist, audio-only voice billing. For the duration, a pass holder is indistinguishable from a subscriber to every personal-scope *capability* gate in the codebase. That is what Decision B buys. A pass does not grant a Plus seat in a Space; Decision F says why and says what the copy must therefore avoid claiming.

**What a pass does not grant is a subscriber's usage allowance**, and §6.3 is where that is set. A 5-hour pass carries one window's worth of usage sized to what $0.99 can pay for — full capabilities, a bounded amount of the expensive ones. The marketing states the voice figure explicitly rather than implying five hours of tutor, because five hours of tutor costs $6.00 to serve and the pass nets $0.75.

Retired: `credit_pack_starter`, `credit_pack_value`, `credit_pack_power`, `plus_yearly` / `maigie_plus_yearly`, and the already-deprecated `study_circle_*` / `squad_*` personal tiers.

Not retired, not in scope: `circle_plan_monthly`, `plus_seat_add_on_monthly`.

Store product ids:

| Internal | Stripe | Apple | Google Play |
| --- | --- | --- | --- |
| `plus_pass_5h` | one-time price, `mode: payment` | `com.maigie.plus.pass5h` (**consumable**) | `plus_pass_5h` (in-app, **consumable**) |
| `plus_pass_7d` | one-time price, `mode: payment` | `com.maigie.plus.pass7d` (**consumable**) | `plus_pass_7d` (in-app, **consumable**) |
| `plus_monthly` | recurring price, `mode: subscription` | `com.maigie.plus.monthly` (auto-renewable, group `maigie_plus`) | `maigie_plus` / base plan `plus-monthly` |

Consumable is the correct store type and not a detail: a non-consumable would be permanently owned, restorable forever, and unbuyable a second time — which is the opposite of a pass.

**Revised in revision 4:** `GOOGLE_PLAY_BASE_PLAN_YEARLY` and the three `GOOGLE_PLAY_SKU_CREDIT_*` settings (`config.py:264-268`) are **deleted**, along with the branches that read them at `google_play_service.py:65` and `:191-193`, and the four corresponding Play products are deleted from the console. Revision 3 kept them for historical RTDN lookups by analogy with the deprecated Stripe price ids; with zero purchases behind them there is no history to look up, and an RTDN for a product nobody owns cannot arrive. The deprecated Stripe price ids go the same way for the same reason (§5.1). Every one of the six store products in the table above is **created new**, and none is a rename of an existing SKU — a renamed SKU keeps its purchase history and its store type, and repurposing a non-consumable as a pass would produce a pass that can only ever be bought once.

### 6.2 The unit is cost, not tokens

**A credit today is a token, and that is why voice is mispriced by two orders of magnitude.**

`GEMINI_LIVE_CREDITS_PER_MINUTE = 100` (`config.py:315`) → 20 charged credits per voice minute after `TOKEN_MULTIPLIER`. A voice minute is therefore billed as if it were 100 tokens of text.

What a voice minute actually costs. The configured model is `models/gemini-3.1-flash-live-preview`. [Published measurement puts Gemini 3.1 Flash Live at roughly $0.005/minute audio in and $0.018/minute audio out](https://rywalker.com/research/gemini-live-api), which cross-checks against the native-audio token rates ($3/1M audio-video in, $12/1M audio out at ~1 500 audio tokens per minute per direction). Working figure: **$0.02 per conversational minute.** Text on Flash-Lite runs about $0.0005 per 1 000 raw tokens. *Content was rephrased for compliance with licensing restrictions.*

So one voice minute costs what ~40 000 raw text tokens cost, and is billed as 100. **Voice is under-priced by roughly 100–400× depending on the comparison model.** `study_voice/billing.py:21-23` already flagged this as "a pricing question flagged in the design document, not a bug here." This is that answer.

**The live consequence, which is the most expensive thing in this audit.** Free tier is 5 000 charged credits/day and `study_voice` has no tier gate at all (drift item 5). 5 000 ÷ 20 = **250 minutes of Gemini Live per day for any free user — about $5.00/day, $150/month, at zero revenue.** This is in production now. It is the reason Phase 3 leads the plan rather than following it.

**The fix is to stop denominating in tokens.** One `usage_unit` = **$0.0001 of measured COGS** — one hundredth of a cent, so 10 000 units = $1.00 of cost. Every operation deducts its true measured cost, which the codebase can already compute (`cost_calculator.py`, `LlmCostRecord`). `TOKEN_MULTIPLIER` is deleted: it existed to make a token count feel generous, and a cost unit does not need flattering.

Reference operation prices at current model rates:

| Operation | Model | ≈ COGS | Units |
| --- | --- | --- | --- |
| Chat turn, Free (8k in / 600 out) | Flash-Lite $0.25/$1.50 per M | $0.0029 | **30** |
| Chat turn, Plus (8k in / 600 out) | 3.5 Flash $1.50/$9.00 per M | $0.0174 | **175** |
| Live voice, per minute | 3.1 Flash Live | $0.0200 | **200** |
| Course generation, Plus (20k in / 8k out) | 3.5 Flash | $0.1020 | **1 020** |
| Document / quiz / flashcard generation | varies | $0.01–0.10 | **100–1 000** |

The same operation costs different units on different tiers, because the tier picks the model (`config.py:310-315`). That is correct and self-balancing rather than a wrinkle to hide: a Plus learner gets a larger allowance *and* a more expensive model, and the ratio between them is the real margin.

### 6.3 Allowances: a visible 5-hour window, an invisible monthly backstop

A **5-hour tumbling window**, started by the learner's first billable operation and reset the moment one occurs after the window has elapsed.

| | Window (5h) | Monthly backstop | ≈ chat turns / window | ≈ voice minutes / window | Window COGS |
| --- | --- | --- | --- | --- | --- |
| Free | **500 units** | 5 000 | ~16 | 2.5 | $0.05 |
| Plus — subscription or trial | **4 000 units** | 30 000 | ~23 | 20 | $0.40 |
| 5-Hour Pass | **3 000 units** (one window) | — | ~17 | 15 | $0.30 |
| 7-Day Pass | 4 000 units/window | **10 000 total** | ~57 total | 50 total | $1.00 total |

**Why there is a monthly backstop when §1 says there is no monthly limit.** A 5-hour tumbling window permits up to 4.8 windows/day, so monthly exposure is 144× the window allowance. No window number is simultaneously generous enough for one session and bounded enough for a month. Claude — the reference implementation — shipped 5-hour windows and then **added weekly limits in 2025** for exactly this reason.

The resolution is that the backstop is not a product limit, it is an abuse limit. It is set at ~7.5 Plus windows/month, which is far above what any learner reaches by studying: 2 windows/day for 20 days is 40 windows, but a *typical* window consumes well under its allowance, so the backstop binds only on sustained maximal draw. It is not shown in the UI, not in the marketing, and not in `GET /billing/usage` until a learner is within 20% of it. Experientially there is no monthly limit. Financially there is a bound.

**Why Free gets more chat turns than Plus.** 500 units buys ~16 Flash-Lite turns; 4 000 buys ~23 3.5-Flash turns. Free is not starved of conversation — it is starved of *voice* (2.5 minutes) and of *model quality*. That is the honest shape of the paywall, it matches what actually costs money, and it means the free tier stays useful enough to convert. The old design had this exactly backwards: free got 250 voice-minutes/day and 3–5 chat turns.

**Three properties make the window worth the migration:**

- **It is explainable in one sentence with a number in it.** "You've used this session's allowance. It resets at 3:40 PM." `window_started_at + 5h` is a real timestamp, available before the limit is reached.
- **Running out is never worse than five hours.** Today's failure is a learner exhausted on the 9th with three weeks of nothing and no reachable way to buy more.
- **The 5-hour pass is one Plus session, priced as one.** Activating a pass **starts a fresh window** (Decision E), so the promise holds regardless of what the learner did ten minutes earlier.

**What the UI shows: a percentage and a reset time, never a unit count.** Units are a COGS accounting device and mean nothing to a learner; a raw number invites exactly the arithmetic we do not want them doing. Marketing states the concrete equivalents instead — "5 hours of full Plus, including about 15 minutes of live voice tutoring" — which is checkable and true.

**What goes away.** `creditsHardCap`, `creditsSoftCap`, `creditsPeriodStart`, `creditsPeriodEnd`, `creditsUsed`, `creditsDailyLimit`, `creditsUsedToday`, `lastDailyReset`, `CREDIT_LIMITS`, `TOKEN_MULTIPLIER`, `CREDIT_COSTS` as a fixed table, `billing.reset_credit_periods`, `progress.daily_credit_reset`, the 80%-of-month soft warning, and the branch at `check_credit_availability:378-400` reconciling a daily cap against a monthly cap against a purchased balance. **There are no paid users, so none of this needs a migration path** — it needs deleting.

A soft warning fires at 80% of the window allowance, carrying the reset timestamp. `purchasedCreditsBalance` is dropped outright rather than sunset, for the same reason.

**Nothing tops up a window.** `referral_rewards_service.get_daily_limit_increase` and `credit_service.claim_ad_reward` both granted a *daily limit increase*, and neither survives. An earned allowance bump is invisible — the learner cannot see it, cannot predict it, and cannot plan a study session around it — so it buys us no advocacy and costs us real inference. Earning instead produces **points**, and points buy **passes**, which are a thing a learner can hold, see and decide when to spend (§6.9, Decision O). A pass also starts a fresh window, so a redeemed reward lands as a clean five hours of Plus rather than a few more turns in a window that was already half spent.

### 6.4 Unit economics

Net revenue per sale, after store or processor cut. Apple and Google both take **15%** under their small-business programmes, which Maigie qualifies for; 30% is shown as the pessimistic case.

| Product | List | Web (Stripe) | Store 15% | Store 30% | Blended assumption |
| --- | --- | --- | --- | --- | --- |
| 5-Hour Pass | $0.99 | $0.66 | $0.84 | $0.69 | **$0.75** |
| 7-Day Pass | $2.49 | $2.12 | $2.12 | $1.74 | **$2.05** |
| Plus Monthly | $4.99 | $4.55 | $4.24 | $3.49 | **$4.00** |

Note the inversion: at $0.99 Stripe's 30¢ fixed fee makes **web the worst channel**, while at $4.99 web is the best. The $0.99 pass loses nothing to store distribution.

Margin at the allowance ceiling, which is the worst case rather than the expected case:

| Product | Net | Max COGS | Floor margin | Typical COGS | Typical margin |
| --- | --- | --- | --- | --- | --- |
| 5-Hour Pass | $0.75 | $0.30 | 60% | $0.20 | 73% |
| 7-Day Pass | $2.05 | $1.00 | 51% | $0.70 | 66% |
| Plus Monthly | $4.00 | $3.00 | 25% | $1.80 | 55% |
| Free | $0.00 | $0.50 | — | $0.18 | — |

The ladder is coherent: **$0.00031/unit on the 5-hour pass, $0.00021 on the 7-day, $0.00013 on monthly.** Impulse buys cost more per unit, the subscription is the value choice, and that ordering matches the per-day price ladder ($4.75/day, $0.36/day, $0.166/day). There is no arbitrage — three 7-day passes ($7.47) already cost more than a month.

### 6.5 The whole AI cost surface — five operations are metered, thirty-one are not

Chat, live voice, study diagrams, voice notes and note merge consume credits. **Nothing else in the product does.** `CREDIT_COSTS` also declares `ai_course_generation: 250` and `ai_action: 100`, and there is no `consume_credits` call for either — both entries are dead, so course generation and note AI actions are free today.

Everything below is a real LLM call that nobody pays for.

**User-triggered, unmetered.** Cost estimates use $1.50/$9.00 per 1M for `gemini-3.5-flash`; **output tokens cost 6× input**, which is why the `max_tokens` column is the one that matters.

| Operation | Call site | `max_tokens` | ≈ COGS | Units |
| --- | --- | --- | --- | --- |
| Quiz / question generation | `quiz_engine.py:452` | **8 000** | $0.078 | 780 |
| Lesson body (`type=explain`) | `knowledge/routes.py:1145` | **8 192** | $0.078 | 780 |
| Growth narrative | `growth_service.py:414` → `narrative_cache.py:237` | **8 192** | $0.077 | 770 |
| Growth drivers | `growth_service.py:464` | **8 192** | $0.077 | 770 |
| Goal insight | `goal_insight_service.py:377` | **8 192** | $0.077 | 770 |
| Resource recommendations | `resource_service.py:283` **+** `:304` | 8 192 ×2 | $0.160 | 1 600 |
| Course outline | `knowledge/routes.py:82` | 4 096 | $0.038 | 380 |
| Prep topic extraction | `exam_prep_service.py:582` | 3 000 | $0.039 | 390 |
| Document generation | `document_impl.py:1242` | variable | $0.057 | 570 |
| Reflection (**two** calls) | `reflection_service.py:191` **+** `:306` | 2 000 ×2 | $0.045 | 450 |
| Note AI action | `note_service.py:412` | 3 000 | $0.030 | 300 |
| Study plan generation | `study_plan_service.py:1522` | 2 000 | $0.023 | 225 |
| Schedule regeneration | `schedule_regen_impl.py:213` | — | $0.023 | 225 |
| Plan generation (intelligence) | `planning_impl.py:55` | — | $0.023 | 225 |
| Flashcards — from note | `flashcard_service.py:207` | 2 000 | $0.020 | 200 |
| Flashcards — from topic | `flashcard_service.py:300` | 2 000 | $0.020 | 200 |
| Flashcards — from plan item | `flashcard_service.py:353` | 1 500 | $0.016 | 160 |
| Flashcards — deck starter | `flashcard_service.py:404` | 1 500 | $0.016 | 160 |
| Lesson quiz / summary | `knowledge/routes.py:1198` | 2 048 | $0.020 | 200 |
| Note summarise | `note_service.py:451` | 1 000 | $0.011 | 110 |
| **Home guidance / today's focus** | `guidance_engine.py:267` | 1 200 | $0.014 | 140 |
| Memory / fact extraction | `memory_impl.py:49` | ~600 | $0.010 | 100 |
| Conversation summarisation | `memory_impl.py:81` | ~600 | $0.010 | 100 |
| Onboarding auto-setup (**three** calls) | `auto_setup_service.py:163`, `:123`, `:219` | — | $0.080 | 800 |
| Discovery recommendations | `discovery_service.py:84` | 1 500 | $0.015 | 150 |
| Background course generation | `ai_course_generation.py:41` | 4 096 | $0.038 | 380 |

Deterministic, and therefore free to us as well as the learner: quiz grading, hints and scoring; readiness and adaptive question selection (`prep_readiness.py`, `prep_focus.py`); behaviour analytics (`behaviour_service.py` is statistical); notification copy (`schedule_reminders.py:18` explicitly records "No LLM-drafted copy"); goal nudges and lifecycle; value summary; milestones; retention. Also confirmed zero: **no ElevenLabs usage anywhere** (`integrations/elevenlabs/__init__.py` is a docstring, three config keys unused), no image or illustration generation (note and lesson "illustrations" are Mermaid text), and embeddings wired but unreachable (`gemini_embedding.py:104`, RAG deferred).

**Background, unmetered, and not requested by anyone.** This is the part the earlier revenue model missed entirely.

| Task | Schedule | Fan-out | Per active profile |
| --- | --- | --- | --- |
| `learning.generate_recommendations` (`tasks/recommendations.py:18`) | **daily 03:00** | every active profile, paged | $0.015/day = **$0.45/month** |
| `learning.generate_reflections` (`tasks/reflections.py:18`) | **Sundays 04:00** | every active profile, paged | $0.045/week = **$0.19/month** |

**$0.64/month per active profile, spent on learners who may not have opened the app.** That is more than three times the free-tier inference budget the previous model assumed for *everything*.

**Three cost amplifiers, all invisible today:**

1. **Retry fan-out.** `llm_resilient.generate_content` retries 3× per provider and then falls through `gemini → openai → anthropic` (`llm_resilient.py:200, 249-320`), treating an empty reply as a failure (`:290`). One logical operation can bill **up to nine provider calls**. Nothing counts them.
2. **`guidance_engine` has no cache.** `growth_service` and `goal_insight_service` go through `narrative_cache` and its `inputs_hash` fingerprint, so reopening a panel is free. Home guidance does not — it is **an LLM call on every home load** that gets past the deterministic ladder (`guidance_engine.py:231`). Five home opens a day is $2.10/month, which exceeds the entire margin on a Plus subscription.
3. **`max_tokens` looks like an unreviewed default.** Growth narrative, growth drivers and goal insight each budget **8 192 output tokens to write a paragraph**. At $9.00/1M output that is 89% of each operation's cost. Setting those three to 1 500 is a ~5× reduction on the most-opened panels in Reflect, and no learner can tell.

**Nothing outside chat is metered, and it cannot be without plumbing.** `LlmCostRecord` is written from exactly one place — `cost_tracker.record` at `router.py:308` — and `LLMRouter`'s only caller in the codebase is Ask/chat (`ask_service.py:2066`). Every operation in the tables above goes through `llm_resilient` or the raw `generate_content` helpers, **which discard the provider response and return text only**, so token counts do not reach the caller. Metering them is not a matter of adding a `consume_credits` call; usage metadata has to be plumbed through first (Decision L).

One more thing to verify before trusting any of these figures: `cost_calculator._EXACT_MODEL_PRICING:31-52` prices `gemini-3.5-flash` at **$0.50/$3.00** per 1M, where published rates are around **$1.50/$9.00**. One of the two is wrong. If the codebase table is right, every COGS figure here falls ~3× and the margins get much easier; if it is stale, the meter would under-charge by 3× from day one. **Check this first** — it is a five-minute task that moves every number in this document.

### 6.6 The gating and metering matrix

Two independent questions per operation, and the book (`business/ch36-pricing-philosophy`) settles the first one:

> Payment should expand capability, not unlock basic usefulness.
> If it does not strengthen learning, it should not exist simply to justify a higher price.

So **cost control is the window, not the gate.** An operation is gated only when Plus genuinely does something more; otherwise it is available to everyone and bounded by the allowance. That is also what `feature_tier_service.py:6-9` already claims, and §5.4 is the list of places the claim wasn't true.

| Operation | Free | Plus | Metered | Gate shape |
| --- | --- | --- | --- | --- |
| Chat / Ask | ✓ | ✓, better model | ✓ today | window only |
| Live voice | ✓ | ✓, audio-only billing | ✓ today | window only |
| Quiz generation | ✓ 3 modes | ✓ 5 modes | **add** | `403` on mode |
| Lesson generation | ✓ | ✓ | **add** | window only |
| Course creation | 2/month | unlimited | **add** | `403` on count |
| Course outline | ✓ | ✓ | **add** | window only |
| Prep topic extraction | ✓ | ✓ | **add** | window only |
| Study plan | even split | adaptive | **add** | `200 + notice` (drift 9) |
| Flashcards (×4) | 5/note, basic | 10/note, 4 types | **add** | silent depth |
| Documents | pdf + academic | +docx, pptx, 3 styles | **add** | `403` on format |
| Note AI action / summarise | ✓ | ✓, better model | **add** | window only |
| Note merge | ✓ | ✓ | ✓ today | window only |
| Reflection — weekly | ✓ summary | ✓ deep | **add** | silent depth |
| Reflection — monthly | ✗ | ✓ | **add** | `403` |
| Growth narrative / drivers | figures only | + prose | **add** | `200 + notice` |
| Growth trend range | 7d | 30d, 90d | n/a | `200 + notice` |
| Goal insight | numbers | + prose | **add** | `200 + notice` |
| Subject insight | figures | + prose | **add** | `200 + notice` |
| Resource recommendations | ✓ | ✓ | **add** — most expensive single op | window only |
| Home guidance | ✓ | ✓ | **add** + **cache** | window only |
| Memory extraction | ✓ | ✓ | **add** | never gated — Principle Two |
| Onboarding auto-setup | ✓ | ✓ | **exempt** | never gated or charged |
| Discovery recommendations | weekly, if active | nightly | **add** | cadence, not access |
| Weekly reflection task | deterministic, no LLM | LLM, nightly-eligible | **add** | cadence |
| Value summary | ✗ → `200 + notice` | ✓ | n/a — no LLM | drift 12 |
| Behaviour analytics | basic | deeper | n/a — no LLM | silent depth |
| Quiz grading / hints / scoring | ✓ | ✓ | n/a — no LLM | never gated |
| Notifications | ✓ | 15-min lead | n/a — no LLM | drift 4 |

Two operations are **never gated and never charged**, on principle rather than on cost. **Onboarding** (`auto_setup_service.py`, three calls, $0.08) is where the book's "free should create real success" is either honoured or not; charging a learner before they have learned anything is the one place a meter would be self-defeating. **Memory extraction** (`memory_impl.py:49`) is what makes Maigie feel like it knows you — Principle Two, AI is invisible — and a learner cannot be asked to pay for the product remembering them.

### 6.7 Revenue, with the whole cost surface in it

Assumptions, all of which are guesses and are the first thing to replace with measurement: payer rate **8% of MAU** (2.75% subscribe, 3.25% buy 7-day passes at 1.3/month, 2.0% buy 5-hour passes at 2.0/month); **50%** of non-paying MAU are AI-active in a given month; net revenue per §6.4.

Per-learner monthly COGS, before and after the §6.5 fixes:

| | Free — as-is | Free — fixed | Plus — as-is | Plus — fixed |
| --- | --- | --- | --- | --- |
| Metered usage (window) | $0.25 | $0.11 | $1.80 | $0.90 |
| Background tasks | $0.64 | $0.07 | $0.64 | $0.30 |
| Home guidance (uncached) | $0.70 | $0.02 | $0.70 | $0.02 |
| Unmetered generation | **unbounded** | in window | **unbounded** | in window |
| **Total** | **$1.59+** | **$0.20** | **$3.14+** | **$1.22** |

The "fixed" column assumes four changes, none of which a learner can perceive: meter every operation into the window (Decision L), gate background AI on engagement (Decision M), cache home guidance the way `narrative_cache` already caches narratives, and set the three 8 192-token narrative budgets to 1 500.

At 8% payer rate, 50% of free MAU AI-active:

| | 1 000 MAU | 10 000 MAU | 50 000 MAU |
| --- | --- | --- | --- |
| Subscribers | 28 | 275 | 1 375 |
| 7-day passes sold | 42 | 423 | 2 113 |
| 5-hour passes sold | 40 | 400 | 2 000 |
| **Net revenue** | **$227** | **$2 267** | **$11 335** |
| Paid COGS | $57 | $573 | $2 865 |
| Free COGS | $94 | $940 | $4 700 |
| **Contribution** | **$76** | **$754** | **$3 770** |
| Margin | 33% | 33% | 33% |
| Revenue per MAU | $0.227 | $0.227 | $0.227 |

**Without the §6.5 fixes the same model returns roughly −$270 at 10 000 MAU.** The fixes are not optimisations; they are the difference between a business and a subsidy.

**Free-tier inference is still the largest single line item** — $940 against $2 267 of revenue at 10 000 MAU. Levers, most powerful first:

| Lever | Contribution at 10 000 MAU |
| --- | --- |
| Baseline (fixed) | $754 |
| Audit `max_tokens` across all 26 operations | **$1 100–1 300** |
| Trim chat context 8 000 → 4 000 input tokens | **$1 197** |
| Free AI-active rate 50% → 30% | **$1 130** |
| Payer rate 8% → 12% | **$1 448** |
| Gemini context caching on the enrichment block | **$1 000–1 250** |
| `cost_calculator` pricing table turns out to be correct | **~$2 000** |

**The two highest-value levers are not pricing decisions.** Auditing `max_tokens` and trimming chat context together roughly double contribution, cost the learner nothing perceptible, and belong to nobody today — which is why they are Open Questions 1 and 2 rather than phase items someone will pick up by accident.

Two things this model does not claim: it ignores infrastructure, storage, CDN, email and salaries, so contribution is not profit; and the 8% payer rate is unvalidated, with passes plausibly pushing it above what a subscription-only product would reach, since $0.99 is an impulse rather than a commitment.

**And it is the wrong model for launch.** The first market is Nigerian students, where the USD list price is unpayable. §6.8 is the one that matters.

### 6.8 Nigeria is the launch market, and FX parity would price us out of it

USD/NGN is around **₦1,380–1,390** ([Wise, week of 20–26 Aug 2026](https://wise.com/gb/currency-converter/usd-to-ngn-rate/history)). At parity, $4.99 is **₦6,900**. What that would mean next to what Nigerians actually pay for subscriptions:

| Product | NGN/month | ≈ USD |
| --- | --- | --- |
| [Spotify Student](https://awajis.com/spotify-subscription-plans-nigeria/) | ₦800 | $0.58 |
| [Spotify Individual](https://www.spotify.com/ng/premium/) | ₦1 600 | $1.16 |
| [Netflix Mobile](https://www.netflix.com/ng/) | ₦2 500 | $1.81 |
| Netflix Standard | ₦6 500 | $4.69 |
| **Maigie Plus at FX parity** | **₦6 900** | $4.99 |

*Content was rephrased for compliance with licensing restrictions.*

At parity we would charge a Nigerian student **more than Netflix Standard**, 4.3× Spotify Individual and 8.6× Spotify's student tier — for a study tool whose nearest substitute is free Gemini on the same phone. Netflix prices Nigeria [~76% below the US](https://www.aisubdeal.com/de/pricing/netflix/nigeria/) precisely because parity does not work here. Every platform that has succeeded in Nigeria puts its mass-market tier at ₦800–2 500.

So **NGN is a set price, not a conversion.** Apple, Google and Paystack all support per-territory pricing; $4.99 stays the US/UK list.

| Product | NGN | ≈ USD | % of USD list |
| --- | --- | --- | --- |
| 5-Hour Plus Pass | **₦700** | $0.51 | 51% |
| 7-Day Plus Pass | **₦1 800** | $1.30 | 52% |
| Plus Monthly | **₦2 400** | $1.73 | 35% |

The ladder stays coherent: ₦3 360/day, ₦257/day, ₦80/day, correctly ordered, and two 7-day passes (₦3 600) still cost more than a month (₦2 400), so there is no arbitrage in either currency.

**₦2 400 rather than ₦2 500 is deliberate.** [Paystack charges 1.5% + ₦100 on local cards, capped at ₦2 000, with the ₦100 waived below ₦2 500](https://paystack.com/pricing). Pricing at ₦2 500 triggers the flat fee: ₦137 instead of ₦36, turning a 1.5% cost into 5.5% for one naira of extra revenue. Every NGN price in this plan sits under that threshold, and the same logic is why ₦700 rather than ₦900 for the 5-hour pass — the flat fee would have been 14% of the sale. *Content was rephrased for compliance with licensing restrictions.*

Net per sale, blending 60% Paystack web and 40% Google Play at 15%:

| Product | Paystack net | Play 15% net | Blended |
| --- | --- | --- | --- |
| 5-Hour Pass | $0.50 | $0.44 | **$0.48** |
| 7-Day Pass | $1.28 | $1.11 | **$1.21** |
| Plus Monthly | $1.71 | $1.47 | **$1.61** |

**Nigeria-first revenue at 10 000 MAU**, with the payer mix weighted toward passes as prepaid purchasing behaviour suggests — 1.5% subscribe, 4% buy 7-day passes (1.4/month), 2.5% buy 5-hour passes (2.5/month):

| | Without the §6.5 fixes | With them |
| --- | --- | --- |
| Net revenue | $1 220 | $1 220 |
| Paid COGS | $965 | $242 |
| Free COGS | $1 472 | $460 |
| **Contribution** | **−$182** | **+$518** |
| Margin | negative | 42% |

**At Nigerian prices the cost work is not an optimisation, it is the precondition for the market existing.** Without the `max_tokens` audit and the chat-context trim, unit economics are negative before a single dollar of infrastructure. With them, 42%.

Revenue per MAU is **$0.122**, roughly half the USD-priced figure — so Nigeria needs about twice the users for the same revenue. That is a planning input, not a problem: it is the normal shape of an emerging-market launch, and it is why §6.5's free-tier cost per learner is the number that decides whether this works.

**Two consequences for product, not just price.**

The **7-day pass is probably the lead product in Nigeria, not the subscription.** Recurring card mandates fail often, and Nigerians are practised buyers of discrete prepaid digital goods — data bundles, airtime. A ₦1 800 pass bought the week before an exam fits that behaviour; a ₦2 400/month standing charge fights it. Phase 7's client work should lead with the pass wallet and treat the subscription screen as secondary.

And the paid case cannot be "AI access", because free Gemini is on the same phone and sets that price at zero. It has to be what a general chatbot structurally cannot do: your courses, your materials, your measured weak areas, your exam date, spaced repetition against what you actually forgot. That is the book's argument in `ch36` — capability, not AI — and here it is also the reason a price above zero is defensible at all.

A student-verified tier (Spotify charges ₦800 against ₦1 600) is the obvious later move. Not now: verification costs more than it would earn at this scale.

### 6.9 Points: the earned path, and it leads to passes

One earned currency, one thing to spend it on.

| | |
| --- | --- |
| Earned by | referring a learner who **stays** — see the qualification below |
| Grant | **100 points** per qualified referral |
| Cap | **none.** No monthly limit, no lifetime limit |
| Spendable on | `plus_pass_5h` (**100 points**), `plus_pass_7d` (**250 points**) |
| Not spendable on | the subscription, at any point total, ever |
| Expiry | **60 days from the moment each grant is earned**, FIFO on redemption |

**One qualified referral is exactly one 5-hour pass.** This is the load-bearing number, not a coincidence of rounding. It means a learner who does the thing once, once, gets something whole for it — and therefore nothing ever expires unspent for anyone who did the minimum. A currency where the smallest earn cannot reach the smallest redemption is a currency that mostly expires, and a reward that mostly expires is worse than no reward, because the learner learns we do not mean it.

**Qualification: the referred learner is active on 7 distinct days.** Kept from the earlier design, and it is the whole anti-abuse mechanism now that the cap is gone. Two conditions, both necessary:

- **7 distinct calendar days**, not 7 days elapsed since signup, and not a single 7-day session. A farmed account has to be driven on seven separate days.
- **Activity means a billable operation**, not an app open. An account that logs in seven times and studies nothing does not qualify. This is measurable the day Phase 3b lands, because that is when every operation starts recording units.

Points are granted **once** per referred learner, at qualification, and never revoked afterwards. A referred learner who churns in week three keeps the referrer's points; clawback for later behaviour is unmanageable and reads as bad faith.

**Removing the cap is safe because the cap was never the control.** The old design had both a 10/month limit and the 7-day qualification, which is two locks on one door — and the cap punished the only person it could reach, the learner with a genuinely large study group. The exposure, priced honestly:

| | Per qualified referral |
| --- | --- |
| Reward COGS ceiling | **$0.30** (a 5-hour pass at its full 3 000-unit allowance) |
| Reward COGS, typical | ~$0.20 |
| Cost of the referred account itself, if fake | $0.18–0.50/month of free-tier inference (§6.7) |
| Attacker's cost | seven days of driven activity per fake account, per $0.30 |

So a farm spends more on our free tier than it extracts from our rewards, and it has to spend a week per unit doing it. A real referral, meanwhile, hands us a learner who has already used the product on seven separate days — the cheapest acquisition in the plan by a wide margin, at $0.30. **There is no number of genuine referrals we should want to refuse**, which is the actual argument for removing the cap.

The one control that remains is **velocity, not volume**: qualifications arriving faster than seven days apart from a single referrer are impossible by construction, and a monitoring alert on referral qualifications per referrer per week is worth having so the first farm is noticed rather than discovered in the COGS.

**Why points cannot buy the subscription.** Three reasons, in descending order of how hard they are to argue with.

1. **A subscription is a billing relationship; a pass is inventory.** Granting subscription time for points means either a server-side entitlement that Stripe and the stores do not know about — so `/entitlement` and the store's notion of the subscription disagree, which is exactly the four-resolvers problem Decision B exists to end — or a discount coupon, which drags points into pricing, tax and store-rule territory for no product gain.
2. **Store rules.** Neither Apple nor Google supports crediting an in-app currency against an auto-renewable subscription managed by their billing. Any implementation is a workaround, and workarounds in the subscription path are what get builds rejected.
3. **The two products mean different things.** A pass is a decision to study this week. A subscription is a decision to keep studying. Points can honestly produce the first and cannot produce the second, and a subscription arrived at without a payment decision does not renew — it lapses, and it lapses having taught us nothing.

Points therefore sit entirely inside the pass rails already being built: redemption is `POST /billing/points/redeem` producing a `PlusPass` row with `status='inventory'`, source `points` rather than a purchase. Everything downstream — activation, the fresh window, Decision D's redundancy refusal, the sweep, the notification — is the code path passes already use. **Points add a ledger and one endpoint. They add no entitlement mechanics.**

**Expiry: 60 days per grant, FIFO, and the date is on screen.** The reasoning is in Decision O. Redemption always spends the oldest live grant first, so a learner who earns steadily never loses anything, and the wallet shows the next expiring batch with its date — the plan already forbids showing a learner a number about their own account that the server did not produce, and an expiry date is exactly such a number.

**Not enabled at launch: earning by contribution.** `ResourceUploadReward` exists and `UploadResourceModal.tsx:298` already promises "1,500 credits" for an approved resource — a currency that will not exist, for an approval process that does not either. The copy is deleted in Phase 7. Resource contribution is the obvious second earn source and it is a moderation problem before it is a commercial one; Open Question 11.

## 7. Architecture decisions

### Decision A: A pass is inventory until the learner activates it. The clock starts on activation, not on purchase.

A learner may hold any number of passes, of any kind, indefinitely. Buying grants a row with `status='inventory'` and no expiry. `POST /billing/passes/{id}/activate` sets `activated_at = now`, `expires_at = now + duration`, `status='active'`.

This is the whole point of the product: a $0.99 five-hour pass bought on Tuesday and spent on Saturday's revision session is worth buying. A five-hour pass whose clock starts at the checkout screen is not, and would generate refund requests at a rate that threatens store standing.

**Exactly one pass is active at a time**, enforced by a partial unique index, not by application logic:

```sql
CREATE UNIQUE INDEX "PlusPass_oneActivePerUser_idx"
  ON "PlusPass" ("userId") WHERE status = 'active';
```

A second concurrent activation loses the insert rather than racing to a state where two passes both look active and one silently leaks. Two requests, one winner, `409`.

### Decision B: One resolver. `entitlement_service.resolve(user_id)` is the only thing that decides whether a learner is Plus.

New module `src/domains/billing/services/entitlement_service.py`:

```python
@dataclass(frozen=True)
class Entitlement:
    tier: Literal["free", "plus"]
    source: Literal["none", "subscription", "pass", "trial"]
    expires_at: datetime | None       # pass expiry, or subscription period end
    pass_id: str | None
    subscription_tier: str | None     # the raw User.tier, for display and history
    is_trial: bool
    trial_days_remaining: int | None
    window_allowance: int             # charged credits per window, §6.2

async def resolve(user_id: str) -> Entitlement: ...
```

**Personal scope only.** `resolve()` takes a `user_id` and nothing else — no `space_id`, no optional scope argument — so it cannot accidentally become the space resolver later. Space-scoped entitlement stays where it is (Decision F).

Precedence, highest first: **subscription → active pass → trial → free.** A subscriber outranks a pass so that a pass is never silently burned by someone who already has Plus (see Decision D). There is no `seat` source, and adding one is out of scope.

Tier is resolved from an **explicit map**, never a prefix:

```python
PLUS_TIERS = frozenset({"PREMIUM_MONTHLY"})
```

**One frozenset, one member.** Revision 3 sat a `LEGACY_PLUS_TIERS` beside it holding `PREMIUM_YEARLY`, `STUDY_CIRCLE_MONTHLY` / `_YEARLY` and `SQUAD_MONTHLY` / `_YEARLY`, resolving all five to `plus` so that subscribers on retired products were not denied capabilities by the `startswith("PREMIUM")` bug (drift 10) while their product was being withdrawn.

**There are no such subscribers, so the frozenset is not written.** Drift 10 is closed by deleting the five tier values, not by admitting them: no tier set names them, `_price_id_to_tier` loses its Study Circle and Squad branches, and a `User.tier` holding one of those strings is a data error rather than a supported state. This is the difference between an entitlement layer with one rule and an entitlement layer with one rule plus an exception table — and the exception table would have outlived the exception, because nothing ever deletes a frozenset that once mattered.

**The precondition, stated where it can be checked:** Phase 2b's first task queries for live subscriptions on all five tiers and for any non-zero `purchasedCreditsBalance`. If any row comes back, `LEGACY_PLUS_TIERS` is restored exactly as revision 3 specified it and Phase 8's migration step returns with it. Breaking someone who is paying us is not a trade this plan is willing to make; the confidence here comes from the count being zero, not from preferring the tidier code.

The four mechanisms in §2 become four thin callers:

- `feature_tier_service.get_effective_tier` → `resolve()`, keeping its `(tier, is_trial, days)` return shape so its ~15 call sites need no edit.
- `feature_flags.effective_tier_for_request` → `resolve()` **for the personal branch only**; the `seat_tier` branch at `:498-501` is left exactly as it is. Fixes drift item 11 for personal-scope requests: trials and passes now select models there.
- `credit_consumption_service` → `Entitlement.window_allowance` instead of `CREDIT_LIMITS[user.tier]`, **on the non-space path**. The `space_id` branch is untouched.
- `require_premium` / `PremiumUser` → **deleted**. Nothing calls them, and a fifth opinion is what this decision exists to prevent.

`resolve()` is called on nearly every gated request, so it must be one row read. Hence Decision C.

### Decision C: The active pass and the usage window are denormalised onto `User`.

Pass: `activePlusPassId`, `activePlusPassExpiresAt`. Window: `usageWindowStartedAt`, `usageWindowUnitsUsed` — **units, not credits**, matching §6.2 and the Phase 3 migration; revision 3 spelled this `usageWindowCreditsUsed` here and `usageWindowUnitsUsed` in Phase 3, and a column cannot have two names. `resolve()` and every credit check read them from the `User` row they already load, and touch `PlusPass` only on activate, expire and inventory listing.

`PlusPass` remains the record of truth for passes; the two cached columns have exactly one writer (`pass_service`) and a reconciliation sweep (Decision E). This is the same trade `Course.progress` already makes, kept true by `recount_course_progress`.

### Decision D: Activating a pass while Plus is already active is refused, not queued and not stacked.

`409 PASS_REDUNDANT` when the learner has an active subscription, an active trial, or another active pass. Passes do not extend one another and do not queue — a queue makes "how long am I Plus for" unanswerable at a glance, and expiry ordering becomes a source of support tickets.

The learner keeps the pass. Nothing is consumed by a refused activation.

Corollary: a pass activated and then interrupted by a *subscription* purchase keeps running. It is not refunded, not paused, not extended. Pausing invites gaming — activate, pause at 4h59m, hold Plus indefinitely — and there is no honest place to draw the line.

### Decision E: A pass ends when its wall clock ends, or when its usage allowance is spent.

`expires_at <= now` and the pass is over. `units_used >= units_allowance` and the pass is also over, with a different message.

**Both conditions are real, and pretending otherwise costs money.** The earlier draft of this decision said wall clock only, on the reasoning that "full premium access for 5 hours" must be literally unbounded or it is mis-sold. §6.2 shows what that promise costs: five hours of continuous live voice is about $6.00 of inference against $0.75 of net revenue. An unbounded pass is not a generous product, it is a product that loses money faster the more it is used, which is the one property a product must not have.

So the pass promise is **capabilities without limit, usage with a stated ceiling**: every Plus feature, plus 3 000 units — about 17 Plus-quality chat turns or 15 minutes of live voice tutoring, and the copy says so. A learner who exhausts the allowance in ninety minutes has had $0.30 of inference for $0.99 and is told plainly what happened, with the option to buy another pass. A learner who spreads it over five hours gets the same thing. Neither is surprised, because the number was on the purchase screen.

The 7-day pass works the same way with a weekly total (10 000 units) rather than a single window, because a week of study is naturally many sessions.

**Activation starts a fresh usage window** — `usageWindowStartedAt = now`, `usageWindowUnitsUsed = 0`. Without this, a 5-hour pass activated at minute 290 of a Free window would deliver ten minutes of allowance and then a wall, and the product would be mis-sold on a technicality.

*(Revision 4 removed a duplicate of the paragraph above that named the column `usageWindowCreditsUsed`. The column is `usageWindowUnitsUsed`, matching §6.2's rename of credits to cost-denominated units and the migration in Phase 3. Decision C carried the same stale spelling and is corrected.)*

Expiry resolves lazily on read — an expired pass is treated as free the instant it is read, before any sweep runs — but a Celery beat task every 5 minutes flips `status='consumed'`, clears the cached `User` columns, and emits the notification. Lazy-only is not sufficient: a learner whose pass ended must be *told*, and nothing tells them if nothing runs.

Unused pass time is forfeited. A five-hour pass is five hours whether or not the learner opened the app.

### Decision F: Spaces are out of scope. Passes are personal-scope by rule, and the rule is enforced by not writing the code.

Nothing in this plan touches: `SpaceMember.seat_tier`, `seat_impl.py`, `learning_spaces/**`, `Space.credits` / `Space.credits_limit`, the `space_id` branch of `check_credit_availability:334-343` and `consume_credits`, the space branch of `feature_flags.effective_tier_for_request:498-501`, `SeatAddonPurchaseRequest`, the Circle Plan and Plus Seat catalogue entries, or their Stripe prices, Paystack plan codes and `config.py` settings.

**What "Study Circle and Squad are retired" does and does not mean.** `STUDY_CIRCLE_MONTHLY/YEARLY` and `SQUAD_MONTHLY/YEARLY` are values of `User.tier` — personal subscription tiers that happen to be named after circles, already in `DEPRECATED_PLAN_IDS` at `stripe_service.py:53-79`. Finishing their retirement is personal-scope work. `circle_plan_monthly` and `plus_seat_add_on_monthly` are different products entirely, live, and untouched. The names are confusingly close; the distinction is `User.tier` versus `SpaceMember.seat_tier`.

**Where the boundary is load-bearing rather than merely respected.** Decision B repoints `feature_flags.effective_tier_for_request` at `resolve()`. That function has two branches: personal scope from `personal_tier`, space scope from `seat_tier`. Only the **personal branch** is repointed. The space branch keeps reading `seat_tier` exactly as it does today, including its existing behaviour of defaulting to `FREE_SEAT` on read failure (`seat_impl.py:53-57`).

The consequence is worth stating rather than discovering: a learner holding an activated pass gets Plus models in their personal workspace and **free-tier models inside a Space** unless that Space has assigned them a Plus seat. A pass does not travel into a Space. That is the correct outcome under this boundary — a seat is a thing a Space owner grants and revokes, and a five-hour reassignable seat has no meaning in a pool the seat accounting can balance — but it will read as a bug to the first learner who hits it, so the paywall copy for passes says "your personal workspace" and does not say "everywhere".

Likewise: space-scoped usage keeps drawing on the space credit pool, not on the acting learner's window. A pass holder working in a Space spends the Space's credits, not their pass. Neither the window nor the pass is involved.

If Spaces later come into scope, the two open items are: whether personal entitlement should override a free seat, and whether space usage should fall back to the learner's window when the pool is empty. Both are deliberately unanswered here.

### Decision G: The purchase record is the source of truth, because a consumable cannot be restored.

`PlusPurchase` (Decision H) is written on verification, before anything is granted, with `providerReference` unique. That uniqueness is the idempotency key: webhook replay, client retry, and a `restore()` that re-presents the same token all collapse onto one row.

This matters most on iOS. **StoreKit does not return finished consumables from `Transaction.currentEntitlements`** — a reinstalled app cannot recover a purchased-but-unactivated pass from the device. If the server did not persist it at verification time, it is gone and the learner is owed a refund. So: verify, persist, *then* grant, in that order, and treat "restore" for passes as "read your inventory from our API", not as a StoreKit operation.

A purchase token already bound to a different `userId` is rejected with `409 PURCHASE_ALREADY_CLAIMED` and logged. This is the standard cross-account IAP abuse vector and the unique constraint is the whole defence.

### Decision H: `PlusPurchase` replaces `CreditPurchaseTransaction`, and the old tables are dropped.

A new table rather than columns on the old one, because the old one is `NOT NULL` on `creditPackId` and `creditsGranted` — both meaningless for a pass or a subscription.

**Revised in revision 4.** Revision 3 kept `CreditPurchaseTransaction` and `CreditPack` unmigrated so that purchase history stayed readable, with `GET /billing/purchases` unioning both. There is no purchase history: nobody has bought a credit pack. Both tables are **dropped** in the Phase 4 migration, `GET /billing/purchases` reads `PlusPurchase` only, and the union logic is never written.

The union was the more expensive half of that endpoint — two schemas with different notions of what was bought, reconciled into one response shape for the benefit of zero rows. Dropping it also removes the last reason `credit_purchase_service.fulfill_purchase` exists, which the Paystack port (Phase 2b) would otherwise have had to carry forward into SQLAlchemy.

### Decision I: Store prices are displayed from the store. The catalogue is for identity, not for currency.

`GET /plans/catalog` returns `price_cents` in USD as reference. On iOS and Android the client renders the **localized `displayPrice` from the store product**, never the catalogue value and never a literal. Apple and Google set local prices per territory, adjust them for tax, and change them without asking. A screen showing `$2.49` to a learner Google will charge ₦3,900 is a store-review finding and a support ticket.

Web renders the catalogue price, because Stripe charges exactly what the catalogue says.

`@maigie/types` gets a generated `PlanCatalogResponse` and every client reads it. `plan-data.ts` (web), `PLANS` (`SubscriptionScreen.tsx:72-79`), and the mobile SKU constants (`usePlayBilling.ts:17-18`) are deleted, not updated. The public marketing site is the one permitted exception — a static Astro build with no session — so `maigie-public/src/components/pricing/plan-data.ts` stays a literal and gets a test asserting it matches the backend catalogue.

### Decision J: One `UpgradeRequiredPanel`, one `LockedNotice` mapper, per client.

The **five** hand-rolled locked cards in the web app (§5.5, and drift 17 found the fifth) collapse into the existing `UpgradeRequiredPanel`. It grows two things: when the learner **owns an unactivated pass**, the primary action becomes "Activate your 7-day pass" instead of "Upgrade"; and when the block is a window cap rather than a capability, it shows the reset time instead of a price.

The first is the single highest-value piece of UI in this plan — the difference between a pass being bought and a pass being used — and it must not be reimplemented four times.

### Decision K: Server-side gates stay server-side. Clients pre-empt only where they can be right.

The `403`/`200 + notice` convention is unchanged and remains authoritative. Clients may grey out a control when `/capabilities` says it is locked (as `MOB/src/app/prepare/launch.tsx:90` already does), but never enforce, and never hide — a locked control that is visible and explained sells; a hidden one does not.

### Decision L: One metering chokepoint, and it has to be plumbed before it can meter.

Every LLM call deducts units. There are 31 call sites and four different ways to reach a provider, so the first job is reducing that to one.

`llm_resilient.py:249-320` is the right place: inside the attempt loop, where the provider, the model and the result are all in scope, and it already sits under `generate_content`, `generate_content_json` and `generate_grounded_content` — which together cover 26 of the 31 sites. The five stragglers each build their own client and must be redirected through it: `memory_impl.py:49`, `planning_impl.py:55`, `schedule_regen_impl.py:213` and `:227` (OpenAI), and `space_impl.py:1324` (out of scope, Decision F — leave it, and accept it stays unmetered).

**The blocker is that `llm_resilient` throws away what it needs to charge.** The wrapper discards the provider response object and returns text only, so no caller ever sees `usage_metadata`. Token counts have to be plumbed through before a single unit can be deducted. That is the actual work in Phase 3, and it is why "add metering" is not a one-line change.

Two consequences that fall out of doing it here rather than at the call sites:

**Retries are charged, because retries cost.** `generate_content` can bill up to nine provider calls for one logical operation (three attempts × three providers, with an empty reply counted as failure at `:290`). Metering inside the loop counts each one. This will look unfair the first time a learner's allowance is consumed by our own instability — so the retry budget also becomes a *cost* decision rather than only a reliability one, and the fallback chain should be shortened rather than the charge hidden.

**Charge on success, absorb on failure.** The pattern `study_voice/notes.py:172-220` and `note_merge_service.py:124-164` already use: if the artefact exists but consumption fails, the learner keeps the artefact. Never the reverse.

Also delete the two dead `CREDIT_COSTS` entries (`ai_course_generation`, `ai_action`) rather than wiring them up — under this decision, cost is measured, not tabulated.

### Decision M: Background AI is entitlement-aware, metered, and capped separately.

Two Celery tasks call an LLM per user per schedule: `learning.generate_recommendations` nightly and `learning.generate_reflections` weekly, both over every active profile. Together, **$0.64/month for a learner who has not opened the app.**

Three rules:

1. **Proactive AI draws on the learner's allowance**, against a sub-budget capped at **20% of the monthly backstop**. It is their usage, spent on their behalf, and it belongs in the same accounting as everything else. The sub-cap stops a background task from consuming an allowance the learner was saving for a study session.
2. **Cadence follows entitlement, not the calendar.** Free gets discovery recommendations weekly, Plus nightly. Free weekly reflections are composed deterministically from `weekly_summary.py`, which already produces honest aggregates with no model call; the LLM narrative is the Plus version, which is what `feature_tier_service` already claims and `reflection_service.py:173-174` already branches on.
3. **Dormancy stops the spend.** No proactive generation for a learner with no activity in the preceding 7 days. Today the fan-out is `list_active_profiles`, where "active" means a profile row exists. Generating tonight's recommendation for someone who last opened the app in March is not proactive, it is a standing order nobody placed.

This is also the book's position rather than only a cost argument (`philosophy/ch04-product-principles`, Principle Five): momentum is designable, and a recommendation is worth generating when there is momentum to build on.

### Decision N: The paywall is the recommendation. Conversion happens at the point of need, or not at all.

From `business/ch37-personal-learning`:

> The platform should recognise when people are ready for greater responsibility and introduce new capabilities at the appropriate time.

And from `business/ch36-pricing-philosophy`:

> People do not pay because features were hidden. They pay because greater capability creates greater value.

So Maigie does not advertise Plus. It recommends a next action, and when the *best* next action for this learner happens to require Plus, the recommendation says so and carries the upgrade inline. The pitch is the learning reason, never a sales line: not "Upgrade for adaptive quizzes" but "Your weakest three topics are algorithms — an adaptive set would drill those first."

The pieces already exist and are not connected:

- `guidance_engine.compute_guidance` (`guidance_engine.py:267`) decides the next action and is called on every home load via `home_service.py:36`.
- `conversion_engine.evaluate_triggers` (`conversion_engine.py:117`) evaluates upgrade moments, writes `ConversionTriggerLog`, and honours `SAME_TRIGGER_COOLDOWN_DAYS = 30` and `trigger_dismissal_count`.
- They have never met. Guidance never asks whether its recommendation is gated, so the recommendation engine and the conversion engine reason about the same learner independently.

**The change is that `next_action` gains `requiresPlus`, `capability` and `upgradeValue`, populated by asking `feature_tier_service.check_capability` about the action it just chose.** Guidance keeps choosing on pedagogical grounds only — it must never prefer a Plus action *because* it is a Plus action, and a test should assert that the chosen action is identical for a free and a Plus learner with identical state. The entitlement lookup happens after the choice, never during it.

Three rules follow, and they are what keep this from becoming the thing the book warns against:

- **One offer at a time, at most one per window.** The existing 30-day per-trigger cooldown stays. A learner who dismisses twice (`trigger_dismissal_count`) stops seeing that capability offered for 90 days.
- **A recommendation that requires Plus must have a free alternative rendered beside it**, not instead of it. "Adaptive practice would target these three topics — or run a weak-areas set now." The learner is never left without a next action, which is Principle Three.
- **Never recommend a gated action to a learner who cannot act on it at all.** If the trial is exhausted and the learner has no pass, offering a capability they cannot reach is an advertisement wearing a recommendation's clothes. A learner holding **redeemable points** can act, and the offer becomes "redeem your points for a 5-hour pass" rather than a price (Decision J's panel already branches on owned passes; points are the third branch).

Where the offer appears: home `next_action`, the Prepare practice launcher (`practiceModes.ts` already knows which modes are Plus), the document format picker, the Reflect locked panels, and `UpgradeRequiredPanel` on any `403`. Where it does not: no interstitials, no banners, no upgrade prompt on app open, nothing in a notification.

`ConversionTriggerLog` already records `shownAt`, `dismissedAt`, `convertedAt` and `capabilityHighlighted`, so which recommendations actually convert is measurable from day one. That is the number that should drive this, not opinion about copy.

### Decision O: Points are a ledger that redeems into inventory. They expire per grant at 60 days, and they never touch the subscription.

Three sub-decisions, because they fail independently.

**Points are a ledger, not a balance column.** `PointsLedgerEntry` rows with signed `points`, an `expiresAt` on every positive entry, and a `kind` recording where they came from or went. A single `pointsBalance` integer on `User` cannot express per-grant expiry, and per-grant expiry is the whole design. Balance is `SUM(points) WHERE NOT expired`, denormalised onto `User.pointsBalance` as a cache the ledger can always rebuild — the same relationship §6.3's window columns have to the usage records, and for the same reason: reads are frequent and the truth is elsewhere.

**Expiry is 60 days from each grant, not 30 and not never.**

- **Never** makes every point ever issued a permanent liability against future COGS, and there is no point at which the books close. It also removes the only thing that converts a saver into a user: a reason to spend.
- **30 days** is too short to be honest. A learner who refers one friend has to wait out that friend's 7-day qualification before the clock even starts, leaving them three weeks to notice they have something and decide to use it. Miss one exam period and it is gone. A reward that expires before the learner has a use for it teaches them the reward was decorative.
- **60 days** covers a full study cycle plus a slow month, bounds the liability at a period we can actually forecast against, and — with one referral equalling one 5-hour pass — means the only points that ever expire are the *remainder* of someone who earned a second referral and did not reach 250. That remainder is small and its expiry is defensible, which is the test.

Mechanically: FIFO on redemption (oldest live grant first), expiry resolved lazily on read like a pass, and a nightly sweep that writes the negative expiry entries so the ledger is self-explaining rather than reconstructed. A learner is notified once, seven days before their oldest grant expires, and only if that grant alone can still buy something. Notifying someone about 40 unspendable points is noise.

**Redemption produces a pass, and the subscription is unreachable from the points path by construction — not by a check.** `POST /points/redeem` accepts only `plus_pass_5h` and `plus_pass_7d`; there is no branch that could grant subscription time, no coupon code path, and no `productKind: 'subscription'` reachable from a ledger entry. The reasons are in §6.9. Stating it as a construction rather than a validation matters because a validation is a thing a later ticket removes.

The redeemed pass is identical to a bought one in every respect except its provenance: `PlusPass.source = 'points'` alongside `'purchase'`, and no `PlusPurchase` row, since nothing was purchased. Decision G's "verify, persist, then grant" does not apply — there is no store transaction to verify — but Decision A, D, E and the sweep all apply unchanged. A points-redeemed pass sits in inventory indefinitely and the learner activates it when they want it, which is the whole reason points buy passes and not window units.

**Rewarded ads are withdrawn, and the withdrawal is a decision rather than a deferral.** `claim_ad_reward` grants credits against a limit that no longer exists, no ad SDK is integrated on either client, and the two screens that call it (`WEB/features/credits/EarnPage`, `MOB/src/app/earn/watch-ad.tsx`) are already unrouted or standalone. Re-pointing it at the window would have shipped an earn mechanic nobody designed. When ads return, the question to answer first is what they buy — and if the answer is points, they arrive as one more `kind` on the ledger built here, which is a day of work. That is the argument for removing the code now and keeping the table.

## 8. Data model

Migration `063_add_plus_passes.py` — `062_chat_generation_attempt.py` is the current head. `060` and `061` are taken (`060_notification_phase1`, `061_notification_phase2`); do not reuse them.

**`PlusPass`**

| Column | Type | Notes |
| --- | --- | --- |
| `id` | String PK | |
| `userId` | String FK → `User.id` CASCADE | indexed |
| `productId` | String | `plus_pass_5h` \| `plus_pass_7d` |
| `durationMinutes` | Integer | 300 \| 10080. Snapshotted, so re-pricing or re-timing the product never changes a pass already sold |
| `unitsAllowance` | Integer | 3 000 \| 10 000. Snapshotted for the same reason (Decision E) |
| `unitsUsed` | Integer | default 0 |
| `status` | String | `inventory` \| `active` \| `consumed` \| `refunded` |
| `purchaseId` | String FK → `PlusPurchase.id` | **nullable** — null when `source='points'` |
| `activatedAt` | DateTime tz | null while in inventory |
| `expiresAt` | DateTime tz | null while in inventory |
| `endedReason` | String | null \| `expired` \| `exhausted` \| `refund` |
| `source` | String | `purchase` \| `points` — Decision O |
| `createdAt` / `updatedAt` | DateTime tz | |

Indexes: `(userId, status)`; `(status, expiresAt)` for the sweep; the partial unique index from Decision A.

**`PlusPurchase`**

| Column | Type | Notes |
| --- | --- | --- |
| `id` | String PK | |
| `userId` | String FK → `User.id` CASCADE | indexed |
| `productId` | String | catalogue id |
| `productKind` | String | `pass` \| `subscription` |
| `provider` | String | `stripe` \| `paystack` \| `apple` \| `google_play` |
| `providerReference` | String | **unique** — Decision G |
| `amountMinor` / `currency` | Integer / String | as charged, in the learner's currency |
| `status` | String | `pending` \| `completed` \| `failed` \| `refunded` |
| `completedAt` / `refundedAt` | DateTime tz | |
| `rawPayload` | JSON | verification response, for disputes |

**`PointsLedgerEntry`** — Decision O

| Column | Type | Notes |
| --- | --- | --- |
| `id` | String PK | |
| `userId` | String FK → `User.id` CASCADE | indexed |
| `points` | Integer | **signed.** `+100` grant, `-100` redemption, `-40` expiry |
| `kind` | String | `referral_qualified` \| `redemption` \| `expiry` \| `adjustment` |
| `expiresAt` | DateTime tz | set on positive entries only; null on redemptions and expiries |
| `sourceRef` | String | referred `userId`, redeemed `PlusPass.id`, or the expiring entry's `id` |
| `note` | String | support-visible reason for `adjustment` |
| `createdAt` | DateTime tz | |

Indexes: `(userId, createdAt)`; `(userId, expiresAt)` for the sweep and for the FIFO read. Unique on `(userId, kind, sourceRef)` where `kind='referral_qualified'` — one grant per referred learner, enforced by the database rather than by the service, because the qualification job is idempotent only if this holds.

**`User`** — added: `activePlusPassId`, `activePlusPassExpiresAt`, `usageWindowStartedAt`, `usageWindowUnitsUsed`, `usageMonthStartedAt`, `usageMonthUnitsUsed` (the §6.3 backstop), `pointsBalance` (cache over `PointsLedgerEntry`, rebuildable), `appleOriginalTransactionId` (unique, nullable), `appleProductId`.

**`User`** — dropped in the same migration: `creditsUsed`, `creditsPeriodStart`, `creditsPeriodEnd`, `creditsSoftCap`, `creditsHardCap`, `creditsUsedToday`, `creditsDailyLimit`, `lastDailyReset`, `purchasedCreditsBalance`. One migration, not two. The earlier draft staged this over two releases to keep a rollback path; with **no paid users and no purchased balances to honour** there is nothing to roll back to and nothing to preserve.

**Dropped tables**: `CreditPack`, `CreditPurchaseTransaction` — unconditionally, per Decision H as revised. The Phase 2b row count confirms they are empty before the Phase 4 migration drops them; it is a precondition check, not a decision point.

**Kept but no longer written**: `ReferralRewardClaim` (superseded by the ledger; the referral *link* tables and `User.referralCode` stay and are the input to qualification), `AdRewardClaim` (Decision O — kept so the redesign is not foreclosed), `ResourceUploadReward` (Open Question 11).

Unchanged: every space-scoped table and column — `SpaceMember.seat_tier`, `Space.credits`, `Space.credits_limit` (Decision F).

## 9. API surface

New, under `/api/v1/billing`:

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/plans/catalog` | four products, reference USD prices, store product ids per platform |
| GET | `/passes` | `{active, inventory[], history[]}` — the source of truth for pass ownership |
| POST | `/passes/{id}/activate` | `200` → `Entitlement`; `409 PASS_REDUNDANT` \| `PASS_ALREADY_ACTIVE` \| `PASS_CONSUMED` |
| POST | `/passes/checkout` | web only — Stripe one-time session for a pass |
| POST | `/purchases/apple/verify` | StoreKit transaction → verified purchase (+ pass, or subscription tier) |
| POST | `/purchases/google-play/verify` | replaces `/subscriptions/google-play/verify-product` |
| GET | `/purchases` | purchase history from `PlusPurchase` (Decision H — no legacy union; the old tables are dropped) |
| GET | `/points` | `{balance, nextExpiry: {points, expiresAt} \| null, redeemable[], history[]}` |
| POST | `/points/redeem` | body `{productId}` — `plus_pass_5h` \| `plus_pass_7d` only. `200` → the new inventory `PlusPass`; `409 INSUFFICIENT_POINTS` |
| GET | `/referrals` | code, qualified count, pending count with each one's days-active progress |
| GET | `/entitlement` | the resolved `Entitlement` — what the clients poll after a purchase |
| GET | `/usage` | `{windowResetsAt, percentUsed, tier}` — a percentage and a timestamp, never a unit count (§6.3). `monthlyPercentUsed` appears only above 80% |
| POST | `/webhooks/apple` | App Store Server Notifications V2 |

`GET /users/usage` (`WEB/features/usage/services/usageApi.ts`) is rewritten onto the same window shape rather than kept alongside it.

Removed: `/billing/ads/*` and `/billing/credit-packs*` (Decision O, §6.1).

`GET /learning/capabilities` gains `entitlementSource`, `entitlementExpiresAt`, `ownedPassCount`, `pointsBalance` and `windowResetsAt`, so `UpgradeRequiredPanel` can offer activation, offer redemption, or show a reset time (Decision J) without a second request. `UpgradeRequiredDetail` gains `ownedPassCount` and `pointsBalance`; the window-cap refusal gains `windowResetsAt`.

## 10. Phases

### Phase 0 — Decide (blocks allowance tuning; no longer blocks everything)

**Verified unstarted on 2026-09-01, not merely unmarked.** The question "was Phase 0 done and left unticked?" was checked against the code rather than assumed, because an unticked box and a done-but-unrecorded task look identical in a document and lead to opposite decisions. All three questions are open, with the evidence:

| Question | Claim in §11 | State in code | Open? |
| --- | --- | --- | --- |
| 3 — is the rate card current? | `gemini-3.5-flash` priced at $0.50/$3.00 vs published ~$1.50/$9.00 | `cost_calculator.py:26` reads `"gemini-3.5-flash": (0.50, 3.00)` | **yes** |
| 2 — 8 000 input tokens per chat turn? | `HISTORY_LIMIT = 12` plus enrichment | `ask_service.py:383` reads `HISTORY_LIMIT = 12` | **yes** |
| 1 — who owns the `max_tokens` audit? | 8 192 output tokens budgeted for a paragraph | `narrative_cache.py:237` passes `max_tokens=8192`; `llm/__init__.py:136` **defaults** every caller to `8192` | **yes** |

Question 1 is worse than §11 described. The 8 192 budget is not three call sites that each chose badly — it is the **default parameter value** of `generate_content_json` at `llm/__init__.py:136`, so every one of the 26 operations that does not pass `max_tokens` explicitly inherits it. The comment at `:124` records that it was raised from 2 048 because a *third* call site was truncating, which is how a fix for one operation became the budget for all of them. The audit is therefore one default plus a per-operation review, not twenty-six independent decisions, which makes it substantially cheaper than §11 implies and does not change who has to own it.

**Scope correction: Phase 0 does not block everything.** Revision 3's heading said it did, which is part of why nothing moved — it made three unowned research questions look like a gate on the whole plan, and they are not. Nothing in Phase 2, 2b, 4, 4b or 5 depends on the rate card: entitlement resolution, the Paystack port, pass lifecycle, points and the purchase rails are all correct whatever a token costs. What Phase 0 genuinely blocks is **allowance tuning** — the window sizes in §6.3, the COGS and contribution figures in §6.7 and §6.8, and any decision that the free tier is affordable.

- [ ] **Answer Question 3 first.** Verify `_EXACT_MODEL_PRICING:26-38` against the live Gemini rate card. Five minutes of work; every COGS figure in this document moves up to ~3× on the answer. **No allowance in Phase 3 is tuned until this is answered** — ship Phase 3 with today's effective limits carried across to the new window mechanism, and tune once the rate card is trustworthy.
- [ ] Answer Questions 1 and 2 (the `max_tokens` default and the chat context size). Both are pure cost work, invisible to learners, and neither blocks a phase — but §6.8's Nigeria economics are negative without them, so they block *launch* in the launch market. Assign an owner; that, and not the analysis, is what has been missing.
- [ ] Answer Open Question 10 (points price for the 7-day pass) before Phase 4b. Unlike a store price it is cheap to change later, so it should not block anything else.

*(Revision 3's first line referred to "Question 1 (7-day pass price)", stale numbering from an earlier draft; pass prices are settled in §6.1.)*

### Phase 1 — Make the money path reachable

**Done**, with two additions and one blocker found on the way. All three are recorded below rather than folded in silently.

- [x] Delete `credit-packs` routes and `credit_service.get_credit_packs` / `initiate_purchase`. **`SeatAddonPurchaseRequest` and the seat repository methods left alone** (Decision F). `credit_purchase_service` itself is untouched: `get_purchase_history` and `admin_adjust_balance` are still served, and `fulfill_purchase` is still reachable from the Paystack webhook, so nothing was deleted that a real transaction can still arrive for. *(Superseded by revision 4: Decision H now drops `CreditPurchaseTransaction` and `CreditPack` outright in Phase 4, and `credit_purchase_service.fulfill_purchase` goes with them in Phase 2b rather than being ported. The caution in this line was correct when written — it assumed a real transaction could still arrive for a credit pack, and the zero-purchase count is what makes that assumption safe to drop.)*
- [x] **Delete the rewarded-ad path** (Decision O): `credit_service.claim_ad_reward` and `get_ad_stats`, `AD_REWARD_CREDITS`, `MAX_ADS_PER_DAY`, the `/billing/ads/*` routes and the `AdRewardRequest` / `AdRewardResponse` / `AdStatsResponse` schemas. `billing_repo.count_ads_today` / `create_ad_claim` / `get_total_ad_earnings` and the `AdRewardClaim` table are all left in place, now unread. One consequence to know about: `billing.credits_purchased` had exactly one emitter and it was the ad claim, so nothing emits it now. The enum member stays — a pass purchase is a fact worth publishing — and the entry was removed from `test_event_bus.EMITTERS_WITHOUT_A_LISTENER`, which describes what fires rather than what exists.
- [x] Rewrite the four `scope: "personal"` entries in `stripe_service.get_active_plan_catalog`, leaving the `circle` and `add_on` entries unchanged. Added `PRICE_CENTS_PLUS_PASS_5H = 99`, `PRICE_CENTS_PLUS_PASS_7D = 249`; `PRICE_CENTS_PLUS_MONTHLY` **left at `499`**; added `STRIPE_PRICE_ID_PLUS_PASS_5H` / `_7D`; `plus_yearly` **and** `maigie_plus_yearly` moved into `DEPRECATED_PLAN_IDS`; deleted `TRIAL_DAYS_STUDY_CIRCLE` and `TRIAL_DAYS_SQUAD` only. Catalogue entries carry the §6.3 usage equivalents in a new `usageNote` field, and a test asserts every one of them names the voice figure — "5 hours of Plus" without it reads as five hours of tutoring, which costs ~8× what the pass earns.
- [x] **Set `TRIAL_DAYS_MAIGIE_PLUS = 3`**, carried in the catalogue as `trialDays`. **`TRIAL_DAYS_CIRCLE_PLAN` left at 7** (Decision F).
- [x] Shorten `TrialService.TRIAL_DURATION_DAYS` to 3. A test asserts the two copies agree. The fallback string in `conversion_engine._build_message` promised "free for 7 days" as a literal and now reads the constant.
- [x] Mount `billing_router` and `webhooks_router`.
- [x] Regenerate `openapi.json` and `libs/types/src/generated/api-types.ts`; re-run `maigie-mobile/scripts/sync-api-paths.mjs`.
- [x] Test: `test_subscription_catalog.py` — rewritten from scratch, because the existing file had not run since the SQLAlchemy migration. It imported `src.schemas.subscription`, `src.services.subscription_service` and `src.utils.exceptions`, none of which are modules, so it failed at *collection* and asserted a five-product catalogue with yearly Plus and a 7-day trial that nothing was checking. New file asserts the four `personal` products, the three prices, `410` on all six withdrawn ids across **both** rails, the pass-is-not-a-subscription refusal, the per-day price ladder, and — as scope guards — that `circle_plan_monthly` and `plus_seat_add_on_monthly` keep their ids, scopes, prices and 7-day trial.
- [x] Test: `test_billing_routes_mounted.py` — new. Asserts the routing table rather than mocking a provider, because the defect was never in the handlers: they were written, complete and unreachable, and a test that called `create_checkout_session` with a fake Stripe would have passed throughout. It also pins each deliberate absence to its reason, and fails if a Prisma sentinel is removed without the corresponding route being mounted.

**Set by hand, outside the code, and not asserted by anything:** the Stripe price's `trial_period_days` must read 3. The App Store Connect and Play Console trial periods are created in Phase 5. Four places hold this number, two of them consoles.

#### Added in Phase 1, not in the original checklist

- [x] **`plus_yearly` is refused on the price-id door as well as the plan-id door.** `assert_plan_id_is_active` guards a fresh checkout, which arrives as a plan id; `modify_existing_subscription` arrives as a *price* id. A monthly subscriber switching to yearly would otherwise have bought a withdrawn product through a door the plan-id check does not watch. Nothing in `_assert_price_id_is_active` runs on a renewal, so grandfathered yearly subscribers are unaffected — asserted.
- [x] **`paystack_service` now imports `PLAN_IDS` and `DEPRECATED_PLAN_IDS` from `stripe_service` instead of holding a copy.** The copy had already diverged the moment yearly Plus was withdrawn: Stripe would have refused it and Paystack would have gone on selling it, so the one learner who found the difference would have been a Nigerian learner buying a product nobody else could. A test asserts the two rails read the same object.
- [x] **`PlanItem` is now a `CamelModel`.** It published `price_cents` / `trial_days` while every other schema written since `CamelModel` landed publishes camelCase. Safe to change precisely because the endpoint has never been mounted, so no client is reading the old spelling — and the alternative was exporting a mixed convention into the generated client types on the same day they first became real.
- [x] **`PlanId` deliberately still accepts the six withdrawn ids.** It listed only active ids, which meant a request carrying `study_circle_monthly` was rejected by FastAPI validation with `422 not a valid plan` before `DEPRECATED_PLAN_IDS` could answer `410 this was retired, here is what replaced it`. The 410 machinery has existed and been unreachable for as long as the router was unmounted. A plan removed from the catalogue is not the same thing as a plan that never existed, and the learner holding the stale id is the one who needs to be told which.

#### Not mounted, and why — three absences, three different reasons

- **`/billing/subscriptions/google-play/verify-product`.** Verified a credit-pack purchase and granted credits, so it verified a product that no longer exists. `google_play_service.verify_product_purchase` is left in place as the basis for its replacement — the `purchases.products.get` call and the token-replay check are both reusable by the pass equivalent in Phase 5.
- **`/billing/referrals/stats`, `/claimable`, `/claim`.** All three resolve into `referral_rewards_service`, which holds a `PrismaClientRemoved` sentinel where its database used to be, so all three would answer 500. Mounting them would take three endpoints that are currently *honestly* unreachable and make them dishonestly reachable. They return in Phase 4b as the points ledger, which is a different contract rather than a port.
- **⚠️ `/billing/subscriptions/paystack/initialize` and `/verify`.** §5.1 lists these as "kept", and they cannot be: `paystack_service` holds the same Prisma sentinel. `initialize_paystack_subscription`, `verify_paystack_transaction`, `cancel_paystack_subscription` and `handle_paystack_webhook` all reach it. The webhook fails quietly — `webhooks.py` catches and answers 200, so Paystack events are silently discarded — but the two routes would answer 500.

  **This is a launch blocker and it was not in the plan.** Paystack is the NGN rail and §6.8 makes Nigeria the launch market, with naira prices set independently rather than converted precisely because FX parity would price Maigie above Netflix Standard there. Phase 1 has therefore made the money path reachable in every market except the one we are launching in. `test_billing_routes_mounted.py:104-114` fails if the sentinel is removed without the routes being mounted, so the port cannot land and be forgotten.

  **Now Phase 2b, with its own checklist.** Revision 3 said the port "belongs immediately after Phase 2" and then gave it no phase, no owner and no task list — the same failure mode as Phase 0, on the more expensive item. It is a phase now.

### Phase 2 — Entitlement resolver

**Done.** Four mechanisms became one, and the two live defects they were causing (drift 10 and 11) are closed. One addition is recorded below rather than folded in silently.

- [x] `entitlement_service.py` per Decision B, with `PLUS_TIERS = frozenset({"PREMIUM_MONTHLY"})` as an explicit frozenset. ~~**No `LEGACY_PLUS_TIERS`** — revision 4 removed it; the retired tiers resolve to `free`.~~ **Superseded by Phase 2a:** `LEGACY_PLUS_TIERS` is restored and the retired tiers resolve to `plus`, because removing it left the resolver disagreeing with everything that writes `User.tier`. The `Entitlement` dataclass is as specified; `window_allowance` carries the §6.3 numbers (Free 500, Plus 4 000, 5-hour pass 3 000, 7-day pass 4 000) so that Phase 3 repoints the meter at a value rather than inventing one. `resolve()` is one round trip — an outer join from `User` to `LearningProfile`, because the tier and the trial live on different tables and Decision C's argument is that this sits in the hot path.
- [x] Repoint `feature_tier_service.get_effective_tier` at it, preserving the `(tier, is_trial, days)` shape. All ~15 call sites unchanged; a pass holder will arrive there as `("plus", False, None)`, indistinguishable from a subscriber.
- [x] Repoint the **personal branch** of `feature_flags.effective_tier_for_request` at it; the `seat_tier` branch is untouched (Decision F, closes drift 11 for personal scope). `_fetch_personal_tier` and `_personal_tier_to_effective` are deleted with the branch they served.
- [x] Delete `require_premium`, `PremiumUser`, `PAID_TIERS` from `shared/auth/dependencies.py` and `shared/auth/__init__.py` (closes drift 8). Confirmed unused before deleting: the only references anywhere were the definitions and the two export lists.
- [x] `GET /billing/entitlement`, returning `EntitlementResponse` (a `CamelModel`). `source` is served rather than left for the client to infer, because the right UI differs per source and `expiresAt` alone cannot tell a renewal date from a pass countdown. `openapi.json`, `libs/types/src/generated/api-types.ts` and `maigie-mobile`'s path fixture regenerated.
- [x] Tests: `test_entitlement_service.py`, 31 assertions. Subscription-outranks-pass, pass-outranks-trial, trial gets Plus allowance, all five retired tiers are grandfathered *(rewritten in Phase 2a; this line originally asserted they resolve to `free`, which was the finding)*, a `PREMIUM`-prefixed unknown string is *not* Plus, the LLM router sees a trial (closes drift 11), and `require_premium` cannot come back. Scope guards: a space-scoped request never calls `resolve()` at all, and `resolve`'s signature is asserted to take `user_id` and nothing else.
- [x] Full suite green: 4 188 passed, 185 skipped, 6 xfailed. `ruff check` clean.

#### Added in Phase 2, not in the original checklist

- [x] **`personal_tier` is removed from `effective_tier_for_request`'s signature, not merely left unread.** It was a pre-loaded `User.tier` offered as a way to skip a database read, and it can no longer answer the question: a trialling or pass-holding learner has `tier == "FREE"` and is entitled to Plus models. Leaving the parameter in place would have let a caller reintroduce drift 11 through the door marked optimisation — which is exactly how the defect arrived the first time. `ask_service`'s `resolve_tier` effect drops the argument with it. `seat_tier` stays, because under space scope a seat tier genuinely is the whole answer.
- [x] **The pass branch is written and tested before passes exist.** `_compose` is a pure function over `(subscription_tier, subscription_period_end, active_pass, active_trial)`, and `_read_active_pass` is a named seam that returns `None` until Phase 4 creates `PlusPass`. The expensive part of passes is the precedence question, not the query, so Phase 4 replaces one function body and does not reopen it. An unknown pass product id falls to the *smallest* allowance, deliberately: under-granting is a support ticket, over-granting is COGS.

### Phase 2a — Close the review findings (runs before 2b)

From the 2026-09-01 review in the status header. Items 1 and 2 are live defects; the rest are coherence.

- [x] **All three webhooks fail closed.** An unset secret now refuses ingestion (`503`, so a real event is redelivered rather than lost) instead of trusting the body — the default `RESEND_WEBHOOK_SECRET` already used. Stripe refuses when `STRIPE_WEBHOOK_SECRET` is empty; the Paystack condition is inverted so an empty key fails rather than short-circuits; the Google Play RTDN endpoint verifies the Pub/Sub push **OIDC bearer token** against Google's certs with a configured audience, and checks the token's `email` against `GOOGLE_PUBSUB_SERVICE_ACCOUNT_EMAIL` — a Google-signed token proves Google minted it, not that our subscription sent it. Handler exceptions now answer `500` so the provider retries: only a processed event answers `200`. New config: `GOOGLE_PUBSUB_AUDIENCE`, `GOOGLE_PUBSUB_SERVICE_ACCOUNT_EMAIL`. Covered by `tests/test_billing_webhook_auth.py` (10 tests), which did not exist because no test of a mount or of a default would have caught a defect in their interaction.
- [x] **`LEGACY_PLUS_TIERS` restored**, as `PLUS_TIERS | LEGACY_PLUS_TIERS = ALL_PLUS_TIERS`, so the resolver and the writers agree again. Two frozensets rather than one edited set, so Phase 2b deletes a name rather than reasoning about a membership list. ~~Delete it when Phase 2b records the count as zero~~ — **done: Phase 2b took the count (all zero), deleted the frozenset, and narrowed the writers in the same change.** The restoration was right for the six hours it existed: it was the only thing standing between a yearly renewal and being billed for nothing, and it cost one frozenset to not need to be right about an unmeasured claim.
- [x] Reconciled the two tests. `test_retired_tiers_resolve_to_free` → `test_retired_tiers_are_grandfathered`, with the reason recorded in the docstring so the next person to flip it sees the argument rather than the assertion.
- [x] **`schedule_reminders._should_remind` reads `entitlement_service.resolve`**; the `tier != "FREE"` rule and the docstring paragraph arguing for it are gone. It is `async` now, and its tests stub the resolver rather than a tier string — the function no longer has an opinion about what "paid" means. A trialling learner gets the 15-minute lead time, which is the case the comparison silently lost and whose absence leaves no trace.
- [x] **Lazy subscription expiry in `_compose`** via `_subscription_lapsed`, so all three sources expire on read rather than two of three. A lapsed subscription falls through to a pass or trial rather than short-circuiting to free — the learner whose card failed and who then bought a pass is the case that makes it matter. A `None` period end is *not* lapsed: absent is not expired.
- [x] Pass ids added to `PlanId`, so `POST /subscriptions/checkout` reaches the written refusal (`400`) instead of a `422` shadowing it. Asserted at the schema boundary, which is the gate that was answering `422`.
- [x] `PlanItem.purchasable` added, defaulting `True`, `False` on both passes until Phase 5 builds the one-time rail. Listed, described, priced, not offered.
- [x] `feature_flags.TIER_TO_ALLOWLIST_KEY:148-162` — **no longer a contradiction.** It maps the five retired tiers to `plus`, which is now exactly what `ALL_PLUS_TIERS` says, so restoring `LEGACY_PLUS_TIERS` closed this finding rather than requiring a second edit. Revisit it in Phase 2b with the frozenset, not before.
- [ ] Soften or Phase-0-gate the `usage_note` figures. They are customer-facing promises resting on an unverified rate card, enforced by nothing until Phase 3, and a test locks the voice number in.
- [ ] Stop describing drift 10 as closed while `CREDIT_LIMITS` still grants `SQUAD_YEARLY` 12M credits against a resolver that says `free`, or narrow the table now. Phase 3 deletes it entirely, so recording the overlap is enough.
- [ ] Memoise `resolve()` per request. `check_capability` and `get_quality_tier` resolve independently, and the ask path pays a join per turn it previously avoided with the pre-loaded tier.

### Phase 2b — Port `paystack_service` to SQLAlchemy (launch blocker)

**Runs immediately after Phase 2 and before Phase 3.** Nigeria is the launch market (§6.8), Paystack is its rail, and today `POST /billing/subscriptions/paystack/initialize` and `GET /verify` are unmounted because `paystack_service` holds `PrismaClientRemoved` at `:32`. The webhook is worse than unmounted: `webhooks.py` catches and answers `200`, so **live Paystack events are being silently discarded**.

**The port is smaller than the phase's importance suggests.** 595 lines, but only **nine** Prisma call sites — `:214`, `:405`, `:429`, `:487`, `:489`, `:515`, `:535`, `:538` (all `db_client.user.find_unique` / `find_first` / `update`) and `:565` (`db_client.creditpurchasetransaction.find_first`). Every column they touch already exists on the SQLAlchemy `User` model with an explicit camelCase DB name: `paystack_customer_code` → `"paystackCustomerCode"` and `paystack_subscription_code` → `"paystackSubscriptionCode"` (`identity/db_models.py:69-73`), plus `subscription_current_period_end` (`:64`) and `tier` (`:28`). The three camelCase attribute reads to fix are `user.paystackCustomerCode:424`, `user.paystackSubscriptionCode`, and `user.subscriptionCurrentPeriodEnd:228`.

**Sequencing matters here and revision 3 did not notice it.** `paystack_service` imports two functions that Phase 3 deletes:

```python
from ..services.credit_consumption_service import reset_credits_for_period_start   # :22, called at :435
from ..services.referral_rewards_service import track_referral_subscription        # :23, called at :454
```

Phase 3 deletes `reset_credits_for_period_start` when the monthly credit period gives way to the rolling window, and deletes `track_referral_subscription` outright. Porting both to SQLAlchemy in Phase 2b and deleting them in Phase 3 is wasted work on the critical path. So:

- **`reset_credits_for_period_start:435`** — port the *call*, not the function. Phase 3 replaces the body with a window reset; Phase 2b leaves the existing signature in place and does not touch its internals.
- **`track_referral_subscription:454`** — **delete the call now.** Phase 3 already deletes the function, `referral_rewards_service` holds its own Prisma sentinel so the call cannot work today, and Phase 4b replaces referral rewards with the points ledger, in which a subscription grants nothing (Decision O). Porting it would mean writing SQLAlchemy for behaviour the plan has already withdrawn.
- **`creditpurchasetransaction.find_first:565`** — **delete the branch.** Decision H as revised drops the table, and no credit-pack transaction exists for it to find. `credit_purchase_service.fulfill_purchase` is deleted here rather than ported, which removes the last live reader of `CreditPurchaseTransaction` ahead of Phase 4's migration.

Checklist:

- [x] **The subscriber count, taken 2026-09-01 against production.** `scripts/count_legacy_commercial_state.py` — read-only, committed, re-runnable, so this never has to be re-derived from memory:

  | Check | Count |
  | --- | --- |
  | Users on a retired tier (`PREMIUM_YEARLY` / `STUDY_CIRCLE_*` / `SQUAD_*`) | **0** |
  | Users with a Stripe subscription id | **0** |
  | Users with a Paystack subscription code | **0** |
  | Users with a Google Play purchase token | **0** |
  | Users with a non-zero `purchasedCreditsBalance` | **0** |
  | `CreditPurchaseTransaction` rows with `status = 'completed'` | **0** |
  | `CreditPurchaseTransaction` rows, any status | 9 (all abandoned) |
  | Tier breakdown | `FREE` 1 205, `PREMIUM_MONTHLY` 1 |

  **The product fact is confirmed, and more strongly than stated: there is no payment relationship anywhere in the database.** Not one Stripe, Paystack or Play identifier. The single `PREMIUM_MONTHLY` row carries no subscription id of any kind, so it is a tier set by hand rather than a subscriber — it keeps Plus either way, since `PREMIUM_MONTHLY` is the tier on sale, and its null `subscriptionCurrentPeriodEnd` is a live example of why Phase 2a made `_subscription_lapsed` treat absent as "not lapsed". The nine credit-pack rows are abandoned checkouts; none took money, so Decision H's table drop stands.

  Consequences applied in the same commit, because the resolver and the writers were only ever wrong *apart*: **`LEGACY_PLUS_TIERS` is deleted** (Phase 2a restored it under uncertainty; the uncertainty is now resolved), and `stripe_service._price_id_to_tier` and `paystack_service._plan_code_to_tier` **no longer emit any tier the resolver refuses** — both collapse to `PREMIUM_MONTHLY` or `FREE`. A new test asserts that every tier a writer can produce is a subset of `PLUS_TIERS`, which is the gap the original defect lived in.
- [x] **Ported.** All nine call sites read through `billing_repo`; the `PrismaClientRemoved` sentinel is gone. Three things changed shape beyond the mechanical session work, each recorded in the module docstring: **`db_client` is removed from every signature** (a Prisma client threaded through nine functions so a caller could pass a transaction — nothing ever did, and `billing_repo` owns its sessions, so it was a place for a future bug rather than a capability); **attributes are snake_case** (`user.paystackSubscriptionCode` → `user.paystack_subscription_code`, the class of mistake `test_orm_attribute_names.py` exists to catch); and `datetime.utcnow()` → `datetime.now(UTC)`. Two repository methods added: `find_user_by_paystack_subscription` (a `subscription.disable` identifies the learner by subscription code and nothing else) and `find_user_by_email`.
- [x] **The webhook handlers fail loudly instead of returning quietly.** An unattributable `subscription.create`, a `charge.success` with no reference or user id, a verification that returns `None`, and an unrecognised event all raise — `webhooks.py` turns that into a `500` so Paystack retries. Previously each logged a warning and returned, which `webhooks.py` then answered `200` to: money taken, nothing granted, and an acknowledgement that we had handled it. The one case that still returns quietly is a `subscription.disable` for a code we do not hold, which is benign and idempotent — usually our own `cancel_paystack_subscription` having cleared it first.
- [x] Deleted the `track_referral_subscription` call. Worth noting where it sat: *inside* the `try` whose `except` logs "Failed to send subscription email", so its failure was reported as an email problem.
- [x] Deleted the `CreditPurchaseTransaction` branch of `_handle_charge_success`. Its error handling is the shape this port removed throughout — the whole branch wrapped in `except Exception: log; return` with the comment "webhook should still return 200".
- [x] Deduplicated the cancellation state. `cancel_paystack_subscription` and `_handle_subscription_disable` are the same change reached two ways (the learner asking, Paystack telling us) and held identical copies of the five-column dict; now `_CANCELLED_SUBSCRIPTION_STATE`. Also fixed a latent bug in `cancel`: it read `subscription_current_period_end` for its return value *after* the update had cleared it, so the learner was always told `None`.
- [x] Removed `PREMIUM_YEARLY` from `plan_family_to_tiers`. With the writers narrowed, no code path can produce that tier, so leaving it would have told a monthly subscriber they were "already subscribed to this plan" on the strength of a value nothing can hold.
- [ ] **Deferred, deliberately: `credit_purchase_service.fulfill_purchase`.** Confirmed to have **zero callers** after the branch above was deleted, and `_send_purchase_receipt_email` is called only from inside it — so ~180 lines are now dead. Not removed here because it is dead rather than wrong, Phase 3 drops the tables it reads anyway, and folding a large deletion into the commit that opens the NGN rail makes both harder to review. It also imports from `credit_purchase_notifications`, which the notification branch is actively editing.
- [x] **NGN placeholder fixed.** `_plan_amount_kobo(plan_id)` reads the price from config instead of sending `"10000"` (₦100). Never wrong in production, because a subscription's plan overrides the field — but it stops being inert the moment Phase 5 adds one-time pass charges, where nothing overrides it and ₦100 *is* the price. Setting it now means the pass work adds a call site rather than finding a placeholder.
- [x] `PRICE_NGN_PLUS_PASS_5H = 70_000`, `PRICE_NGN_PLUS_PASS_7D = 180_000`, `PRICE_NGN_PLUS_MONTHLY = 240_000` added to `config.py`, with the ₦2 500 flat-fee reasoning recorded beside them so nobody rounds one up.
- [x] **Both Paystack routes mounted**, and the sentinel comment block replaced by the handlers. Also added `is_modification` / `is_upgrade` to `PaystackInitializeResponse`: the service already computed them and Pydantic was dropping them silently as extra keys, so the client could not tell that an upgrade had been cancel-and-resubscribe. Stripe's response has carried the equivalent pair since it was written.
- [ ] Delete the four `STRIPE_PRICE_ID_STUDY_CIRCLE_*` / `_SQUAD_*` and four `PAYSTACK_PLAN_STUDY_CIRCLE_*` / `_SQUAD_*` settings (§5.1), and the Study Circle / Squad branches of `_plan_code_to_tier`, `_price_id_to_tier` and `_assert_price_id_is_active`. `DEPRECATED_PLAN_IDS` keeps all six ids so a stale client still gets `410`.
- [x] Inverted `test_billing_routes_mounted.py`'s sentinel guard. **It earned its keep**: it asserted the sentinel was present specifically so that porting the service without mounting the routes would fail loudly, and that is exactly what happened during this phase. It now asserts `paystack_service` has no `db` attribute, so nothing can quietly reintroduce a Prisma client.
- [ ] Tests: initialize returns an authorization URL with the NGN amount from config; verify promotes a `FREE` user to `PREMIUM_MONTHLY` and sets `subscription_current_period_end`; `charge.success` on a renewal extends the period; `subscription.disable` returns the user to `FREE`; a webhook whose signature fails answers non-`200` and logs; a retired plan id answers `410` on the Paystack door as well as the Stripe one (already asserted in Phase 1 — keep it green through the port).

### Phase 3 — Reprice voice, redenominate usage, add windows

**Runs first among the implementation phases.** The free-voice exposure in §6.2 is live and costs roughly $150/month per free user who finds it. The one-line mitigation — raise the voice price — can ship ahead of everything else in this phase.

- [ ] **Immediate:** reprice live voice against measured cost. This is the single highest-value line in the plan and does not depend on anything else in it.
- [ ] Migration `063` part one: `usageWindowStartedAt`, `usageWindowUnitsUsed`, `usageMonthStartedAt`, `usageMonthUnitsUsed` on `User`; drop the nine retired credit columns.
- [ ] Introduce the cost-denominated `usage_unit` (§6.2). Deduct **measured** COGS from `cost_calculator.py` rather than a fixed per-operation table. Delete `TOKEN_MULTIPLIER`, `apply_token_multiplier`, `CREDIT_COSTS` and `CREDIT_LIMITS`.
- [ ] Rewrite the **non-space path** of `check_credit_availability` and `consume_credits` against window + monthly backstop. Delete `initialize_user_credits`, `reset_daily_credits_if_needed`, `ensure_credit_period`, `reset_credits_for_period_start`. **Leave the `space_id` early-return at `:334-343` and the space branch of `consume_credits` exactly as they are** — both return before any of the deleted machinery is reached, which is what makes this separable.
- [ ] Pre-flight estimates for voice: `min_session_credits()` becomes a units estimate at the real rate, so a session cannot start that the allowance cannot fund.
- [ ] Delete `billing.reset_credit_periods` and `progress.daily_credit_reset` (closes drift 13).
- [ ] **Delete `referral_rewards_service.get_daily_limit_increase`, `claim_referral_reward`, `get_claimable_rewards`, `track_referral_subscription` and `REFERRAL_REWARDS`.** Nothing tops up a window (§6.3). The module's `PrismaClientRemoved` sentinel means none of this runs today, so there is no behaviour to preserve — keep only `generate_referral_code`, `get_or_create_referral_code`, `track_referral_signup` and `get_referral_stats`, ported to SQLAlchemy, as the input to Phase 4b. **Phase 2b has already deleted the only caller of `track_referral_subscription`** (`paystack_service.py:23, 454`), so this deletion is unblocked by the time it runs.
- [ ] `GET /billing/usage` returning percentage + reset time only; rewrite `GET /users/usage` onto the same shape and delete the credit-balance fields.
- [ ] Refusal messages carry `windowResetsAt`. Delete the monthly soft-cap, daily-limit and purchased-balance message bodies at `check_credit_availability:378-455` and the `ask_service:913-924` refusal copy.
- [ ] `LimitReachedEmailLog` dedupe key moves from period to window.
- [ ] Tests: window opens on first use, resets on first use after elapse, does not reset on a read, 80% warning carries the timestamp, monthly backstop binds, a voice minute deducts ~200 units, free voice is capped at ~2.5 min/window. Scope guard: `test_circle_billing.py` and `test_seat_service.py` pass unmodified.
- [ ] **Verify `cost_calculator._EXACT_MODEL_PRICING:31-52` against live provider rates before anything else.** It prices `gemini-3.5-flash` at $0.50/$3.00 where published rates are ~$1.50/$9.00. Every figure in §6.6 and §6.8 moves ~3× on the answer. Five minutes of work, largest single effect in the plan.
- [ ] Instrument before tuning: log units per operation and per user so the §6.7 assumptions can be replaced with measurement inside a month.

### Phase 3b — Meter everything else (Decision L)

Without this, §6.7's contribution is roughly **−$270 at 10 000 MAU** rather than +$754. It is not optional and it is not a follow-up.

- [ ] **Plumb usage metadata through `llm_resilient`.** The wrapper currently discards the provider response and returns text only, so no caller can see token counts. This is the prerequisite for every line below.
- [ ] Meter inside the attempt loop at `llm_resilient.py:249-320` — one chokepoint covering 26 of 31 call sites, and it counts retries because retries cost (Decision L).
- [ ] Redirect the four in-scope stragglers through it: `memory_impl.py:49`, `planning_impl.py:55`, `schedule_regen_impl.py:213` and `:227`. Leave `space_impl.py:1324` (Decision F).
- [ ] Shorten the retry/fallback chain. Three attempts × three providers = up to nine billable calls for one operation; that is a cost decision now, not only a reliability one.
- [ ] Delete the dead `CREDIT_COSTS` entries `ai_course_generation` and `ai_action` — cost is measured, not tabulated.
- [ ] **Cache home guidance.** `guidance_engine.py:267` fires on every home load; `growth_service` and `goal_insight_service` already avoid this through `narrative_cache`'s `inputs_hash`. Reuse it. Uncached this is ~$2.10/month per active learner, more than a Plus subscription's whole margin.
- [ ] **Audit `max_tokens` on all 26 operations.** Growth narrative, growth drivers and goal insight each budget **8 192 output tokens for a paragraph**, at 6× the input rate. Setting those three to 1 500 is a ~5× cut on the most-opened panels in Reflect.
- [ ] Exempt onboarding auto-setup and memory extraction from charging, on principle (§6.6).
- [ ] Charge on success, absorb on failure, following `study_voice/notes.py:172-220`.
- [ ] Tests: every operation in the §6.5 table deducts units; a retry storm is counted not swallowed; a failed generation charges nothing; onboarding and memory extraction charge nothing.

### Phase 3c — Background AI (Decision M)

- [ ] `learning.generate_recommendations` (nightly) and `learning.generate_reflections` (weekly) meter against a **proactive sub-budget capped at 20% of the monthly backstop**.
- [ ] Cadence by entitlement: discovery recommendations weekly for Free, nightly for Plus. Free weekly reflections composed deterministically from `weekly_summary.py` with no model call; the LLM narrative becomes the Plus version, which `reflection_service.py:173-174` already branches on.
- [ ] **Dormancy stop:** no proactive generation without activity in the preceding 7 days. `list_active_profiles` currently means "a profile row exists", which is why we generate nightly recommendations for people who left in March.
- [ ] Tests: a dormant learner triggers no LLM call; the proactive sub-cap cannot consume a learner's session allowance.

### Phase 4 — Passes

- [ ] Migration `063` part two: `PlusPass`, `PlusPurchase`, the pass and Apple columns on `User`, the partial unique index.
- [ ] SQLAlchemy models in `billing/db_models.py`; add `test_field_mapping_completeness` entries.
- [ ] `pass_service.py`: `grant(purchase)`, `activate(user_id, pass_id)`, `list_passes(user_id)`, `expire(pass_id, reason)`, `revoke(purchase_id)`. Activation resets the usage window (Decision E).
- [ ] `GET /billing/passes`, `POST /billing/passes/{id}/activate`.
- [ ] Celery beat: `billing.sweep_expired_passes` every 5 minutes.
- [ ] Notifications: pass activated (with expiry time), 30 minutes remaining, pass ended.
- [ ] Tests: one-active invariant under concurrency, `PASS_REDUNDANT` for each of the three reasons, pass grants every Plus capability for its duration and none after, activation resets the window, expiry forfeits remaining time. Scope guard: an activated pass does not change any space-scoped read.

### Phase 4b — Points (§6.9, Decision O)

Depends on Phase 4 and on Phase 3b, which is what makes "active" measurable.

- [ ] Migration `063` part three: `PointsLedgerEntry`, `User.pointsBalance`, `PlusPass.source`, `PlusPass.purchaseId` made nullable. Constants: `POINTS_PER_QUALIFIED_REFERRAL = 100`, `POINTS_EXPIRY_DAYS = 60`, `POINTS_COST = {"plus_pass_5h": 100, "plus_pass_7d": 250}`.
- [ ] `points_service.py`: `grant(user_id, points, kind, source_ref)` (idempotent on the unique index), `balance(user_id)`, `redeem(user_id, product_id)` → `pass_service.grant(source="points")`, `expire_due()`. Redemption spends the oldest live grant first and writes one negative entry per grant it consumes, so the ledger explains itself.
- [ ] **Qualification job**: a referred learner is qualified on their **7th distinct day with a billable operation**. Evaluate from the usage records Phase 3b creates — not from `lastLoginAt`, which an app open satisfies. Daily Celery task, idempotent, granting once per referred learner.
- [ ] Celery beat: `billing.expire_points` nightly, writing the negative entries. Lazy expiry on read as well, so a stale sweep can never let expired points be spent (the same belt-and-braces as the pass sweep, Decision E).
- [ ] Notify once, 7 days before the oldest grant expires, **and only if that grant alone can still buy a pass**. No notification for an unspendable remainder.
- [ ] `GET /billing/points`, `POST /billing/points/redeem`, `GET /billing/referrals`. The redeem endpoint accepts the two pass ids and has no subscription branch to remove later (Decision O).
- [ ] Monitoring: qualified referrals per referrer per week, and total live points as a forecastable COGS liability. There is no cap, so the alert is the control.
- [ ] Tests: 100 points buys a 5-hour pass and leaves zero; 249 points cannot buy the 7-day pass; a grant 61 days old cannot be spent even if the sweep has not run; redemption is FIFO across three grants with different expiries; the same referred learner cannot grant twice; **six days of activity grants nothing and the seventh grants exactly 100**; seven logins with no billable operation grant nothing; **no request can produce subscription time from points** — assert on the redeem endpoint's accepted product set.

### Phase 5 — Purchase rails

**iOS is in this phase, not after it.** Open Question 6 is resolved — iOS ships with Android — so the Apple work below is a peer of the Google work, not a contingency. §5.7 holds the creation timeline and the reason the console tasks come first.

**Store setup, before any verification code** (§5.7). These are console and agreement tasks with external lead times, and every server task below needs the product ids to exist:

- [ ] **Apple, start now — earlier than this phase if possible.** App Store Connect app record for `com.maigie`; In-App Purchase enabled on the **App ID** (a developer-portal capability, *not* an entitlements-file change — see §5.6); App Store Server API key generated (`APPLE_ISSUER_ID`, `APPLE_KEY_ID`, `APPLE_PRIVATE_KEY`); **Paid Apps agreement active with banking and tax complete**. No in-app purchase product can be created until that agreement is active, and it needs details an engineer cannot supply.
- [ ] **Apple products**: `com.maigie.plus.pass5h` and `com.maigie.plus.pass7d` as **Consumable**; `com.maigie.plus.monthly` in subscription group `maigie_plus` with a **3-day introductory free trial**; NGN prices per §6.8.
- [ ] **Google Play products**: `plus_pass_5h` / `plus_pass_7d` as **consumable** in-app products; `plus-monthly` base plan stays at `$4.99` with its **free-trial offer set to 3 days**; NGN prices per §6.8.
- [ ] **Delete, do not repurpose** (§6.1): the Play `plus-yearly` base plan and the three `credit_pack_*` consumables, plus `GOOGLE_PLAY_BASE_PLAN_YEARLY` and the three `GOOGLE_PLAY_SKU_CREDIT_*` settings (`config.py:264-268`) and the branches reading them at `google_play_service.py:65, 191-193`. Nobody has bought any of them, so there is no RTDN history to decode. Archive the Stripe yearly price.
- [ ] **Store trial parity check**: the App Store Connect introductory offer, the Play base-plan free trial, the Stripe `trial_period_days` and `config.TRIAL_DAYS_MAIGIE_PLUS` all read **3**. Four places, two of them consoles, no test covers them — check by hand and record the check here with a date.

Server rails:

- [ ] **Stripe**: one-time Checkout for passes (`mode: payment`), existing `$4.99` subscription price reused, Apple Pay + Google Pay + Link enabled in the dashboard. `checkout.session.completed` → `PlusPurchase` → `pass_service.grant`.
- [ ] **Paystack**: NGN one-time charges for both passes, using the `PRICE_NGN_*` settings Phase 2b adds; extend `handle_paystack_webhook`. Depends on Phase 2b — there is no NGN rail to extend until the port lands.
- [ ] **Google Play**: `purchases.products.get` verification in `google_play_service.py` — `verify_product_purchase` is left in place from Phase 1 precisely as the basis for this, since the `purchases.products.get` call and the token-replay check are both reusable; extend RTDN for `SUBSCRIPTION_*` and voided-purchase revocation. Mount the replacement as `POST /billing/purchases/google-play/verify`.
- [ ] **Apple** (new domain code): `apple_service.py` — App Store Server API client, JWS verification of `signedTransactionInfo` against Apple's root CAs, `POST /billing/purchases/apple/verify`, `POST /webhooks/apple` handling `DID_RENEW`, `EXPIRED`, `REFUND`, `REVOKE`, `CONSUMPTION_REQUEST`. Config: `APPLE_ISSUER_ID`, `APPLE_KEY_ID`, `APPLE_PRIVATE_KEY`, `APPLE_BUNDLE_ID`, `APPLE_ENVIRONMENT`.
- [ ] Idempotency and abuse tests: replayed token grants nothing; token bound to user A rejected for user B with `409`; refund revokes an active pass mid-run. Run the set against **both** stores, not just Play — Apple's `REVOKE` and Google's voided-purchase RTDN are different shapes reaching the same revocation path.

**Testing does not wait for App Review** (§5.7). Play internal-testing track and an Apple Sandbox Apple Account or StoreKit configuration file both exercise unapproved products end to end, so every item above can be verified before the first submission. Review gates the launch, not the phase.

### Phase 6 — Close the drift list, and wire conversion to guidance (Decision N)

- [ ] `next_action` gains `requiresPlus`, `capability`, `upgradeValue`, populated by asking `feature_tier_service.check_capability` about the action `guidance_engine` **already chose**.
- [ ] Connect `conversion_engine.evaluate_triggers` to `guidance_engine` / `home_service`. They currently reason about the same learner independently and have never met.
- [ ] Every gated recommendation renders a free alternative beside it, never instead of it (Principle Three — the learner always has a next action).
- [ ] Suppress offers a learner cannot act on: trial exhausted, no pass owned **and no redeemable points** means no gated recommendation. A learner with 100+ points can act, and the offer is redemption rather than a price.
- [ ] One offer per window, existing 30-day per-capability cooldown retained, two dismissals silence that capability for 90 days.
- [ ] Copy carries the learning reason, never a sales line. No interstitials, no banners, no upgrade prompt on app open, nothing in a notification.
- [ ] **Test that guidance is entitlement-blind when choosing**: identical state must produce an identical `next_action` for a free and a Plus learner. The gate lookup happens after the choice, never during it.
- [ ] `ConversionTriggerLog` already records `shownAt` / `dismissedAt` / `convertedAt` / `capabilityHighlighted` — surface conversion-by-capability so copy is decided by measurement.
- [ ] **Replace `conversionMomentsMock` with real `conversion_engine` output at the four mount points that already exist** (drift 21): `StudyPlanCreatePage`, `WeeklyGrowthReflectionPage`, `GrowthTrendsPage`, `DocumentsPage`, plus the global `SidebarCommercialExperience`. The placements are already right; only the content is invented. Do not add new surfaces.

**Drift list:**

- [ ] Enforce file uploads 5/month (drift 1) and AI summaries 10/month (drift 2) against the existing columns. `403 UpgradeRequiredDetail`.
- [ ] Wire `check_capability("study_plan", "adaptive")` (drift 9) — `200 + LockedNotice` on the plan response.
- [ ] Enforce notification lead time: 15 minutes for Plus, 60 for Free (drift 4, first half).
- [ ] Convert value summary to `200 + LockedNotice` (drift 12).
- [ ] Publish which depth was served on reflections and behaviour profiles, so a Free learner's page can say so (closes `REFLECT_API_INTEGRATION_PLAN.md` §5.5 defect 11).

### Phase 7 — Clients

**Web**
- [ ] Delete `features/credits/**`, the three credit-pack pages, and the personal half of `features/subscription/data/plan-data.ts`. **`CIRCLE_PRODUCTS` and the Circle/seat UI stay** (Decision F).
- [ ] `features/commercial`: catalogue hook, `usePasses`, `useActivatePass`, `useEntitlement`, `useUsageWindow`.
- [ ] Pass wallet on `/subscription`: inventory, one-tap activate, active-pass countdown.
- [ ] Window meter: allowance used, remaining, reset time. Replaces the credit-balance UI.
- [ ] Collapse all **five** locked cards into `UpgradeRequiredPanel`; add owned-pass activation, points redemption, and the window-cap variant (Decision J).
- [ ] Points wallet, in the same screen as the pass wallet rather than a separate "Earn" section: balance, what it buys, the next expiry date from the server, referral code, and each pending referral's days-active progress. Replaces `EarnPage` / `EarnReferralsPage` rather than reviving them.
- [ ] Fix `SubscriptionPage.tsx:18` and `:182` to read the resolved entitlement; delete the hardcoded `$1.99`, `purchasedCreditsBalance: 250`, and the two dead `/credits/*` links.
- [ ] **Rewrite `pages/settings/UsageSettings.tsx` onto `GET /billing/usage`** — percentage and reset time, no token counts (drift 15). It currently renders a `USAGE_DATA` literal to live learners.
- [ ] **Document studio (drift 16):** mark `docx` / `pptx` and the report/minimal styles as Plus in the picker from `/capabilities`, and read `getApiError(...).upgrade` so the 403 renders `UpgradeRequiredPanel` the way `CourseCreatePage` and `PreparePracticePage` already do.
- [ ] **Ask Maigie (drift 18):** render the window-limit state distinctly from a network error — it already stores `failureCode: 'CREDIT_LIMIT'` and nothing branches on it. Show `windowResetsAt`, and the pass-activation action when one is owned. Surface `credit_info` frames carrying only a balance.
- [ ] **Study Mode (drift 19):** the `402` and `credit_limit_error` paths get the reset time and an action instead of a bare sentence.
- [ ] Persist `TrialBanner` dismissal (drift 17) — currently local state defaulting to `true`, so it returns on every reload.
- [ ] Delete the six unrouted credit/earn pages and the hardcoded credit-reward copy at `UploadResourceModal.tsx:298` (drift 20), including the ad-reward UI inside `EarnPage` (Decision O).
- [ ] Reconcile `pages/settings/AiModelSettings.tsx:13-25` with the tier allowlist, or remove the picker (drift 22).

**Mobile**
- [ ] Generalise `usePlayBilling` → `useStoreBilling`: remove the `Platform.OS !== 'android'` early return, add the StoreKit path, drive SKUs from the catalogue, render store `displayPrice` (Decision I).
- [ ] Rewrite `SubscriptionScreen` for the four personal products; delete the `PLANS` literal.
- [ ] Pass wallet screen; replace `src/app/earn/buy-credits.tsx`. Window meter in `src/app/profile/usage.tsx`.
- [ ] **Delete `src/app/earn/watch-ad.tsx`** and any ad-reward call site (Decision O). Rebuild `src/app/earn/` as the points wallet: balance, referral code and share sheet, pending referrals with days-active progress, next expiry date, redeem-to-pass action.
- [ ] Pass paywall copy says "your personal workspace", not "everywhere" (Decision F).
- [ ] **iOS — corrected in revision 4.** Revision 3 listed "`expo prebuild` for `ios/`, StoreKit capability, App Store Connect app record and three products, uncomment the iOS EAS jobs". Three of those five were wrong or misplaced:
  - ~~`expo prebuild`~~ — already run; `ios/` exists with `Maigie.xcworkspace` and installed Pods, and is gitignored (`.gitignore:12`) because EAS regenerates it per build. Nothing to do, and nothing to commit.
  - ~~StoreKit capability~~ — not an entitlements change. In-App Purchase is a capability on the **App ID** in the developer portal; `ios/Maigie/Maigie.entitlements` correctly holds only `aps-environment` and `applinks:app.maigie.com`. Moved to Phase 5's store-setup block, where it belongs with the other portal tasks.
  - ~~App Store Connect record and three products~~ — moved to **Phase 5** (§5.7). They gate the server verification code, so they cannot sit in the last client phase.
  - [ ] **Add the StoreKit branch to `useStoreBilling`.** This is the real iOS work and it was buried. `react-native-iap`'s iOS pod (`NitroIap`) is already linked in `ios/Podfile.lock`, so the native module is present and building — the only thing stopping iOS purchases is the `Platform.OS !== 'android'` early return at `usePlayBilling.ts:88-93`, which refuses the platform its own dependency supports.
  - [ ] **Add `submit.production.ios` to `eas.json`** (`appleId`, `ascAppId`, `appleTeamId`). The `submit.production` block currently holds `android` only.
  - [ ] Uncomment the iOS EAS jobs at `.eas/workflows/deploy-production.yml:72-108` (`get_ios_build`, `build_ios`, `submit_ios_build`, `publish_ios_update`).
  - [ ] Submit the first iOS build **with the three IAP products attached** — Apple reviews in-app purchases against a build, and this is the highest rejection-risk submission the project will make. Budget 1–2 weeks and expect one rejection round.

### Phase 8 — Copy, and existing customers

- [ ] `maigie-public/plan-data.ts`: four personal products, new prices, **`trialDays: 3`** on monthly only. Delete `CreditPacks.tsx`. **`CIRCLE_PRODUCTS` and `CircleProductsSection.tsx` stay** (Decision F).
- [ ] Referral and points copy states the qualification plainly — "when they've studied on 7 different days" — and the expiry plainly. A reward whose condition is in the small print produces support tickets from exactly the learners we most wanted to reward.
- [ ] Remove every "watch an ad" and "earn credits" claim from the public site and the FAQ (Decision O).
- [ ] Rewrite `PRICING_COMPARE_ROWS` against §5.3 — remove the five unenforced rows, state the window allowance instead of "unlimited", leave the three Circle rows alone.
- [ ] Fix the duplicated credit-pack prices in `landing/Pricing.tsx`; rewrite `content/faq/pricing-and-plans.yaml`, which still sells Study Circle at $9.99 and Squad at $14.99 — both retired personal tiers, not the live Circle Plan.
- [ ] Test asserting `plan-data.ts` matches `GET /plans/catalog` (Decision I).
- [ ] **No price migration is needed.** `PREMIUM_MONTHLY` stays at $4.99, so no Stripe price migration, no Play notice, no Apple consent flow.
- [x] ~~Query for live `PREMIUM_*` / `STUDY_CIRCLE_*` / `SQUAD_*` subscriptions and non-zero `purchasedCreditsBalance`, and delete the grandfathering machinery if all are zero.~~ **Moved to Phase 2b, first item.** It was the precondition for decisions taken in Phases 2, 4 and 5, so running it in the final phase meant every earlier phase had to hedge against its answer. That hedging *was* the grandfathering machinery. `LEGACY_PLUS_TIERS` is now never written (Decision B), and `CreditPack` / `CreditPurchaseTransaction` are dropped in Phase 4 (Decision H) rather than conditionally surviving to here.
- [ ] ~~Migrate grandfathered legacy subscribers onto `plus_monthly`.~~ **Deleted — there are none.** If Phase 2b's count comes back non-zero, this step returns along with `LEGACY_PLUS_TIERS`.
- [ ] Set the **§6.8 NGN prices** on Play, App Store Connect and Paystack: ₦700 / ₦1 800 / ₦2 400. All three sit under Paystack's ₦2 500 flat-fee threshold, deliberately — do not round any of them up.
- [x] ~~Fix the 100 NGN placeholder amount at `paystack_service.py:333`.~~ **Moved to Phase 2b**, and the line reference corrected to `:317-323` — `:333` is inside the `httpx` call, not the payload. It cannot wait until the copy phase: the placeholder is harmless for subscriptions (the plan overrides the amount) and becomes the actual price the moment Phase 5 adds one-time pass charges.
- [x] ~~Add `PRICE_NGN_*` settings.~~ **Moved to Phase 2b**, for the same reason — the NGN rail needs them to charge correctly.
- [ ] Make `GET /plans/catalog` currency-aware rather than USD-only, serving the `PRICE_NGN_*` values Phase 2b added. Store-purchased products display the store's own `displayPrice` regardless (Decision I); this is for the web rail and for copy.

## 11. Open questions

**Resolved, recorded so they are not reopened.** *Is there pass-versus-subscription arbitrage?* No — the per-day ladder is $4.75 / $0.356 / $0.166 and three 7-day passes already exceed a month. An earlier draft claimed otherwise and was wrong. *Should monthly be $5.00?* No, §6.1. *Should passes be unmetered?* No, Decision E. *Does the 7-day trial survive the 7-day pass?* **No — the trial is 3 days** (§6.1). *Should referrals stay capped at 10/month?* **No — the cap is removed; the 7-day qualification is the control** (§6.9). *Should rewarded ads be re-pointed at the window?* **No — withdrawn** (Decision O). *Can points buy the subscription?* **No, and not by validation but by construction** (Decision O). *Does iOS ship after Android?* **No — together** (question 6). *Is anything grandfathered?* **No — there are no subscribers**, so `LEGACY_PLUS_TIERS`, the retired tier settings and the credit tables are deleted rather than carried (Decision B, Decision H, §5.1); the count is verified in Phase 2b before any of it is acted on. *Was Phase 0 quietly done?* **No — verified open against the code**, and its scope corrected: it blocks allowance tuning, not every phase.

1. **Who owns the `max_tokens` audit?** Growth narrative, growth drivers and goal insight each budget 8 192 output tokens to write a paragraph, at 6× the input rate — 89% of each operation's cost. Twenty-six operations have never had these numbers reviewed. This is the single largest lever in §6.8, it is invisible to learners, and it is not a commercial change, which is why nobody has picked it up.
2. **Is 8 000 input tokens per chat turn necessary?** `HISTORY_LIMIT = 12` plus the enrichment block. Halving it lifts contribution ~60% at every tier simultaneously and no learner can perceive it. Same ownership problem as question 1.
3. **Is `cost_calculator._EXACT_MODEL_PRICING` current?** It says `gemini-3.5-flash` is $0.50/$3.00 per 1M; published rates are ~$1.50/$9.00. Every COGS figure in this document moves 3× on the answer, in whichever direction. **Answer this before tuning any allowance.**
4. **Is the free tier affordable at scale?** After the §6.5 fixes, free inference is still $940 of $2 267 revenue at 10 000 MAU. The model rests on two unmeasured assumptions — 50% of free MAU AI-active, and typical consumption around half the allowance. If either is materially higher, contribution goes negative again. Instrument before tuning (Phase 3, last item).
5. ~~**Does the 3-day trial still need the 180-day cooldown?**~~ **Decided: 90 days.** The 180-day figure was sized for a 7-day trial; a 3-day trial is a much smaller giveaway, and a learner who trialled in January and returns in May is one we want to re-engage rather than turn away. `trial_service.TRIAL_COOLDOWN_DAYS` is now 90 and is the single source — `feature_tier_service._trial_available` reads it instead of repeating the number, so eligibility as shown and eligibility as enforced cannot drift. Still a retention question with no data behind it: watch whether second trials convert at all before treating 90 as settled.
6. ~~**When does iOS ship?**~~ **Decided: iOS ships with Android.** No Android-first phasing, and the Apple work in Phase 5 does not defer. Two premises of the original question were also wrong: `ios/` **does** exist as a gitignored Expo prebuild with `react-native-iap`'s `NitroIap` pod already linked, so the native side of StoreKit is in place and the gap is the `Platform.OS !== 'android'` early return at `usePlayBilling.ts:88-93` plus console configuration (§5.6). What remains true is the schedule risk: Apple review is 1–2 weeks with real first-submission rejection risk, and **IAP products are reviewed attached to a build**. That makes the App Store Connect record, the Paid Apps agreement and the three product records the longest-lead items in the plan, which is why §5.7 starts them in parallel with Phase 2b rather than waiting for Phase 5.
7. **Refunds on an activated pass.** Apple and Google decide refunds unilaterally and neither asks first, so a learner can consume most of a pass and be refunded. `CONSUMPTION_REQUEST` (Apple) lets us report usage and reduces this, but it is advisory. Recommend accepting the leakage and measuring it — a consumption cap that fires on a legitimate learner is worse than the loss.
8. **Is a 5-hour window right for Free, or should Free be longer?** Five hours means a Free learner can reach up to 4.8 allowances a day, which the monthly backstop bounds but does not prevent. The length is shared with Plus for explainability and because it is the pass duration. A 12-hour Free window (~2/day) tightens it at the cost of two numbers to explain. Recommend 5h for both, and let question 1's instrumentation decide.
9. **Should the 5-hour pass be shown next to the 7-day pass?** $1.50 more buys 33× the duration, so the 5-hour pass is value-dominated for anyone uncertain about how long they need. Its job is the sub-$1 impulse and the first card on file, not volume. Displaying them side by side with equal weight makes the cheap one look silly; surfacing the 5-hour pass contextually — at a paywall, mid-session — is probably where it earns its place.
10. **Is 250 points the right price for the 7-day pass?** 100 and 250 mirror the cash ratio ($0.99 : $2.49), which is tidy but arbitrary — nothing says an earned currency should price like a sold one. 200 would make two referrals buy the better product cleanly and would push learners toward the 7-day pass, which is the one that actually establishes a study habit. 300 would make the 5-hour pass the default redemption and leave a remainder to expire. Recommend 250 for launch and watch which pass gets redeemed.
11. **Should contributing a resource earn points?** `ResourceUploadReward` exists and the UI already promises 1 500 credits for an approved upload. Contribution is a better earn source than referral in principle — it produces something other learners use — but "approved" implies a moderation process that does not exist, and points redeemable for real product make an unmoderated upload queue an attack surface. Deferred, deliberately, until moderation exists.
12. **What is the total points liability?** Every live point is deferred COGS at up to $0.003 (100 points → a $0.30-ceiling pass). Uncapped referrals make this unbounded in principle and 60-day expiry bounds it in practice, but the missing number is qualified referrals per learner. Until Phase 4b's monitoring runs, this is a guess.
