# Maigie Plus Commercial Plan

> Status: **Phases 0–3 done. Prices are live at $9.99. No product is buyable by anyone, because no store product exists and there is no one-time checkout.**
>
> Where the money path actually stands. The `billing` router is mounted and all three rails are reachable: Stripe, Google Play and Paystack, the launch market's rail. **Entitlement is one resolver**: `entitlement_service.resolve` is the only thing that decides whether a learner is Plus. All three webhooks fail closed. **Usage is metered in cost units against a 5-hour rolling window** — `067_usage_windows.py` is the migration, `TOKEN_MULTIPLIER` and `CREDIT_LIMITS` are gone, and `usage_note` carries real counts again because something now counts them. **There is no payment relationship anywhere in the database** — zero Stripe subscription ids, zero Paystack codes, zero Play tokens, 1 205 `FREE` users and one hand-set `PREMIUM_MONTHLY` (Phase 2b, re-runnable via `scripts/count_legacy_commercial_state.py`). Nothing is grandfathered because there is nobody to grandfather.
>
> **What blocks a learner paying us is now entirely Phase 4 and Phase 5.** There is no `PlusPass` table, so nothing can hold a pass; there is no one-time checkout, so **no pass can be bought on any rail**; and **no store product exists in any of the four providers.** §5.7 is the provider-by-provider creation order and it holds the longest external lead times in the plan — the Apple Paid Applications agreement gates everything on iOS and is not an engineer's to supply.
>
> **Also outstanding from revision 9: the voice counters do not exist.** §6.3 unbundles live voice onto `voiceSecondsRemaining`, and the catalogue's voice figures are still derived from the *unit* window because that is what the meter does today. The notes are honest about the running meter; they do not yet describe the design. `plus_voice_30` cannot ship before the counter does.
>
> **The commercial model, current as of revision 10.** Six personal products — Free, a 5-hour pass, a 7-day pass, Plus Monthly at **$9.99**, an NGN-only 4-month Term Pass, and a 30-minute voice pack. Prices are set per market rather than converted (§6.8). Usage is a 5-hour rolling window denominated in **cost units**, one unit being $0.0001 of measured COGS, with live voice on its own counter (§6.3). **Decision Q is the rule that holds the economics together**: COGS is quoted from the allowance cap, never from a per-operation estimate — a cap denominated in dollars cannot be wrong about what it costs, however wrong the rate card is. **Decision R is the rule that stops the catalogue growing**: voice is the only top-up, because the pass ladder already is the top-up for everything metered.
>
> At 10 000 MAU the model returns **+$463 at 40% in Nigeria** and **+$2 367 at 72% globally** (§6.11). Contribution excludes infrastructure and salaries, so it is not profit. **The two markets do different jobs**: global buys runway at $0.237 contribution per MAU — a lean two-engineer team at ~17 000 MAU — while Nigeria buys product-market fit and needs ~87 000 MAU for the same team. Every payer rate in this document is a guess against zero payment history, and the first real number replaces all of them.
>
> **The one unexploited structural advantage is out of scope here and has its own document.** Nigeria's national syllabi (JAMB, WAEC, NECO) mean generated study content is near-identical across thousands of learners, so free-tier COGS could become a function of syllabus rather than of learner. That is the largest remaining cost lever in the business and it is designed in [`SHARED_CURRICULUM_CACHE_PLAN.md`](./SHARED_CURRICULUM_CACHE_PLAN.md), not here.
>
> Owners: Backend (catalogue, entitlement, usage windows, store verification) + Web client + Mobile client + Public site
> Scope: the **personal** product catalogue, purchase rails on three surfaces, the pass activation model, the rolling usage window that replaces daily and monthly credit caps, the earned-points ledger that redeems into passes, and one entitlement resolver that every personal-scope gate reads.
> **Out of scope: Learning Spaces, entirely.** Not the Space feature, not Circle Plan, not the Plus Seat add-on, not `SpaceMember.seat_tier`, not `seat_impl.py`, not `Space.credits`, not the space branch of `consume_credits`, not the space branch of `feature_flags.effective_tier_for_request`. Nothing in this plan reads or writes anything space-scoped. See Decision F.
> Companion documents: [`../../../maigie-client/docs/PREPARE_API_INTEGRATION_PLAN.md`](../../../maigie-client/docs/PREPARE_API_INTEGRATION_PLAN.md) (§4 defers checkout to this document), [`../../../maigie-client/docs/REFLECT_API_INTEGRATION_PLAN.md`](../../../maigie-client/docs/REFLECT_API_INTEGRATION_PLAN.md) (Decision Z, the locked-read convention)
> Source of authority for pricing intent: the Maigie Book — `business/ch36-pricing-philosophy`, `business/ch37-personal-learning`, `philosophy/ch04-product-principles`. Where this plan and the book disagree, the book wins and this plan is wrong. Decision N and §6.7 are derived from them directly.
>
> **Revision log.** Only decisions that still bind are listed; the reasoning for each lives in the section or Decision it names, not here.
>
> | Rev | Change | Where |
> | --- | --- | --- |
> | 11a | **Gemini is the only provider, and `LLM_ENABLED_PROVIDERS` did not reach `llm_resilient`.** Found while checking what deactivating OpenAI would actually do: nothing, for the 27 generation surfaces. The switch is read by the chat path only, so a disabled provider stayed reachable as a fallback and became the *primary* for any learner whose `preferred_llm_provider` named it; only an unset API key stopped it, by accident, at the cost of three timed attempts against a missing credential. Both the primary and the fallback list are now filtered, and the default is `"gemini"`. This makes the untiered-fallback gap unreachable and retires "shorten the retry chain" as a cost item — worst case is three Gemini attempts, not nine mixed calls. | §6.3, Phase 3b |
> | 11 | **Drift 23 closed: the model-quality split reaches all 27 AI surfaces, not just chat.** Implementing Decision P found three things this document had understated — six of the 26 call sites were not on the metering chokepoint at all (so no meter, no gate, no retry, no tier), the most expensive operation in the product *cannot* use the chokepoint because grounded search has no fallback provider, and 23 of 27 sites were unlabelled, which the split is keyed on. Two documented figures were wrong: §6.10's roster named `gemini-3.5-flash-lite` at `gemini-3.1-flash-lite`'s price, and Decision P's enumeration omits course generation despite Decision R pricing it at twice the threshold. `document_impl` was reporting a `402` refusal as a `502`. | drift 23, Decision P, §5.2, §6.3, §6.10 |
> | 10a | **Revision 10 implemented.** Prices and allowances are live in `config.py` and `entitlement_service.py`; `PRICE_NGN_PLUS_PASS_7D` corrected 180 000 → 150 000 kobo. The test that pinned $4.99 is replaced by two that pin the *ladder* rather than the numbers — `test_the_subscription_is_the_best_value_per_unit` and `test_stacking_passes_never_beats_the_subscription` — so a future price change is free to move both halves as long as it keeps them consistent. 4 414 tests pass, Ruff clean, `openapi.json` unchanged because a price is data rather than schema. | `config.py`, `entitlement_service.py` |
> | 10 | **Plus Monthly $4.99 → $9.99, 7-day pass $2.49 → $3.99.** The "decisive" argument for $4.99 was that no subscriber should see a price-increase flow, and Phase 2b proved there are no subscribers — the change costs nothing now and becomes permanently expensive at the first signup. Monthly backstop 20 000 → 36 000 units and 5-hour pass 3 000 → 2 000 to keep the per-unit ladder ordered. Payer rate assumption 8% → 6% as the elasticity cost. NGN prices unchanged. | §6.1, §6.3, §6.4, §6.7 |
> | 9 | `plus_voice_30` added — the voice top-up that revision 8 promised in five places and never created, which left a subscriber out of voice minutes with nothing to buy (Decision D forbids activating a pass while Plus is active). Per-feature top-ups ruled out for everything else. The two Apple keys distinguished: the App Store Connect API key on the Expo account is not the In-App Purchase key the App Store Server API needs. | Decision R, §6.1, §6.3, §5.7.5 |
> | 8 | COGS quoted from the allowance cap, not from operation estimates. Voice unbundled and removed from Free. Monthly backstop 30 000 → 20 000 units. NGN allowances derived from NGN net revenue after the Nigerian monthly was found to have a negative unit margin. §6.8's free COGS reconciled with §6.7's (+$518 → +$58 before fixes). 7-day pass ₦1 800 → ₦1 500. NGN-only ₦5 500 Term Pass added. | Decision Q, §6.3, §6.4, §6.8, §6.11 |
> | 7 | Model-quality split confined to operations above 500 units; `gemini-3.5-flash-lite` as Free's second candidate. The Gemini 2.5 family shuts down October 2026, so the cheapest row in the price table was not a usable fallback. | Decision P |
> | 6 | The model-quality paywall gated nothing — `LLM_TIER_ALLOWLIST_FREE` listed the Plus model and the chat chain put it first. Free narrowed to Flash-Lite; the 26 un-gated `llm_resilient` call sites recorded as drift 23. | §5.2, drift 23 |
> | 5 | Phase 2: one resolver; `require_premium` deleted; `personal_tier` removed from the model router's signature. | Decision B |
> | 4 | Paystack port promoted to its own phase; iOS committed alongside Android; store-product creation consolidated into one section; all grandfathering machinery removed on the zero-subscriber fact. | §5.7, Phase 2b |
> | 3 | Phase 1: catalogue rewritten, credit packs deleted, billing router mounted. | Phase 1 |
> | 2 | Trial shortened to 3 days; referral cap removed; rewarded ads withdrawn; points introduced as a pass-only currency. | §6.9, Decision O |
>
> Last reviewed: 2026-09-02 (revision 10)

## 1. Purpose

Four products replace the current **personal** catalogue: three **consumable Plus passes** — 5-hour, 7-day, and an NGN-only 4-month Term Pass — and one **$9.99/month subscription**. Credit packs go, and the retired Study Circle and Squad personal tiers are finished off. Daily and monthly credit caps are replaced by a **rolling usage window** that resets on a clock the learner can see, with live voice metered separately.

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

**There was no entitlement layer — there were four, and they disagreed.** Closed by Phase 2 and recorded here in one line because it is the argument for Decision B and the reason `resolve()` has the shape it does. `feature_tier_service`, `require_premium`, the credit meter and the LLM tier resolver each held their own notion of "paid": a retired-tier subscriber was denied every capability while the meter granted them millions of credits, a trialling learner got Plus features with free-tier models, and `require_premium` was a working gate wired to zero endpoints. A pass would have been a fifth notion, and the one that changes minute to minute. **One decider now**, and the credit meter is the last mechanism still reading `CREDIT_LIMITS[User.tier]` — Phase 3 repoints it.

**The credit meter measures the wrong period.** Today: a monthly hard cap (`creditsUsed` vs `creditsHardCap`), plus a daily cap for FREE only, plus an 80%-of-month soft warning, plus a purchased-balance fallback, plus a referral daily-limit increase. Five interacting quantities across `check_credit_availability:319-455`. The failure mode a learner actually hits is "I ran out on the 9th and have three weeks of nothing", and the message they get is a wall of formatted numbers. Section 6.2 replaces all of it with one window and one reset time.

**"Apple Pay for in-app purchase" is not the right rail, and the distinction costs money.** For digital content consumed inside the app, Apple and Google require **In-App Purchase / Play Billing** — StoreKit and Play Billing Library, not the Apple Pay or Google Pay wallet APIs. Apple Pay is a payment *method* for physical goods and for web checkout; using it for Plus inside the iOS app is a guaranteed rejection under App Review Guideline 3.1.1. Where Apple Pay and Google Pay *are* correct is **web checkout**, as wallet payment methods on the Stripe payment sheet — a dashboard checkbox, not an integration. So:

| Surface | Rail | Store cut |
| --- | --- | --- |
| Web (`app.maigie.com`) | Stripe Checkout, Apple Pay + Google Pay + card wallets enabled; Paystack for NGN | ~2.9% + 30¢ |
| iOS app | StoreKit 2 in-app purchase | 15–30% |
| Android app | Google Play Billing | 15–30% |

On a $0.99 pass the store keeps 15¢ (Small Business Program) to 30¢. Net is ~$0.69–0.84 versus ~$0.66 on Stripe after fixed fees — at $0.99 the 30¢ Stripe fixed fee makes web *worse* than the store. At $9.99/month web is clearly better ($9.40 vs $8.49). That inversion is worth knowing before anyone builds steering logic; it also means the $0.99 pass is the one product where store distribution costs nothing extra. Full table in §6.4.

## 3. Outcomes

When complete:

- `GET /api/v1/billing/plans/catalog` returns the `scope: "personal"` products available to the caller's territory — Free, the 5-hour pass, the 7-day pass, Plus Monthly everywhere, plus the Term Pass in Nigeria — alongside the two existing `scope: "circle"` / `scope: "add_on"` entries, unchanged. It is the only place any client learns what exists or what it costs.
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
- **Migrating legacy tiers off grandfathered subscriptions.** Withdrawn: there is nobody to migrate. Phase 2b counted, against production, zero users on any retired tier and zero payment identifiers of any kind. `LEGACY_PLUS_TIERS` is therefore never written, drift 10's resolver half closes by deleting the tiers rather than admitting them, and the Phase 8 migration step is gone. The count is re-runnable (`scripts/count_legacy_commercial_state.py`), so if it ever comes back non-zero the correct response is to restore the frozenset, not to break a payer.

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

**Also deleted:** `STRIPE_PRICE_ID_STUDY_CIRCLE_MONTHLY` / `_YEARLY`, `STRIPE_PRICE_ID_SQUAD_MONTHLY` / `_YEARLY` (`config.py:198-203`) and `PAYSTACK_PLAN_STUDY_CIRCLE_*` / `_SQUAD_*` (`config.py:248-251`). All eight existed so a webhook could identify a grandfathered subscriber's source tier; with no subscribers on those tiers no such webhook can arrive, and eight empty-string settings that decode events which cannot be received are not history, they are clutter. `_price_id_to_tier` and `_assert_price_id_is_active` (`stripe_service.py:214-266`) lose their Study Circle and Squad branches with them. **`DEPRECATED_PLAN_IDS` keeps all six ids**, because a learner or a stale client presenting a retired plan id still deserves `410` rather than `422`.

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
| **Chat only** | LLM model allowlist | `feature_flags.is_model_allowed`, `config.LLM_TIER_ALLOWLIST_FREE` | **flash-lite** (narrowed in revision 6) | silent |

`check_capability` — the function the whole matrix is built around — is called from **exactly two places**: `quiz_engine.py` and `document_impl.py`. Everything else reads `get_quality_tier` and degrades silently.

**The model allowlist is chat-scoped, and that is the one row worth reading twice.** `route_request` is the only thing that consults it and has exactly one caller, the Ask/chat turn. The other 26 LLM call sites reach providers through `llm_resilient`, which resolves a provider from its own `["gemini", "openai", "anthropic"]` order and a model from `registry._DEFAULTS`, and asks about neither allowlist nor entitlement. So quiz generation, lesson bodies, documents, flashcards and every narrative panel ran the Plus model for a free learner. That was drift 23, and it meant the model-quality half of the paywall was true of one surface out of twenty-seven. **Closed in Phase 3b, and the allowlist is still chat-scoped** — `llm_resilient` asks `entitlement_service` and picks from `registry` rather than being routed through the chain, so the two mechanisms remain separate and `route_request` still has one caller. Free's chat allowlist was itself listing the Plus model until revision 6 — and since that model is first in `FALLBACK_CHAT_DEFAULT`, listing it did not make it a fallback, it made it the default. Now Flash-Lite only, asserted in `test_model_quality_paywall.py`.

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
8. ✅ **Closed in Phase 2.** `require_premium` / `PremiumUser` — a gate with no callers, deleted and replaced by Decision B.
9. **`check_capability("study_plan", "adaptive")`.** The branch exists at `feature_tier_service.py:322-333`; nothing calls it. → **wire it** so a Free learner asking for an adaptive plan gets a truthful `200 + notice` instead of a silently different plan.
10. ⚠️ **Half-closed.** `STUDY_CIRCLE_*` / `SQUAD_*` tiers resolved to `"free"` while the meter granted them millions of credits. The **resolver half is done**: Phase 2b deleted the writers that could produce those strings, so resolving them to `free` is now the correct answer rather than a defect. The **meter half is not**: `CREDIT_LIMITS` still holds rows granting `SQUAD_YEARLY` 12M credits and `STUDY_CIRCLE_YEARLY` 6M, keyed by strings nothing can write. Dead weight, not a live grant — **Phase 3 deletes the whole table**, so narrowing it now would be a second edit to something being removed.
11. ✅ **Closed in Phase 2.** Trials were invisible to the LLM router (`feature_flags.py:455-501`), so a trialling learner got Plus features with free-tier models. Fixed by Decision B.
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
22. **`WEB/pages/settings/AiModelSettings.tsx:13-25` lets a learner pick a model from a hardcoded list**, while the backend allowlists models by tier (`config.py:310-315`, `feature_flags.is_model_allowed`). Nothing reconciles the two, so the picker can offer a model the server will refuse. Small, but it is a tier gate with a UI that does not know it exists. **Now larger than when written:** since Free is Flash-Lite only, the picker offers a free learner a model the server refuses on every option but one.
23. **The model-quality paywall covers chat and nothing else.** `LLM_TIER_ALLOWLIST_*` is read only by `router.route_request`, whose single caller is the Ask/chat turn (`ask_service.py:2109`). The other 26 LLM call sites — quiz and question generation, lesson bodies, course outlines, documents, all four flashcard paths, every narrative panel, reflections, home guidance, memory extraction, discovery — go through `personal_learning.services.llm_resilient`, which resolves a provider from `["gemini", "openai", "anthropic"]` and a model from `registry._DEFAULTS`, and consults no allowlist and no entitlement. `_DEFAULTS` names `gemini-3.5-flash` for most of them.

    So a free learner's quiz generation runs the Plus model, and the catalogue's Plus copy is true of one surface out of twenty-seven. → **the fix belongs with Phase 3b, not before it.** Decision L already routes all 26 sites through one chokepoint in `llm_resilient` in order to meter them; a tier-aware model choice is the same plumbing and the same argument, and doing it twice would mean doing it wrong once. Both sub-decisions it once left open — Free's second candidate, and whether the split covers every operation — are settled in **Decision P**.

    **✅ Closed in Phase 3b, and it was larger than the description above.** `llm_resilient.model_for_operation` resolves the tier from `entitlement_service` and returns `GENERATION_PREMIUM` or `GENERATION_STANDARD` from `registry._DEFAULTS`, which `generate_content_with_usage` now accepts as an argument — it bound `CHAT_DEFAULT` unconditionally, which is the line the Plus model came down. Resolved once per logical operation, so a retry cannot change the rate mid-flight, and only for operations in `QUALITY_SPLIT_OPERATIONS`, so most calls never resolve an entitlement at all.

    Three things the audit had wrong, all found by doing it:

    - **Six of the 26 sites were not on the chokepoint at all.** `document_impl`, `note_service` (rewrite and summary), `discovery_service`, `auto_setup_service` and the topic quiz/summary route called `intelligence.reasoning.llm.generate_content` directly — no meter, no headroom gate, no retry, *and* no tier. Document generation is the one that matters: the largest paid-feature generation in the product, hard-gated on format and style, ungated on cost. All six now go through `llm_resilient`, so this closed part of Phase 3b's metering gap as a side effect rather than only the quality half.
    - **The most expensive operation in the product cannot go through the chokepoint.** `resource_service`'s step 1 is `generate_grounded_content`, and the search tool has no OpenAI or Anthropic equivalent, so there is nothing to fall back to and the wrapper's shape does not fit. It reaches the split through the exported `model_for_operation` instead, which is why that function is public. It is **still unmetered** — `GroundedResult` carries no token counts — so the recorded cost of a recommendation is currently its cheaper half. Straggler below.
    - **23 of 27 call sites passed `operation="unknown"`**, and the split is keyed on the operation. Labelling them was a prerequisite, not tidying.

    Two smaller findings recorded rather than swept up: `document_impl` caught `SubscriptionLimitError` in a broad `except` and reported it as a `502` "the document could not be written — please try again", which is untrue in a way that costs money, and is now re-raised above the handler as `generate_content_json` already does; and the local `.env` pinned `LLM_TIER_ALLOWLIST_FREE` to the pre-revision-6 value, so **the chat paywall was switched off in development** — the exact failure `test_model_quality_paywall._service()` was written to catch, caught.

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

**iOS is less greenfield than it looks, and two things that read like tasks are not.**

`ios/` **exists**: `expo prebuild` has been run and the directory holds `Maigie.xcodeproj`, `Maigie.xcworkspace`, a `Podfile.lock` and installed `Pods`. It is gitignored (`.gitignore:12`, alongside `android/` at `:11`), which is the normal Expo CBA arrangement and the reason a source-only reading misses it — a build artifact regenerated from `app.config.js`, not committed state. So `expo prebuild` is not a task, and there is nothing to commit.

**`react-native-iap`'s iOS pod is already linked.** `ios/Podfile.lock` carries `NitroIap`, so the StoreKit native module is present and building. The iOS purchase gap is JavaScript and console configuration, not native integration — `usePlayBilling.ts:88-93` early-returns on `Platform.OS !== 'android'` before `initConnection`, so the hook refuses the platform its own dependency supports.

**"StoreKit capability" is not an entitlements change.** `ios/Maigie/Maigie.entitlements` carries `aps-environment` and `applinks:app.maigie.com`, and correctly carries nothing for in-app purchase: In-App Purchase is enabled on the **App ID in the Apple Developer portal**, and modern StoreKit adds no entitlement key. Editing the entitlements file would be wrong.

What genuinely does not exist for iOS: the App Store Connect app record, the in-app purchase products, the App Store Server API key (`APPLE_ISSUER_ID` / `APPLE_KEY_ID` / `APPLE_PRIVATE_KEY`), the `POST /webhooks/apple` endpoint and its JWS verification, the StoreKit branch of the purchase hook, and the EAS iOS submit configuration. That is §5.7 and Phase 5, and none of it is deferred.

### 5.7 Creating the products, provider by provider

**Nothing in this plan can take money until the products exist in four places, and none of the four is a code change.** This section is the authoritative creation order. It exists because the consoles are the only part of the plan with external dependencies measured in days rather than hours, and because an earlier draft split the work across two phases and two owners, which is how a launch blocker becomes a surprise.

**The rule that sets the order:** an in-app purchase product cannot be reviewed on its own. Apple reviews IAP products **attached to a build**, and the first submission of an app with IAP is the most rejection-prone submission a project makes. Google is more forgiving — Play in-app products go live from the console without review — but a Play subscription needs an active app with a published build on some track before purchases can be tested. Stripe and Paystack have no review at all and can be done in an afternoon, which is exactly why they should not be done first: they are not the critical path and doing them first creates the impression of progress.

| Order | What | Owner | Lead time |
| --- | --- | --- | --- |
| 1 | Apple prerequisites — app record, Paid Apps agreement, banking and tax | **Finance + Apple account holder** | **Days to weeks.** Not an engineer's to supply |
| 2 | Google Play prerequisites — merchant account, tax and payout profile | **Finance** | Hours to days |
| 3 | Stripe products and prices | Backend | Under an hour |
| 4 | Paystack plan and price constants | Backend | Under an hour |
| 5 | Play products | Backend | Under an hour, live immediately |
| 6 | Apple products | Backend | Created immediately, **reviewed with the first build** |
| 7 | First iOS submission with all four IAPs attached | Mobile | **1–2 weeks, expect one rejection round** |

Steps 3–6 are all "Phase 5, first task". Step 1 should start **now**, in parallel with whatever else is in flight, because it gates everything Apple and costs nothing to hold open.

#### 5.7.1 The product matrix — the one table every console is filled in from

Prices from §6.1 (USD) and §6.8 (NGN). **NGN is a set price, not a conversion**, so every console needs the NGN figure entered by hand as a territory price rather than left to the store's FX table.

| Internal id | USD | NGN | Stripe | Paystack | Google Play | Apple |
| --- | --- | --- | --- | --- | --- | --- |
| `plus_pass_5h` | 0.99 | ₦700 | one-time price | one-off charge | `plus_pass_5h`, consumable | `com.maigie.plus.pass5h`, Consumable |
| `plus_pass_7d` | 3.99 | ₦1 500 | one-time price | one-off charge | `plus_pass_7d`, consumable | `com.maigie.plus.pass7d`, Consumable |
| `plus_monthly` | 9.99/mo | ₦2 400/mo | recurring price, 3-day trial | plan code, monthly | `maigie_plus` / base plan `plus-monthly` | `com.maigie.plus.monthly`, group `maigie_plus` |
| `plus_pass_term` | **—** | ₦5 500 | **not created** | one-off charge | `plus_pass_term`, consumable, **NG only** | `com.maigie.plus.passterm`, Consumable, **NG only** |
| `plus_voice_30` | 1.49 | ₦1 500 | one-time price | one-off charge | `plus_voice_30`, consumable | `com.maigie.plus.voice30`, Consumable |
| `free` | 0 | 0 | — | — | — | — |

**The Term Pass is deliberately absent from Stripe.** It is NGN-only (§6.1), the NGN web rail is Paystack, and creating a USD price for a product with no USD market would put an unbuyable product one API call away from being sold. Its two rails are Paystack on web and the stores on mobile, with Nigeria-only availability set in both consoles.

**`plus_voice_30` is the same price in both markets and that is not an oversight.** ₦1 500 is $1.08 against a $1.49 USD list — a smaller discount than any other product, because a voice minute costs the same in Lagos as in London. Voice is the one line where the market cannot be discounted into, only sized around, which is why the pack is 30 minutes rather than 60: a 60-minute pack needs roughly ₦2 800 to clear a 40% margin, and that crosses the ₦2 500 flat-fee threshold. Same reason ₦2 400 is not ₦2 500.

#### 5.7.2 Stripe

Three products, three prices, no review. Do this in the dashboard rather than the API so the ids are visible to whoever debugs a webhook later.

- [ ] Product **Maigie Plus** → recurring price **$9.99/month**, `trial_period_days = 3`. **Create a new price and archive any existing $4.99 one.** §6.1 explains why this is free to do now and expensive later: with zero subscribers there is no migration, and the first subscriber makes this permanent.
- [ ] Product **5-Hour Plus Pass** → one-time price **$0.99**. → `STRIPE_PRICE_ID_PLUS_PASS_5H`
- [ ] Product **7-Day Plus Pass** → one-time price **$3.99**. → `STRIPE_PRICE_ID_PLUS_PASS_7D`
- [ ] Product **30 Voice Minutes** → one-time price **$1.49**. → `STRIPE_PRICE_ID_PLUS_VOICE_30`
- [ ] Enable **Apple Pay, Google Pay and Link** as payment methods in the dashboard. This is a checkbox, not an integration, and it is the correct place for the wallet APIs — §2 explains why using them inside the iOS app would be a guideline 3.1.1 rejection.
- [ ] **Archive** the yearly Plus price and the three credit-pack prices. Archive rather than delete: Stripe keeps them referenceable, and archiving is what stops them being selectable.
- [ ] Set the webhook endpoint to `POST /webhooks/stripe` for `checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`, `charge.refunded`. **Copy the signing secret into `STRIPE_WEBHOOK_SECRET`** — an unset secret now refuses ingestion with a `503` rather than trusting the body (Phase 2a), so a missing secret fails loudly instead of granting tiers to anyone who posts.

Passes are charged with `mode: payment`, the subscription with `mode: subscription`. One endpoint each; a pass is not a subscription and the catalogue refuses the confusion explicitly.

#### 5.7.3 Paystack

Paystack has two shapes and the passes use the simpler one. **A subscription needs a Plan object with a plan code; a one-off charge does not** — it is `/transaction/initialize` with an amount, which is why the three passes need no console object at all and exist purely as price constants.

- [ ] Create the **Plus Monthly plan**: interval `monthly`, amount **₦2 400**. → `PAYSTACK_PLAN_MAIGIE_PLUS_MONTHLY`
- [ ] **No plan object for any pass.** All three are one-off charges. What they need is the amount, in **kobo**, in `config.py`:

  | Setting | Value (kobo) | Naira |
  | --- | --- | --- |
  | `PRICE_NGN_PLUS_PASS_5H` | `70_000` | ₦700 |
  | `PRICE_NGN_PLUS_PASS_7D` | **`150_000`** | ₦1 500 |
  | `PRICE_NGN_PLUS_MONTHLY` | `240_000` | ₦2 400 |
  | `PRICE_NGN_PLUS_PASS_TERM` | **`550_000`** | ₦5 500 |
  | `PRICE_NGN_PLUS_VOICE_30` | **`150_000`** | ₦1 500 |

  **Three of these five are wrong or missing in the code today.** Phase 2b added `PRICE_NGN_PLUS_PASS_7D = 180_000` for the retired ₦1 800; `PRICE_NGN_PLUS_PASS_TERM` and `PRICE_NGN_PLUS_VOICE_30` do not exist. All must be set before any one-off charge is initialised, because `_plan_amount_kobo` is the only thing standing between a learner and being charged the wrong amount — for a subscription the plan overrides the amount, but **for a one-off charge nothing does.**
- [ ] Verify the **flat-fee boundary** on each amount: [Paystack charges 1.5% + ₦100 on local cards, with the ₦100 waived below ₦2 500](https://paystack.com/pricing). The first three sit under it deliberately; **₦5 500 sits above it and pays the ₦100 knowingly** (§6.8). Do not round any of the first three up, and do not "fix" the Term Pass by pricing it under ₦2 500 — that would undercut two months of the subscription. *Content was rephrased for compliance with licensing restrictions.*
- [ ] Set the webhook to `POST /webhooks/paystack` and **copy the secret into `PAYSTACK_WEBHOOK_SECRET`**. The handler HMACs the body and now refuses an empty key rather than skipping the check.
- [ ] **Delete** the Study Circle and Squad plan codes (§5.1) — four settings and their `_plan_code_to_tier` branches.

#### 5.7.4 Google Play

Play products go live from the console without review, so this is the fastest of the four consoles and the one most worth getting exactly right, because a wrong product **type** cannot be corrected later.

- [ ] Confirm the merchant account, tax and payout profile are complete. Without them the in-app products section is read-only.
- [ ] Create `plus_pass_5h`, `plus_pass_7d`, `plus_pass_term` and `plus_voice_30` as **in-app products of type consumable**. **Consumable is load-bearing and irreversible**: a non-consumable is permanently owned, restorable forever, and unbuyable a second time, which is the exact opposite of a pass. Getting this wrong means a new SKU, not an edit. `plus_voice_30` in particular is bought repeatedly by design (Decision R), so a non-consumable would break it on the second purchase.
- [ ] Set the **NGN price for every product by hand** in the territory pricing table. Play's automatic conversion would apply FX parity, which §6.8 spends a section explaining is the thing that prices us out of the launch market.
- [ ] Restrict `plus_pass_term` to **Nigeria only** in its availability settings.
- [ ] On subscription `maigie_plus`, base plan `plus-monthly`: set **$9.99** (raised from $4.99 — no 7-day price-change notice is owed because there are no subscribers), set the NGN price to **₦2 400**, and set the **free-trial offer to 3 days**.
- [ ] **Delete, do not repurpose**: the `plus-yearly` base plan and the three `credit_pack_*` consumables, plus `GOOGLE_PLAY_BASE_PLAN_YEARLY` and the three `GOOGLE_PLAY_SKU_CREDIT_*` settings (`config.py:264-268`) and the branches reading them at `google_play_service.py:65, 191-193`. Nobody has bought any of them, so there is no RTDN history to decode. **Never rename an existing SKU into a new role** — a renamed SKU carries its old purchase history and its old type.
- [ ] Point Real-Time Developer Notifications at the Pub/Sub topic feeding `POST /webhooks/google-play/rtdn`, and set `GOOGLE_PUBSUB_AUDIENCE` and `GOOGLE_PUBSUB_SERVICE_ACCOUNT_EMAIL`. The endpoint verifies the push OIDC token against Google's certs and checks the token's `email` — a Google-signed token proves Google minted it, not that our subscription sent it (Phase 2a).
- [ ] Confirm the service account still has **View financial data** and Play Developer API access, which is what `purchases.products.get` and `purchases.subscriptions.get` authenticate as.

#### 5.7.5 Apple App Store Connect

The longest lead time in the plan, and most of it is not engineering.

- [ ] **Create the app record for `com.maigie`.** It does not exist. Everything below is blocked on it.
- [ ] **Complete the Paid Applications agreement**, with banking and tax. **No in-app purchase product can be created until this is active**, and it needs details an engineer cannot supply. This is the single item most likely to add a week to the schedule, which is why it is step 1 rather than a Phase 5 task.
- [ ] Enable **In-App Purchase on the App ID** in the Apple Developer portal. This is a portal capability, **not** an entitlements-file edit — §5.6 says why editing `Maigie.entitlements` would be wrong.
- [ ] Create `com.maigie.plus.pass5h`, `com.maigie.plus.pass7d`, `com.maigie.plus.passterm` and `com.maigie.plus.voice30` as **Consumable**. Same irreversibility as Play.
- [ ] Create `com.maigie.plus.monthly` as **auto-renewable** in subscription group `maigie_plus`, with a **3-day introductory free trial**.
- [ ] Set NGN prices per territory via each product's **price schedule**, and restrict `com.maigie.plus.passterm` to **Nigeria** in its availability. Apple's default is to derive every territory from the base price, which is FX parity again.
- [ ] **Generate the In-App Purchase key — not the App Store Connect API key.** There are two Apple keys with nearly the same name, both issuing an issuer ID, a key ID and a `.p8`, and **they are not interchangeable**:

  | Key | Generated where | Used for |
  | --- | --- | --- |
  | **App Store Connect API** key | Users and Access → Integrations → **App Store Connect API** tab | EAS Submit uploading builds, fastlane |
  | **In-App Purchase** key | Users and Access → Integrations → **Keys**, key type **In-App Purchase** | **App Store Server API** — the server-side transaction lookup this plan needs |

  The App Store Connect API key is already on the Expo account, which unblocks submission and **nothing in `apple_service.py`**. `APPLE_ISSUER_ID`, `APPLE_KEY_ID` and `APPLE_PRIVATE_KEY` must come from the **In-App Purchase** key, alongside `APPLE_BUNDLE_ID` and `APPLE_ENVIRONMENT`. Both `.p8` files download exactly once — put them in the secret store rather than the repo, and do not paste either into a ticket. Distinction per [Adapty's StoreKit 2 walkthrough](https://adapty.io/blog/storekit-2-api-tutorial/); *content was rephrased for compliance with licensing restrictions.*
- [ ] Set the **App Store Server Notifications V2** URL to `POST /webhooks/apple`.
- [ ] Each product needs a **screenshot and review notes** before it can be reviewed. Trivial work that reliably surprises people on submission day.

#### 5.7.6 Parity checks, by hand, recorded with a date

Nothing tests these, because nothing in the repo can read a console.

- [ ] **The trial is 3 days in four places**: `config.TRIAL_DAYS_MAIGIE_PLUS`, the Stripe price's `trial_period_days`, the Play base-plan free trial, and the Apple introductory offer. Two of the four are consoles. `TRIAL_DAYS_CIRCLE_PLAN` stays at **7** — it is space-scoped (Decision F).
- [ ] **Every NGN price matches §6.8** in Play, App Store Connect and `config.py`: ₦700 / ₦1 500 / ₦2 400 / ₦5 500, and ₦1 500 for the voice pack.
- [ ] **Every USD price matches §6.1** in Stripe and both stores: $0.99 / $1.49 / $3.99 / $9.99. **No console anywhere still reads $4.99 or $2.49.**
- [ ] **The Term Pass is unavailable outside Nigeria** in both stores, and `PlanItem.availability` keeps it out of `GET /plans/catalog` elsewhere (§6.1). A catalogue that advertises an unbuyable product is a defect this plan has already recorded once.
- [ ] **Every pass and the voice pack are consumable** in both stores. Check it before the first purchase, not after — it cannot be corrected by editing.
- [ ] **The two Apple keys are not confused.** `APPLE_KEY_ID` belongs to the **In-App Purchase** key, not the App Store Connect API key on the Expo account. The symptom of getting this wrong is authentication failures on transaction lookup while builds upload perfectly.

#### 5.7.7 Testing does not wait for review

Play's internal-testing track and an Apple Sandbox Apple Account or a local StoreKit configuration file both exercise unapproved products end to end. Stripe and Paystack both have test modes. So every server rail in Phase 5 and every client flow in Phase 7 can be built and verified before the first submission. **Review gates the launch, not the work.**

## 6. The new model

### 6.1 The catalogue

**Six personal products.** Four are **consumable, non-renewing products** — three passes and one voice pack. One is a subscription. One is Free. The catalogue also keeps its two space-scoped entries (`circle_plan_monthly`, `plus_seat_add_on_monthly`) unchanged — **eight entries in total**, and the `scope` field already on `PlanItem` is what separates them.

Two products carry constraints the others do not, and `PlanItem` needs a field for each:

- **`plus_pass_term` must be absent from the catalogue outside Nigeria**, which makes it the first product here whose *availability* is regional rather than just its price. `PlanItem.availability` is no longer optional.
- **`plus_voice_30` must be absent unless the learner already holds Plus.** It is the only product that is useless on its own — voice is a Plus capability, so 30 minutes sold to a free learner buys nothing they can use. `PlanItem.requiresEntitlement` gates it, and Decision R explains why this is a purchase refusal rather than a UI hint.

| Product id | Display | Type | USD | Grants |
| --- | --- | --- | --- | --- |
| `free` | Free | — | 0 | baseline capabilities, Free window allowance |
| `plus_pass_5h` | 5-Hour Plus Pass | **consumable product** | **0.99** | full Maigie Plus for 5 hours from activation, then nothing |
| `plus_pass_7d` | 7-Day Plus Pass | **consumable product** | **3.99** | full Maigie Plus for 7 days from activation, then nothing |
| `plus_monthly` | Maigie Plus | auto-renewing subscription | **9.99/mo** | full Maigie Plus while active, **3-day trial** on first purchase |
| `plus_pass_term` | 4-Month Term Pass | **consumable product** | **NGN only — ₦5 500** | full Maigie Plus for 4 months from activation, then nothing |
| `plus_voice_30` | 30 Voice Minutes | **consumable product** | **1.49** | 30 live-voice minutes added to the learner's voice balance. Not an entitlement |

These are **US/UK list prices**. The launch market is Nigeria, where FX parity would price the product above Netflix Standard; §6.8 sets the NGN ladder independently at **₦700 / ₦1 500 / ₦2 400 / ₦5 500** and is the table that matters for launch.

**The Term Pass exists only in Nigeria.** §6.8's argument is that Nigerians are practised buyers of discrete prepaid digital goods and that recurring card mandates fail often. A pass wallet honours that at the scale of one study week; nothing else in the catalogue honours it at the scale of a semester, which is the unit Nigerian students plan in. ₦5 500 for four months is one prepaid decision, aligned to an academic term, with **no renewal that can fail** — worth more than the ₦4 100 of nominal discount against four monthly charges, because a mandate that fails in month two collects nothing at all. It is a consumable like the other passes, so it adds no entitlement mechanics (Decision A, Decision E): the same inventory-then-activate path with a longer duration. It carries no USD price, and §5.7.1 says why creating one would be a mistake rather than an omission.

**$9.99, raised from $4.99, and the window to do it closes on the first subscriber.**

Revisions 1–9 held the monthly at $4.99 on four arguments, one of which was labelled decisive: *unchanged from today's price, so no subscriber ever sees a price-increase flow.* **That argument is void and has been since Phase 2b took the count.** There are zero Stripe subscription ids, zero Paystack codes and zero Play tokens — there is no subscriber to protect, no Stripe price migration to perform, no mandatory Google Play notice to serve, and no Apple consent prompt where non-responders are cancelled at renewal. The entire cost of a price change is currently **zero**, and it becomes permanently non-zero the moment one person subscribes.

The remaining three arguments were preferences for the digit 9, and $9.99 satisfies them equally.

**What $4.99 was actually signalling.** ChatGPT Plus is $20/month, Course Hero around $40, Photomath around $10. At $4.99 Maigie was the cheapest thing in its category by a factor of two, which does not read as value — it reads as a toy, against a paid case (§6.8) that is specifically *not* "cheap AI access". $9.99 is still half of the nearest general-purpose comparison and buys something a general chatbot structurally cannot do.

**The 7-day pass moves $2.49 → $3.99** to hold the ladder at roughly 40% of the monthly. The **5-hour pass stays at $0.99** — its job is the sub-$1 impulse and the first card on file, not revenue, and $0.99 is the price point that job requires. The **voice pack stays at $1.49**, because it is priced off marginal cost rather than willingness to pay (Decision R).

**NGN prices do not move.** §6.8's market comparison is unaffected by any of this: ₦2 400 is set against Spotify at ₦1 600 and Netflix Mobile at ₦2 500, and raising it to chase the USD ratio is the one move guaranteed to make the launch market worse. **The gap between the two ladders widens from 35% to 17% of USD list, and that is correct** — regional pricing is a market fact, not a discount schedule, and a wider gap simply reflects that the US market was being under-charged rather than that Nigeria is now being over-served.

**This must ship before the first subscriber exists.** It is the only item in this plan whose cost rises permanently with time rather than falling.

**The trial is 3 days, not 7.** A free 7-day trial sitting beside a $3.99 7-day pass is the same product at two prices, and the one that costs money looks like a trick to anyone who remembers the free one. Three days separates them cleanly: the trial is a look, the pass is a study week. It costs nothing to shorten because **no trial has ever converted to a paying subscriber** — there has never been a reachable checkout to convert into. Three days is also long enough to be honest at a 5-hour window: ~14 windows, several study sessions, every Plus capability.

`config.TRIAL_DAYS_MAIGIE_PLUS` is `3` as of Phase 1, but the number also lives in **three store configurations the server does not control** — the Stripe price's `trial_period_days`, the App Store Connect introductory offer, and the Play base-plan free trial. All four must agree, two of them are set by hand in a console, and nothing can test them; §5.7.6 is the parity check. `TRIAL_DAYS_CIRCLE_PLAN` stays at 7 — space-scoped (Decision F).

A pass is a product. It does not renew, it cannot be cancelled, there is no billing relationship to manage, and it has no grace period. It is bought, it is held, it is activated, it runs out. That is the entire lifecycle, and it is the reason passes are cheap: nothing about them has to be serviced.

**Passes grant every Plus capability, in the learner's personal workspace.** Every entry in `FEATURE_TIER_MATRIX["*"]["plus"]`, `get_quality_tier() == "plus"`, the Plus LLM allowlist, audio-only voice billing. For the duration, a pass holder is indistinguishable from a subscriber to every personal-scope *capability* gate in the codebase. That is what Decision B buys. A pass does not grant a Plus seat in a Space; Decision F says why and says what the copy must therefore avoid claiming.

**What a pass does not grant is a subscriber's usage allowance**, and §6.3 is where that is set. A 5-hour pass carries one window's worth of usage sized to what $0.99 can pay for — full capabilities, a bounded amount of the expensive ones. The marketing states the voice figure explicitly rather than implying five hours of tutor, because five hours of tutor costs $6.00 to serve and the pass nets $0.75.

Retired: `credit_pack_starter`, `credit_pack_value`, `credit_pack_power`, `plus_yearly` / `maigie_plus_yearly`, and the already-deprecated `study_circle_*` / `squad_*` personal tiers.

Not retired, not in scope: `circle_plan_monthly`, `plus_seat_add_on_monthly`.

Store product ids:

**§5.7.1 is the product matrix** — every id, every price, every provider, in one table, and it is what the four consoles are filled in from. Two properties of it are decisions rather than details:

**Consumable is the correct store type and it is irreversible.** A non-consumable is permanently owned, restorable forever, and unbuyable a second time, which is the opposite of a pass. Getting it wrong means creating a new SKU, not editing one.

**Every store product is created new; none is a rename.** A renamed SKU carries its old purchase history and its old type. The live Play catalogue holds a `plus-yearly` base plan and three `credit_pack_*` consumables, and all four are **deleted** rather than repurposed — along with `GOOGLE_PLAY_BASE_PLAN_YEARLY`, the three `GOOGLE_PLAY_SKU_CREDIT_*` settings (`config.py:264-268`) and the branches reading them at `google_play_service.py:65, 191-193`. With zero purchases behind them there is no history to preserve, and an RTDN for a product nobody owns cannot arrive.

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
| Free | **500 units** | 5 000 | ~16 | **0 — unbundled** | $0.05 |
| Plus — subscription or trial | **4 000 units** | **36 000** | ~23 | separate allowance | $0.40 |
| 5-Hour Pass | **2 000 units** (one window) | — | ~11 | separate allowance | $0.20 |
| 7-Day Pass | 4 000 units/window | **10 000 total** | ~57 total | separate allowance | $1.00 total |
| Term Pass (NGN) | 4 000 units/window | **20 000/month** | ~114/month | separate allowance | $2.00/month |

**Two allowances moved with the $9.99 price rise, and they had to.** Raising the monthly's price without raising its allowance inverted the per-unit ladder: at $8.95 net for 20 000 units the subscription would have cost **more** per unit than a $0.99 pass, making the value product the worst deal and handing a learner doing arithmetic a reason to buy passes forever. So the monthly backstop goes **20 000 → 36 000** and the 5-hour pass goes **3 000 → 2 000**. §6.4 has the restored ladder.

**A price rise that arrives with a larger allowance is defensible to a learner; a bare one is not.** 36 000 units is roughly 1 200 Flash-Lite chat turns a month, about 40 a day — genuinely generous rather than nominally so. The backstop is now ~9 windows of maximal draw per month rather than ~5, which is still far above what anyone reaches by studying and still an abuse bound rather than a product limit.

**The Term Pass keeps 20 000 units/month** because its price did not change. NGN allowances are derived from NGN net revenue (§6.8) and NGN prices are unmoved, so nothing in the launch market shifts.

**Live voice is not drawn from the usage window, and unbundling it is what makes NGN prices affordable.** At 200 units/minute (§6.2) voice is 40× a Flash-Lite chat turn, so it dominated every ceiling: a learner who spent an allowance on voice hit the COGS ceiling, and one who spent it on text did not come close. A single allowance covering both meant pricing for the voice case and serving mostly the text case — the worst of both, because the price had to be defensible against a cost almost nobody incurred.

So voice has **its own stated allowance**, drawn from its own counter, and beyond it voice is a separate top-up purchase:

| | Voice minutes included | Voice COGS at ceiling | Beyond it |
| --- | --- | --- | --- |
| Free | **0** | $0.00 | not available — Plus capability |
| Plus Monthly | **60 / month** | $1.20 | `plus_voice_30` |
| 5-Hour Pass | **10** | $0.20 | `plus_voice_30` |
| 7-Day Pass | **25** | $0.50 | `plus_voice_30` |
| Term Pass (NGN) | **60 / month** | $1.20 | `plus_voice_30` |

**`plus_voice_30` is a real product, and it has to be, because of Decision D.** A subscriber who exhausts 60 minutes cannot activate a pass to get more — Decision D refuses activation while Plus is already active — so without a voice pack the catalogue has **no answer at all** for the learner using the product most. The only outcomes available are serving the minutes free, which is COGS at zero revenue, or refusing with nothing to buy. $1.49 is better than either. Decision R covers why this is the *only* top-up in the plan.

**Free gets no voice at all, and that is the honest version of the paywall.** An earlier design gave Free 2.5 minutes *per window* — but a 5-hour window permits 4.8 windows a day, so that is 12 minutes daily, $0.24/day, **$7.20/month at zero revenue**, from a tier whose entire target COGS is $0.20. It was not a small grant of voice; it was an unbounded one wearing a per-window label. Zero is defensible in a way that 2.5 is not: voice becomes the one capability a free learner is told plainly they do not have, which is also the clearest thing a pass can be sold on.

**Two properties this buys.** The flat price no longer carries a volatile cost — text usage varies by maybe 3×, voice by 40×, and only one of them was ever priced. And the marketing claim gets *more* concrete: "5 hours of full Plus including 10 minutes of live voice tutoring" is checkable, where "about 15 minutes" was an allowance-division artefact that no counter enforced.

**Why there is a monthly backstop when §1 says there is no monthly limit.** A 5-hour tumbling window permits up to 4.8 windows/day, so monthly exposure is 144× the window allowance. No window number is simultaneously generous enough for one session and bounded enough for a month. Claude — the reference implementation — shipped 5-hour windows and then **added weekly limits in 2025** for exactly this reason.

The resolution is that the backstop is not a product limit, it is an abuse limit. It is set at ~7.5 Plus windows/month, which is far above what any learner reaches by studying: 2 windows/day for 20 days is 40 windows, but a *typical* window consumes well under its allowance, so the backstop binds only on sustained maximal draw. It is not shown in the UI, not in the marketing, and not in `GET /billing/usage` until a learner is within 20% of it. Experientially there is no monthly limit. Financially there is a bound.

**Why Free gets more chat turns than Plus.** 500 units buys ~16 Flash-Lite turns; 4 000 buys ~23 3.5-Flash turns. Free is not starved of conversation — it is starved of *voice*, which it no longer gets at all, and of *model quality*. That is the honest shape of the paywall, it matches what actually costs money, and it keeps the free tier useful enough to convert.

> **Two caveats on the numbers in that paragraph, both live.** The ~16 figure holds only because revision 6 narrowed `LLM_TIER_ALLOWLIST_FREE` to Flash-Lite — before that Free ran the Plus model, first in the chat fallback chain, and 500 units bought about **3** turns rather than 16. The figure has still never been observed, only derived, which is why §6.7's typical-consumption column stays a forecast until Phase 3 instruments units per operation.
>
> ~~And the **model-quality half of the paywall holds for chat only** until drift 23 is closed.~~ **Drift 23 is closed.** `llm_resilient.model_for_operation` picks the model from the entitlement resolver, so quizzes, lesson bodies, course outlines, documents, the narrative panels and the grounded resource search now run Flash-Lite for a free learner and `gemini-3.5-flash` for a Plus one. Operations below the 500-unit line are identical on both tiers by design (Decision P), so on those surfaces the difference between Free and Plus is the allowance and nothing else — which is the honest version of the claim and the one the catalogue copy now makes.
>
> One gap remains in principle and **is unreachable in practice, because Gemini is the only enabled provider**: a fallback to OpenAI or Anthropic consults no allowlist, so a free learner whose Gemini attempts all fail would get `OPENAI_DEFAULT_MODEL` — a model no allowlist was consulted about, possibly dearer than the Plus model the split was avoiding. It stays recorded rather than resolved because the fix is a cheaper fallback model if one is ever enabled, not a second tier map: two places deciding what a learner is entitled to is the mistake drift 23 is a record of.
>
> **What made it unreachable was not the configuration but a defect the configuration exposed: `LLM_ENABLED_PROVIDERS` did not reach `llm_resilient` at all.** It is read by `adapter_registry`, `feature_flags` and `router` — the chat path — while `llm_resilient` hardcoded `["gemini", "openai", "anthropic"]` for its fallback order and validated a learner's stored `preferred_llm_provider` against `SUPPORTED_PROVIDERS`. So **disabling OpenAI in production would have turned it off for chat and left it serving all 27 generation surfaces**, and serving them as the *primary* provider for any learner whose preference was `"openai"` — a disabled provider reached first rather than last. The only thing that actually stopped one was an unset API key, which works by accident: `_call_openai` raises `RuntimeError("... not configured")` and the attempt loop reads that as "skip". That is the state the product is in — no key is provisioned — so it worked, at the cost of three timed attempts against a missing credential on the latency of every failed generation. A policy enforced by a missing credential is also re-enabled by anyone who supplies one. `router.py` already stated the rule this violated — "turning a provider off must turn it off everywhere" — and generation was the half of *everywhere* that was missing.
>
> `enabled_providers()` now filters both the primary and the fallbacks, in the supported order rather than the configured one so the sequence is a property of the code and not of how a variable was typed. An empty or unrecognised list degrades to the default and logs at `error`: a configuration naming no callable provider takes every AI surface down at once and is far likelier to be a typo than a decision. And `LLM_ENABLED_PROVIDERS` now defaults to `"gemini"` rather than `"gemini,openai"`, so an environment that does not set it gets the intended state instead of enabling a fallback nobody asked for.

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
| 7-Day Pass | $3.99 | $3.57 | $3.39 | $2.79 | **$3.48** |
| Plus Monthly | $9.99 | $9.40 | $8.49 | $6.99 | **$8.95** |
| 30 Voice Minutes | $1.49 | $1.15 | $1.27 | $1.04 | **$1.21** |

Note the inversion: at $0.99 Stripe's 30¢ fixed fee makes **web the worst channel**, while at $9.99 web is clearly the best — a 91¢ advantage per subscriber per month, up from 31¢ at $4.99. **The price rise made web steering worth building** where previously it was worth a footnote.

Margin at the allowance ceiling, which is the worst case rather than the expected case. **Every COGS figure here is the allowance cap × $0.0001, per Decision Q — not an operation estimate**, which is why revision 8 can state these without the "recompute before quoting" caveat that hung over the previous version of this table:

| Product | Net | Allowance | Max COGS | Floor margin | Typical COGS | Typical margin |
| --- | --- | --- | --- | --- | --- | --- |
| 5-Hour Pass | $0.75 | 2 000 units | $0.20 | **73%** | $0.14 | **81%** |
| 7-Day Pass | $3.48 | 10 000 units | $1.00 | **71%** | $0.60 | **83%** |
| Plus Monthly | $8.95 | **36 000 units/mo** | $3.60 | **60%** | $1.40 | **84%** |
| 30 Voice Minutes | $1.21 | 30 min = 6 000 units | $0.60 | **50%** | $0.48 | **60%** |
| Free | $0.00 | 500 units/window | **$0.15** | — | $0.08 | — |

**The monthly's true floor is 46%, not 60%, once its 60 included voice minutes are counted** — $3.60 of units plus $1.20 of voice against $8.95 net. That is the one place the two meters have to be added together, and it is worth stating because §6.3's voice table and this table each look complete on their own.

**The voice pack is the one product whose ceiling is routinely reached**, which is why its floor and typical margins sit closer together than anything else here. A learner buys 30 minutes in order to use 30 minutes; nobody buys voice minutes speculatively. The $0.48 typical figure assumes 80% consumption, and if the real number is 100% the margin is 50% rather than 60% — the narrowest band in the catalogue, and deliberately so, because a pack sized to be under-consumed would be a pack that misleads.

**The monthly's floor margin was 25% and is now 50%, from cutting the backstop 30 000 → 20 000 units.** §6.3 justifies the backstop as an abuse limit rather than a product limit, set "far above what any learner reaches by studying" — and a limit set that far above real usage was buying nothing except a worst case that halved the margin on the flagship product. 20 000 units is still ~5 Plus windows/month of maximal draw, which no studying learner reaches; the difference is only visible to the learner it was written to bound. With voice unbundled the backstop is also now a text budget, which is the thing that varies least.

**Free's ceiling drops $0.50 → $0.15** for the same reason plus voice removal: 500 units/window × 4.8 windows is the text bound, and the $0.50 figure was carrying voice exposure that no longer exists.

The ladder is coherent again after the §6.3 allowance change: **$0.000375/unit on the 5-hour pass, $0.000348 on the 7-day, $0.000249 on monthly.** Impulse buys cost most per unit, the subscription is the value choice, and the ordering matches the per-day price ladder ($4.75 / $0.57 / $0.33).

**No arbitrage, checked three ways.** Three 7-day passes cost $11.97 against $9.99 for a month and buy 30 000 units against 36 000 — more money for fewer units, which is the correct direction. Matching the monthly's 36 000 units with 5-hour passes takes 18 of them at $17.82. And a pass cannot be stacked to simulate a subscription anyway: Decision D allows one active at a time, so 18 passes are 18 separate five-hour sessions rather than a month of availability.

**The margins improved substantially and that is a symptom, not a win.** Floor margins went 60% / 51% / 50% to **73% / 71% / 60%**, because the price rose faster than the allowance. A product whose floor margin jumps 20 points on a price change nobody was consulted about was under-priced, and this table is the clearest evidence in the document that $4.99 was the wrong number.

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
3. **Withdrawn: the `max_tokens` audit.** It was scoped on the belief that an 8 192-token output budget was 89% of an operation's cost. `max_output_tokens` is a **ceiling, not a charge** — billing is on tokens produced — so lowering it saves nothing, and lowering these three would have re-opened five separately-diagnosed truncation bugs. The knob that does cap reasoning spend is **`thinking_budget`**, which nothing in the codebase set; that is the audit worth doing and Decision P's threshold work is where it landed.

   Two real over-provisions survive the withdrawal: `narrative_cache`'s single hardcoded budget shared by four panels of very different sizes, and `resource_service.py:304` at 8 192 to transcribe text it was just handed. Phase 0 holds the three-class `thinking_budget` split.

**Nothing outside chat is metered, and it cannot be without plumbing.** `LlmCostRecord` is written from exactly one place — `cost_tracker.record` at `router.py:308` — and `LLMRouter`'s only caller in the codebase is Ask/chat (`ask_service.py:2066`). Every operation in the tables above goes through `llm_resilient` or the raw `generate_content` helpers, **which discard the provider response and return text only**, so token counts do not reach the caller. Metering them is not a matter of adding a `consume_credits` call; usage metadata has to be plumbed through first (Decision L).

**The rate card these estimates rest on was wrong and is now corrected.** `cost_calculator._EXACT_MODEL_PRICING` priced `gemini-3.5-flash` at $0.50/$3.00 against a published $1.50/$9.00 — 3× low, in two tables, with five tests agreeing with it. Fixed in Phase 0.

**It does not move any margin in this document, and that is Decision Q's point.** Margins are quoted from allowance caps, which are denominated in dollars and cannot be wrong about what they cost. What the rate card moves is how much *product* an allowance buys — the §6.3 question of whether the offer is generous enough to sell — and the "typical COGS" columns, which are behavioural forecasts and labelled as such. Every estimate above stands as a sizing input at the corrected rates.

### 6.6 The gating and metering matrix

Two independent questions per operation, and the book (`business/ch36-pricing-philosophy`) settles the first one:

> Payment should expand capability, not unlock basic usefulness.
> If it does not strengthen learning, it should not exist simply to justify a higher price.

So **cost control is the window, not the gate.** An operation is gated only when Plus genuinely does something more; otherwise it is available to everyone and bounded by the allowance. That is also what `feature_tier_service.py:6-9` already claims, and §5.4 is the list of places the claim wasn't true.

| Operation | Free | Plus | Metered | Gate shape |
| --- | --- | --- | --- | --- |
| Chat / Ask | ✓ | ✓, better model | ✓ today | window only — **the one surface where the model split is live** |
| Live voice | ✓ | ✓, audio-only billing | ✓ today | window only |
| Quiz generation | ✓ 3 modes | ✓ 5 modes | **add** | `403` on mode |
| Lesson generation | ✓ | ✓ | **add** | window only |
| Course creation | 2/month | unlimited | **add** | `403` on count |
| Course outline | ✓ | ✓ | **add** | window only |
| Prep topic extraction | ✓ | ✓ | **add** | window only |
| Study plan | even split | adaptive | **add** | `200 + notice` (drift 9) |
| Flashcards (×4) | 5/note, basic | 10/note, 4 types | **add** | silent depth |
| Documents | pdf + academic | +docx, pptx, 3 styles | **add** | `403` on format |
| Note AI action / summarise | ✓ | ✓, **same model** | **add** | window only — below Decision P's threshold (300 / 110 units) |
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

Assumptions, all of which are guesses and are the first thing to replace with measurement: payer rate **6% of MAU** (2.0% subscribe, 2.5% buy 7-day passes at 1.3/month, 2.0% buy 5-hour passes at 2.0/month, 12% of payers buy a voice pack); **50%** of non-paying MAU are AI-active in a given month; net revenue per §6.4; COGS from allowance caps per Decision Q.

**The payer rate is 6%, down from 8%, and that drop is deliberate.** It is the assumed elasticity cost of the $9.99 price rise — roughly a third of would-be subscribers and a quarter of 7-day buyers lost, with the $0.99 impulse pass unaffected because its price did not move. **The rise pays for itself anyway**, which is the whole argument: revenue goes up while the number of learners we have to serve goes down. The break-even is around a **4.5% payer rate**; below that the rise is wrong. Nothing in the repo can tell you which side of 4.5% reality sits on, so this is the assumption to instrument first after launch.

Per-learner monthly COGS, before and after the §6.5 fixes:

| | Free — as-is | Free — fixed | Plus — as-is | Plus — fixed |
| --- | --- | --- | --- | --- |
| Metered usage (window) | $0.25 | $0.05 | $1.80 | $1.10 |
| Background tasks | $0.64 | $0.02 | $0.64 | $0.28 |
| Home guidance (uncached) | $0.70 | $0.01 | $0.70 | $0.02 |
| Unmetered generation | **unbounded** | in window | **unbounded** | in window |
| **Total** | **$1.59+** | **$0.08** | **$3.14+** | **$1.40** |

The "fixed" column assumes four changes, none of which a learner can perceive: meter every operation into the window (Decision L), gate background AI on engagement (Decision M), cache home guidance the way `narrative_cache` already caches narratives, and set `thinking_budget` per operation class. **Voice is not in this table** — it is separately allowanced (§6.3), which is what makes the Plus figure bounded rather than a guess.

At 8% payer rate, 50% of free MAU AI-active:

| | 1 000 MAU | 10 000 MAU | 50 000 MAU |
| --- | --- | --- | --- |
| Subscribers | 20 | 200 | 1 000 |
| 7-day passes sold | 32 | 325 | 1 625 |
| 5-hour passes sold | 40 | 400 | 2 000 |
| Voice packs sold | 7 | 72 | 360 |
| **Net revenue** | **$329** | **$3 308** | **$16 541** |
| Paid COGS | $56 | $566 | $2 828 |
| Free COGS | $38 | $376 | $1 880 |
| **Contribution** | **$235** | **$2 367** | **$11 833** |
| Margin | 71% | **72%** | 72% |
| Revenue per MAU | $0.329 | **$0.331** | $0.331 |

**The $9.99 rise is worth +77% contribution at 10 000 MAU** — $1 340 → $2 367 — on **a third fewer subscribers and a quarter fewer 7-day buyers.** Margin goes 56% → 72%. That is what correcting an under-price looks like: you serve fewer people, earn more, and spend less doing it.

It also halves the growth requirement. At $0.237 contribution per MAU, a lean two-engineer team at ~$4k/month needs **17 000 MAU** rather than 30 000, and a $20k/month team needs **85 000** rather than 149 000. Nigeria at $0.046 needs 87 000 MAU for the same lean team, which is the clearest statement of why the two markets are not interchangeable: **global buys runway, Nigeria buys product-market fit.**

**Voice packs assume a 12% monthly attach rate against payers, and it is the weakest assumption in the table.** Voice is the feature with the least usage data behind it and the highest unit cost, so the range that matters is wide: at 30% attach the pack is a significant product, at 2% it is a rounding error that still needs four store SKUs. Phase 3's instrumentation answers it. The pack is worth building at any of those numbers for the reason in Decision R — it closes an unbounded exposure rather than chasing the revenue — but **do not plan headcount against the +$70.**

**Without the §6.5 fixes the same model returns roughly −$270 at 10 000 MAU.** The fixes are not optimisations; they are the difference between a business and a subsidy — and note that **no price appears in that sentence.** The margin here is a cost-surface result, not a pricing one.

**Free-tier inference is still the largest single line item** — $368 against $2 383 of revenue at 10 000 MAU, and that is *after* the fixes; before them it was $940. Remaining levers, most powerful first:

| Lever | Status |
| --- | --- |
| Set `thinking_budget` per operation class | **the real knob, not yet estimated.** Open Question 1 |
| Context-cache the system instruction + tool declarations at a 90% cached-token discount | **not yet estimated; likely the largest single input saving.** Open Question 2 |
| Trim chat context 8 000 → 4 000 input tokens | worth roughly +$400 at 10 000 MAU |
| Free AI-active rate 50% → 30% | worth roughly +$150; a retention question, not a cost one |
| Payer rate 8% → 12% | worth roughly +$700, and the only lever here that is a growth question |

**The two highest-value levers are not pricing decisions and belong to nobody today**, which is why they are Open Questions 1 and 2 rather than phase items someone picks up by accident.

Two things this model does not claim: it ignores infrastructure, storage, CDN, email and salaries, so **contribution is not profit**; and the 8% payer rate is unvalidated against zero payment history, with passes plausibly pushing it above what a subscription-only product would reach, since $0.99 is an impulse rather than a commitment.

**And it is the wrong model for launch.** The first market is Nigerian students, where the USD list price is unpayable. §6.8 is the one that matters.

### 6.8 Nigeria is the launch market, and FX parity would price us out of it

USD/NGN is around **₦1,380–1,390** ([Wise, week of 20–26 Aug 2026](https://wise.com/gb/currency-converter/usd-to-ngn-rate/history)). At parity, the $9.99 list is **₦13,800** — and even the old $4.99 list was ₦6,900. What parity would mean next to what Nigerians actually pay for subscriptions:

| Product | NGN/month | ≈ USD |
| --- | --- | --- |
| [Spotify Student](https://awajis.com/spotify-subscription-plans-nigeria/) | ₦800 | $0.58 |
| [Spotify Individual](https://www.spotify.com/ng/premium/) | ₦1 600 | $1.16 |
| [Netflix Mobile](https://www.netflix.com/ng/) | ₦2 500 | $1.81 |
| Netflix Standard | ₦6 500 | $4.69 |
| Maigie Plus at parity with the old $4.99 | ₦6 900 | $4.99 |
| **Maigie Plus at parity with the $9.99 list** | **₦13 800** | $9.99 |

*Content was rephrased for compliance with licensing restrictions.*

At parity we would charge a Nigerian student **more than twice Netflix Standard**, 8.6× Spotify Individual and 17× Spotify's student tier — for a study tool whose nearest substitute is free Gemini on the same phone. Netflix prices Nigeria [~76% below the US](https://www.aisubdeal.com/de/pricing/netflix/nigeria/) precisely because parity does not work here. Every platform that has succeeded in Nigeria puts its mass-market tier at ₦800–2 500.

So **NGN is a set price, not a conversion.** Apple, Google and Paystack all support per-territory pricing; $9.99 stays the US/UK list.

**The $9.99 rise made this section's argument stronger, not weaker.** The NGN ladder is now **17–24% of USD list** rather than 35–52%, and a reader could mistake that for Nigeria being subsidised more heavily. It is the opposite: the ratio moved because the US price was corrected upward, not because Nigeria was discounted downward. **Not one naira changed.** A regional price is set against local substitutes, and Spotify and Netflix did not move.

| Product | NGN | ≈ USD | % of USD list |
| --- | --- | --- | --- |
| 5-Hour Plus Pass | **₦700** | $0.51 | 51% |
| 7-Day Plus Pass | **₦1 500** | $1.08 | 27% |
| Plus Monthly | **₦2 400** | $1.73 | 17% |
| **4-Month Term Pass** | **₦5 500** | $3.97 | — (NGN only) |

The ladder stays coherent: ₦3 360/day, ₦214/day, ₦80/day, ₦46/day, correctly ordered, and no arbitrage — two 7-day passes (₦3 000) cost more than a month (₦2 400) and buy fewer days; two months (₦4 800) cost less than the term pass but buy four fewer months.

> **Why the 7-day pass is ₦1 500 rather than ₦1 800: a cost-side arbitrage that a price-side check does not catch.** At ₦1 800 the pass was 75% of the monthly price for 23% of the duration, which reads fine as a price ladder — and is why it survived four revisions. But allowances are what cost money, and once they are derived from local net revenue (below), ₦1 800 for 7 days against ₦2 400 for 30 days forces the monthly to be roughly 4× more generous per day at 1.33× the price. The ladder was too compressed at the top to support a coherent allowance ladder underneath it. ₦1 500 opens the gap and still sits under the ₦2 500 Paystack threshold, so the flat fee is still waived.

**₦2 400 rather than ₦2 500 is deliberate.** [Paystack charges 1.5% + ₦100 on local cards, capped at ₦2 000, with the ₦100 waived below ₦2 500](https://paystack.com/pricing). Pricing at ₦2 500 triggers the flat fee: ₦137 instead of ₦36, turning a 1.5% cost into 5.5% for one naira of extra revenue. **The three recurring-scale prices all sit under that threshold**, and the same logic is why ₦700 rather than ₦900 for the 5-hour pass — the flat fee would have been 14% of the sale. Only the Term Pass crosses it, knowingly, for the reason below. *Content was rephrased for compliance with licensing restrictions.*

Net per sale, blending 60% Paystack web and 40% Google Play at 15%:

| Product | Paystack net | Play 15% net | Blended |
| --- | --- | --- | --- |
| 5-Hour Pass | $0.50 | $0.44 | **$0.48** |
| 7-Day Pass (₦1 500) | $1.07 | $0.92 | **$1.01** |
| Plus Monthly | $1.71 | $1.47 | **$1.61** |
| Term Pass (₦5 500) | $3.83 | $3.38 | **$3.65** |
| 30 Voice Minutes (₦1 500) | $1.07 | $0.92 | **$1.01** |

The Term Pass is the one NGN product **above** the ₦2 500 threshold, so it pays the ₦100 flat fee — ₦182 total against ₦82 at 1.5% alone. That is 3.3% of the sale rather than 1.5%, and it is accepted rather than designed around, because the alternative is pricing a four-month product under ₦2 500, which would undercut two months of the monthly.

**NGN allowances are derived from NGN net revenue, not inherited from the USD product.** This is where the launch market's subscription was losing money. Applying Decision Q's second rule — pick a target floor margin, then let it set the cap — at a 60% floor:

| Product | Blended net | Allowance | Max COGS | Floor margin | Typical COGS | Profit (typical) | Typical margin |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 5-Hour Pass | $0.48 | **1 800 units** | $0.18 | 63% | $0.11 | **$0.37** | 77% |
| 7-Day Pass | $1.01 | **4 500 units** | $0.45 | 55% | $0.28 | **$0.73** | 72% |
| Plus Monthly | $1.61 | **6 000 units/mo** | $0.60 | 63% | $0.36 | **$1.25** | 78% |
| Term Pass | $3.65 | **20 000 units** | $2.00 | 45% | $1.20 | **$2.45** | 67% |
| 30 Voice Minutes | $1.01 | 30 min | $0.60 | **41%** | $0.48 | **$0.53** | 52% |
| Free | $0.00 | 400 units/window | $0.12 | — | $0.08 | −$0.08 | — |

**The voice pack is the thinnest product in the NGN table and it cannot be made thicker.** 41% at the ceiling against 50% globally, because a voice minute costs $0.02 in Lagos exactly as it does in London while the price is a third lower. Every other product absorbed the NGN discount by cutting its allowance; voice cannot, because the allowance *is* the minutes and cutting them below 30 makes the pack not worth buying. This is the one place in the plan where regional pricing has no lever left, and 41% is the honest floor rather than a target.

**Why this table exists: the Nigerian Plus Monthly used to be a loss-making product, and nothing here said so.** ₦2 400 nets $1.61 against a typical monthly COGS of $1.80, so every sale lost $0.19 and a ceiling draw lost $1.39. The error was structural rather than arithmetical — this section set the NGN *price* from the Nigerian market, correctly and at length, and left the NGN *allowance* at whatever the $4.99 product carried. **A 65% price cut with no allowance change is a margin inversion**, and it stayed invisible because the price and the allowance lived in different sections. Deriving the allowance from local net revenue is the general fix, which is why this is a table rather than a note reading "NGN margins are thinner".

**6 000 units/month is a real product, not a token one.** Under Decision P chat runs Flash-Lite (~30 units/turn) for everyone, so 6 000 units is roughly **200 chat turns a month, 6–7 a day**, plus the 60 unbundled voice minutes. The figure only looks small against the USD product's 20 000, and a learner paying a third of the price receiving a third of the allowance is the honest shape of regional pricing — considerably more honest than the previous arrangement, where they received the same allowance and the company absorbed the difference as a loss.

**Free is 400 units/window in Nigeria rather than 500.** Free COGS is the largest line item in the launch market by a wide margin and the local revenue per MAU is half the USD figure, so the free tier has to be tuned to the market it is subsidising. 400 units is ~13 Flash-Lite turns per 5-hour window.

**Nigeria-first revenue at 10 000 MAU.** Payer mix weighted to prepaid products, which is what §6.8's own behavioural argument implies: **1.0% Plus Monthly, 1.0% Term Pass, 4.0% buy 7-day passes (1.4/month), 2.0% buy 5-hour passes (2.5/month)** — 8% in total, matching §6.7's rate — plus **96 voice packs/month at a 12% attach against payers**. Term Pass revenue and COGS are recognised monthly across its four months rather than at purchase.

| | Without the §6.5 fixes | **With them** |
| --- | --- | --- |
| Net revenue | $1 155 | **$1 155** |
| Paid COGS | $880 | **$324** |
| Free COGS | $1 463 | **$368** |
| **Contribution** | **−$1 188** | **+$463** |
| Margin | negative | **40%** |
| Revenue per MAU | $0.116 | **$0.116** |

**At Nigerian prices the cost work is not an optimisation, it is the precondition for the market existing.** Without it, unit economics are negative before a single dollar of infrastructure is paid for.

> **One correction worth keeping visible, because it is the class of error this section kept making.** An earlier version of this table claimed **+$518 at 42%** on a free COGS of $460 — while §6.7 put the same population, at the same MAU and the same payer rate, at $940. A free Nigerian learner runs the same models as a free American one, so both could not be right, and $460 implied either a 25% AI-active rate or half the per-learner cost, neither of which was ever stated. **At the reconciled figure that version of the model returns +$58 at 5%, not +$518 at 42%** — the difference between a viable market and a break-even one, resting on an assumption nobody had written down. Same shape as the monthly's negative margin: a number set correctly in one section and quietly re-derived in another. The table above is derived once, from allowance caps, for both markets.

Revenue per MAU is **$0.116**, under half the USD-priced $0.238 — so Nigeria needs roughly twice the users for the same revenue. That is a planning input rather than a problem: it is the normal shape of an emerging-market launch, and it is why free-tier cost per learner is the number that decides whether this works.

**The voice pack is worth more here than globally, proportionally.** It adds $51 of contribution against $412, or 12%, where globally it adds $70 against $1 270, or 5.5% — because NGN revenue per payer is lower, so any product priced near parity carries more weight in the mix. It is the one product in the catalogue that does *not* discount into the market, and in a market this thin that is a feature of the revenue line even though it is a constraint on the margin line.

**Two consequences for product, not just price.**

The **passes are the lead products in Nigeria, not the subscription.** Recurring card mandates fail often, and Nigerians are practised buyers of discrete prepaid digital goods — data bundles, airtime. A ₦1 500 pass bought the week before an exam fits that behaviour; a ₦2 400/month standing charge fights it. Phase 7's client work should lead with the pass wallet and treat the subscription screen as secondary.

**The Term Pass follows that argument one step further.** If the objection to the subscription is the mandate rather than the amount, the answer is not only a smaller prepaid product but also a *longer* one — a learner who wants four months of Plus otherwise has no way to buy four months of Plus except by accepting four chances for their card to fail. ₦5 500 once, aligned to an academic term, is the same commitment with none of the fragility, and it is the only product a Nigerian student can buy with cash-in-hand certainty for longer than a week. The margin arithmetic agrees independently: at $3.65 net it is the largest single transaction available in the market, and its 45% floor margin is the weakest in the NGN table only because its allowance is the most generous.

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

### 6.10 Model roster

| Role | Model | Rate per 1M (in/out) | Where it is set |
| --- | --- | --- | --- |
| Free chat, and every operation under 500 units | `gemini-3.1-flash-lite` | $0.25 / $1.50 | `LLM_TIER_ALLOWLIST_FREE`, `registry.GENERATION_STANDARD` |
| Free fallback (chat only) | `gemini-3.5-flash-lite` second candidate | **$0.30 / $2.50** | Decision P |
| Plus, operations **above 500 units** | `gemini-3.5-flash` | **$1.50 / $9.00** | `LLM_TIER_ALLOWLIST_PLUS`, `registry.GENERATION_PREMIUM` |

**This table named the wrong model for the first row until Phase 3b implemented it, at the other model's price.** It said `gemini-3.5-flash-lite` at $0.25/$1.50, which is `gemini-3.1-flash-lite`'s rate — the two rows were describing one model between them. Decision P is the one that has it right: `3.1-flash-lite` is Free's *primary* and `3.5-flash-lite` is its *fallback*, precisely because the fallback is **dearer** ($0.30/$2.50). Building the row as written would have raised the cost of every below-threshold operation by ~60% on output for nothing. A test now pins the standard generation model as the cheaper of Free's two candidates rather than as a named string, so the error cannot come back through this table.

The fallback is marked chat-only because that is what it is: the second candidate exists in the chat *chain*, which `router` walks. `llm_resilient` has no model chain — it falls back across *providers* — so a generation whose Gemini attempts fail leaves Gemini entirely rather than trying the second Flash-Lite.
| Live voice, both tiers | `gemini-3.1-flash-live-preview` | $3.00 / $12.00 per 1M audio tokens ≈ **$0.02/min** | `config.py` |

**`gemini-2.5-flash-lite` is not in this table and must not be added.** It is the cheapest row in `cost_calculator`'s pricing table at $0.10/$0.40, which is exactly why it keeps being suggested — but the whole Gemini 2.5 family shuts down in **October 2026**. A price table records cost, not availability, and a fallback with weeks to live is not a fallback.

The `gemini-3.5-flash` rate above is the corrected one. It was $0.50/$3.00 in two separate tables, 3× low, with five tests asserting the wrong figure (Phase 0). Decision Q is why that error no longer reaches any margin in this document.

### 6.11 The two markets side by side

Both at 10 000 MAU, with and without the §6.5 cost work. Nigeria's mix is prepaid-weighted per §6.8; global keeps §6.7's mix.

| | **Nigeria** | **Global** |
| --- | --- | --- |
| Net revenue | **$1 155** | **$3 308** |
| — of which voice packs | $97 | $87 |
| Paid COGS | $324 | $566 |
| Free COGS | $368 | $376 |
| Total COGS | **$692** | **$942** |
| **Contribution** | **+$463** | **+$2 367** |
| Margin | **40%** | **72%** |
| Revenue per MAU | $0.116 | **$0.331** |
| Contribution without the §6.5 fixes | **−$1 188** | **−$270** |
| At 1.5× usage overshoot | **+$117** | **+$1 896** |

**Nigeria now earns about a third of global revenue at 40% margin against 72%, and the gap is mostly the price ladder rather than the product.** Before the $9.99 rise the two markets were much closer — $1 155 against $2 383, 40% against 56%. Correcting the US under-price widened the gap in both revenue and margin without changing anything a Nigerian learner sees.

**That is the strategic shape worth naming: the two markets do different jobs.** Global buys runway — $0.237 contribution per MAU, a lean team at 17 000 MAU, and margin that survives a usage overshoot. Nigeria buys product-market fit — the standardised-syllabus market where the product's hardest claim (your syllabus, your weak areas, your exam date) is most testable and most differentiated from a general chatbot. **Neither market alone is the plan.** Funding a team from Nigeria alone needs ~87 000 MAU; global alone needs 17 000 but offers no structural cost advantage and much more competition.

**The 1.5× overshoot row is the one that matters for confidence.** Under Decision Q there is no rate-card error left to absorb, because the caps are denominated in dollars. The residual risk is behavioural: learners drawing closer to their ceilings than the "typical" column assumes. At 1.5× typical draw both markets stay positive, and the shape of the risk is now a usage question that Phase 3's instrumentation answers directly rather than a rate question nobody could close.

**What this does not fix, stated plainly because the margin numbers invite the wrong conclusion.** $463/month at 10 000 MAU funds nobody. Contribution excludes infrastructure, storage, CDN, email and salaries, so it is not profit. At a 40% contribution margin and $0.116 revenue per MAU, Nigeria needs on the order of **90 000 MAU to fund two engineers** — and that, not pricing, is the binding constraint on this business. The margin question is answered. The distribution question is open, and it is the harder one.

**No catalogue change will move that.** The voice pack added 12% to Nigerian contribution and it is the last obvious product gap; the next one would be a fifth SKU earning single-digit percentages against four more consoles to keep in sync. **Stop adding products after this one** and spend the effort on the MAU number instead — Decision R is the rule that makes that a decision rather than a drift.

**Every payer rate above is a guess against zero payment history.** 1 205 free users and no payment relationship anywhere in the database (Phase 2b). The mix is a hypothesis for Phase 3 to instrument, not a forecast, and the first real number replaces all of them.

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

### Decision P: The model-quality split covers expensive operations only, and Free carries two cheap candidates.

Two questions that drift 23 left open. Both are settled here because the answer to each constrains what the catalogue may claim, and copy that outruns the code is what §5.4 exists to catch.

**Free gets two models, not one.** `gemini-3.1-flash-lite` primary, `gemini-3.5-flash-lite` fallback. Narrowing Free to a single model — which is what closing the allowlist hole did on its own — also narrowed its chat fallback to a single model, for the tier holding 1 205 of 1 206 accounts. The roster and the prices are in §6.10; the short version is that the fallback costs a third more than the primary rather than six times more, which is what it cost when the fallback was the Plus model.

**The split applies to expensive operations only.** An operation picks its model by tier when it is above the threshold; below it, both tiers run the cheap model.

The threshold is **500 units** — $0.05 of measured COGS — which from §6.5 selects: quiz and question generation (780), lesson bodies (780), the three narrative panels (770 each), resource recommendations (1 600), document generation (570), and chat itself. Everything below stays on Flash-Lite for everybody: note summarise (110), home guidance (140), discovery (150), memory extraction (100), conversation summarisation (100), the four flashcard paths (160–200), study plan and schedule generation (225).

Four reasons, in descending order of how much they matter.

**Spend is concentrated, so a universal split buys little.** The six operations above the line are most of the per-learner bill. Downgrading a 100-unit operation saves $0.008 and costs a perceptible quality drop on something a learner sees constantly — the worst available trade.

**Two of the cheap operations must not be degraded at all, on principle rather than on cost.** §6.6 already exempts **onboarding auto-setup** and **memory extraction** from *charging*: onboarding is where the book's "free should create real success" is honoured or not, and memory extraction is what makes Maigie feel like it knows you (Principle Two). Exempting them from the meter and then quietly serving them a worse model would honour the letter and lose the point. A threshold covers this without a second exception list, which is the reason to prefer a threshold over an operation-by-operation table.

**It makes the Plus claim checkable.** "Advanced models throughout" was unenforceable — and untrue in the other direction, since Free had the advanced model everywhere. A claim naming the surfaces where quality differs can be tested; §5.4's rule is that a price is a promise about behaviour, and this is the version of the promise the code can keep. The catalogue copy is narrowed accordingly.

**It shrinks the blast radius of getting the split wrong.** Six operations reviewed properly beats twenty-seven reviewed by a rule nobody checked, and the twenty-one below the line keep behaviour identical for both tiers — so a mistake there is impossible rather than merely unlikely.

Two consequences worth stating rather than discovering. Below the threshold there is **no quality difference at all**, so the honest Free-versus-Plus story on those surfaces is the window allowance and nothing else. And the threshold is denominated in the §6.2 unit, which means it moves when the rate card moves: it is a line on a cost table, not a property of an operation, and Phase 3's instrumentation is what makes it a measurement instead of an estimate.

Implementation is Phase 3b, in the same chokepoint as the meter. `registry._DEFAULTS` already names Flash-Lite for `FACT_EXTRACTION_LITE`, `MINIMAL_RESPONSE`, `MEMORY_JSON` and `EMAIL_FALLBACK`, so several below-threshold operations are correct already and the work is smaller than the count of call sites suggests.

### Decision Q: COGS is quoted from the allowance cap, never from an operation estimate. A price is a market fact; an allowance is derived from it.

Two rules, and the first one retires a recurring failure in this document rather than adding a new constraint.

**Rule one: the ceiling COGS of any product is its allowance cap × $0.0001.** §6.2 defines a usage unit as $0.0001 of *measured* cost, which means an allowance cap is already a dollar cap. A 3 000-unit pass cannot cost more than $0.30 to serve — not because the rate card says so, but because the meter stops. Yet every margin table in revisions 1–7 was built by estimating per-operation costs and summing them, which is how one wrong entry in `cost_calculator._EXACT_MODEL_PRICING` propagated into §6.4, §6.7 and §6.8 simultaneously and forced revision 4 to mark all three stale with an instruction to recompute that nobody could act on until Phase 3 lands.

The instruction was the wrong response. **A cap does not need recomputing when a rate changes; it needs no computing at all.** The rate card determines how much *product* a cap buys — how many turns, how many minutes — and that is a §6.3 question about whether the offer is generous enough to sell. It does not determine what the cap costs us, which is the only input a margin needs. Revisions 1–7 conflated the two, and the tell is that §6.5 ends with "check this first — it is a five-minute task that moves every number in this document." Under this decision it moves no margin in this document. It moves the *value* of every allowance, which is a real question and a smaller one.

So: margin tables quote caps. Operation estimates stay in §6.5 and §6.2 where they belong, as inputs to sizing an allowance and to the "typical COGS" column, which is a forecast of learner behaviour and is labelled as one.

**Rule two: where a price is set by the market, the allowance is derived from the price. Never the reverse, and never inherited across markets.** §6.8 sets NGN prices from what Nigerians pay for subscriptions, at length and correctly, and this is not negotiable downward by a cost model — the market does not care what we spend. What *is* ours to set is what the price buys. Pick a target floor margin, and the cap follows: `cap = net_revenue × (1 − floor_margin) ÷ $0.0001`.

The absence of this rule is what produced a loss-making product in the launch market. NGN prices were cut 65% from USD list and NGN allowances were left at the USD product's values, so Plus Monthly netted $1.61 against a $1.80 typical COGS and a $3.00 ceiling. **Nobody multiplied it out, because the price lived in §6.8 and the allowance lived in §6.3**, and each was defensible in isolation. A regional price without a derived allowance is not a discount, it is a subsidy with no stated size.

Corollary, and it is the part that will feel wrong: **a learner paying a third of the price gets roughly a third of the allowance.** That is the honest shape of regional pricing and it is more honest than the alternative the plan had, which gave them the full allowance and absorbed the gap as an unrecorded loss. It also survives being said out loud to a learner, which the previous arrangement only survived by never being stated.

**What this decision requires in code: nothing new.** Decision E already terminates a pass on `units_used >= units_allowance`, and §6.3's window already bounds a subscription. The caps are the enforcement. This decision only changes which number a margin table is allowed to cite — which is why it is a decision in this document rather than a phase item.

### Decision R: One top-up, for voice, and no others. The pass ladder is already the top-up for everything else.

**Voice gets a pack. Nothing else does.** The reasoning matters more than the conclusion, because "should feature X have a top-up" is a question that will be asked again about every expensive operation in §6.5, and the answer is no for all of them for one structural reason.

**The pass ladder is already a units top-up.** A `plus_pass_5h` at $0.99 grants 3 000 units. A learner who wants more course generation, more quizzes, more documents, more of anything metered buys another pass — a product that exists, is priced, is in four store consoles and needs no new counter. A separate "units top-up" would be the 5-hour pass with a different name and a worse price, and it would compete with the product it duplicates.

| Expensive operation | Units | Top-up? | Why |
| --- | --- | --- | --- |
| **Live voice** | 200/min | **Yes** | Own counter; the pass ladder cannot refill it |
| Resource recommendations | 1 600 | No | Draws from the window; a pass refills it |
| Course generation | 1 020 | No | Same |
| Quiz / question generation | 780 | No | Same |
| Lesson body | 780 | No | Same |
| Document generation | 570 | No | Same |

**Voice is the exception because it is not denominated in the same thing.** §6.3 unbundles it into `voiceSecondsRemaining`, so a pass grants voice minutes but cannot be bought *for* voice minutes — and Decision D closes the loophole that would have made this work by accident: **a learner with active Plus cannot activate a pass at all.** So a subscriber who exhausts 60 minutes has, without this product, no purchase available anywhere in the catalogue. The only outcomes are serving the minutes free (COGS at zero revenue) or refusing them with nothing to buy. Both are worse than $1.49, and the learner in that position is by definition the most engaged one we have.

**Three reasons the answer stays no for everything else.**

1. **One currency was the point of §6.2, and there are already two.** The window's first stated property is that it is explainable in one sentence with a number in it. Two counters is a defensible cost of the 40× voice asymmetry. Three is a product nobody can predict their own spend on, and a learner who cannot predict their spend stops using the expensive features — which is the opposite of what a top-up is for.
2. **Every SKU is real work in four consoles.** §5.7 is now explicit about what adding a product costs: a Stripe price, a Paystack constant, a Play product with per-territory pricing, an Apple product with a price schedule and availability, a consumable type that cannot be corrected later, plus a counter, a sweep, a purchase rail and client UI. That is the price of admission for any top-up, and it should be paid once.
3. **Metering a study tool per feature charges people for studying harder.** `business/ch36-pricing-philosophy` says payment should expand capability rather than unlock basic usefulness. A voice pack expands a capability that has a genuine marginal cost per minute. A "10 more quizzes" pack charges for the core loop, and the honest version of that product is a subscription — which we already sell.

**The rule, so this is decided rather than re-litigated:** a capability earns its own top-up **only if it has its own counter**, and it earns its own counter only if its unit cost is an order of magnitude away from everything else. Voice qualifies at 40×. Nothing else in §6.5 is within an order of magnitude of the chat turn it is measured against, so nothing else qualifies. If a future capability does — video, or a realtime model priced like voice — it draws from `voiceSecondsRemaining`'s *pattern* rather than inventing a third meter.

**One consequence for the purchase rail: buying voice minutes requires holding Plus.** `plus_voice_30` is refused with `403 VOICE_PACK_REQUIRES_PLUS` for a learner with no active entitlement, and hidden from `GET /plans/catalog` for the same learner via `PlanItem.requiresEntitlement`. This is a purchase refusal rather than a UI convention because the store rails do not consult our UI: a learner can reach a Play or StoreKit purchase sheet from a cached catalogue, and selling 30 minutes of a capability the buyer does not have is the clearest possible version of the thing §1's rule forbids. The refusal is also the correct upsell surface — the learner wanting voice without Plus should be offered a pass, which is what Decision N's machinery is for.

**The pack stacks, unlike a pass.** `plus_voice_30` is a balance, not an entitlement, so Decision D's redundancy refusal does not apply and a learner may hold and consume any number. Minutes expire with the entitlement that was active when they were bought, tracked by `voiceAllowanceSourceId` (§8) — buying 30 minutes on the last day of a 7-day pass does not carry them into next month, for the same reason unused pass time is forfeited.

## 8. Data model

Migration `063_add_plus_passes.py` — `062_chat_generation_attempt.py` is the current head. `060` and `061` are taken (`060_notification_phase1`, `061_notification_phase2`); do not reuse them.

**`PlusPass`**

| Column | Type | Notes |
| --- | --- | --- |
| `id` | String PK | |
| `userId` | String FK → `User.id` CASCADE | indexed |
| `productId` | String | `plus_pass_5h` \| `plus_pass_7d` \| `plus_pass_term`. **Not `plus_voice_30`** — a voice pack is a balance, not a pass, and never becomes a `PlusPass` row (Decision R) |
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

**Added for voice: `voiceSecondsRemaining` (Integer) and `voiceAllowanceSourceId` (String, nullable).** Unbundling voice (§6.3) means it needs its own counter, and it has to be a counter rather than a derived value because the whole point is that voice draws down independently of the unit window. Seconds rather than minutes: a `study_voice` relay bills continuously and rounding a 40-second session up to a minute is a charge the learner did not incur. `voiceAllowanceSourceId` records which pass or subscription period granted the current balance, so the sweep in Decision E can zero it when that source ends rather than letting a pass's voice minutes outlive the pass — the same leak Decision C's denormalisation exists to prevent for entitlement.

**A `plus_voice_30` purchase adds 1 800 seconds to the same counter** and writes a `PlusPurchase` row with no `PlusPass` behind it — the first purchase in the plan that grants no entitlement. It does **not** change `voiceAllowanceSourceId`: bought minutes inherit the expiry of whatever entitlement is already active, which is what makes them expire with it (Decision R) and what makes the pack refusable when there is no entitlement to inherit from.

**This is the only second meter, and it is a deliberate partial retreat from §6.2.** The argument for a single cost-denominated unit was that one currency is comprehensible and two are not. The retreat is justified by the 40× cost ratio: a single currency means either pricing every product for the voice case, which makes text-only learners subsidise a feature they do not use, or pricing for the text case and losing money on voice users. **Decision R is the rule that stops a third**, and it is worth reading before adding any counter.

**`User`** — dropped in the same migration: `creditsUsed`, `creditsPeriodStart`, `creditsPeriodEnd`, `creditsSoftCap`, `creditsHardCap`, `creditsUsedToday`, `creditsDailyLimit`, `lastDailyReset`, `purchasedCreditsBalance`. One migration, not two. The earlier draft staged this over two releases to keep a rollback path; with **no paid users and no purchased balances to honour** there is nothing to roll back to and nothing to preserve.

**Dropped tables**: `CreditPack`, `CreditPurchaseTransaction` — unconditionally, per Decision H as revised. The Phase 2b row count confirms they are empty before the Phase 4 migration drops them; it is a precondition check, not a decision point.

**Kept but no longer written**: `ReferralRewardClaim` (superseded by the ledger; the referral *link* tables and `User.referralCode` stay and are the input to qualification), `AdRewardClaim` (Decision O — kept so the redesign is not foreclosed), `ResourceUploadReward` (Open Question 11).

Unchanged: every space-scoped table and column — `SpaceMember.seat_tier`, `Space.credits`, `Space.credits_limit` (Decision F).

## 9. API surface

New, under `/api/v1/billing`:

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/plans/catalog` | the personal products available **to this learner's territory** (five in NGN, four elsewhere), reference prices, store product ids per platform |
| GET | `/passes` | `{active, inventory[], history[]}` — the source of truth for pass ownership |
| POST | `/passes/{id}/activate` | `200` → `Entitlement`; `409 PASS_REDUNDANT` \| `PASS_ALREADY_ACTIVE` \| `PASS_CONSUMED` |
| POST | `/passes/checkout` | web only — Stripe or Paystack one-time session for a pass **or the voice pack**. `403 VOICE_PACK_REQUIRES_PLUS` when `plus_voice_30` is requested without an active entitlement (Decision R) |
| GET | `/voice/balance` | `{secondsRemaining, expiresAt, packPurchasable}` — the voice counter, separate from `/billing/usage` because it is a separate meter |
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

### Phases 0–2b — done, with what still binds

**Complete.** Full narration of each phase lives in git history; what follows is only the outcomes later phases depend on, plus the items still open.

**Phase 0 — decide.** Three research questions, all answered.

- **The rate card was wrong.** `gemini-3.5-flash` was priced at $0.50/$3.00 against a published $1.50/$9.00 — 3× low, in *two* tables (`cost_calculator._EXACT_MODEL_PRICING` and `cost_tracker.PROVIDER_PRICING`), with five tests asserting the wrong figure. Both corrected. `gemini-3.1-flash-lite` at $0.25/$1.50 was right, and one correct row is what stopped anyone checking the next. Rates now in §6.10.
- **The `max_tokens` audit was withdrawn, and the premise was the plan's own error.** `max_output_tokens` is a **ceiling, not a charge** — `llm/__init__.py:137` says so in as many words — so lowering it saves nothing. Worse, lowering it re-opens truncations this codebase had already diagnosed five separate times (diagram `1200→2048→8192`, grounded search `2048→8192`, lesson `4096→8192`, reflection title `800→2048`, home guidance `500→1200`), because these are thinking models and **reasoning tokens draw from the same output allowance**: the visible reply is cut off while the budget looks generous. Measured case at `resource_service.py:278` — `thoughts_token_count=1067` of 2 000, `finish_reason=MAX_TOKENS`, reply cut at 364 characters, JSON never closed, learner told no resources existed.
- **The real knob is `thinking_budget`, and nothing sets it.** `0` disables thinking, `-1` is dynamic, a positive integer caps it. Three classes: **0** for transcription with no reasoning to do (`resource_service.py:304`, note summary, `type=quiz`/`type=summary` markdown, `memory_impl` summarisation); **~512** for bounded phrasing of pre-computed facts (goal insight, growth drivers, subject insight, home guidance — every prompt word-bounded and every figure supplied); **dynamic** for genuine reasoning (lesson bodies, quiz generation, course outlines, reflection narrative, diagrams). Two real over-provisions to fix alongside: `narrative_cache.compose_json:237` hardcodes one budget for four panels of very different sizes — **add a per-caller parameter, do not lower the default** — and `resource_service.py:304` sits at 8 192 to transcribe text it was just handed. Then stop guessing: `finish_reason == MAX_TOKENS` is already extracted and `GroundedResult.truncated` already carries it, so **count it per operation and set budgets from data**.
- **Chat context is where the input money is, and `HISTORY_LIMIT` is not the lever** — leave it at 12; it is what makes "what did you just say" work. Ranked: **(1) context-cache the stable prefix**, the single biggest win — [cached input tokens cost 10% of standard](https://cloud.google.com/blog/products/ai-machine-learning/vertex-ai-context-caching), and two blocks qualify while nothing caches today: the system instruction (`prompts.py:6-63`, ~1 200 tokens, identical for every learner but a trailing name line) and the tool declarations (`gemini_chat_tools.py:109-131`, rebuilt on every call including an `_uppercase_types` walk). Caching is **prefix-based**, and `context.py:70` opens every prompt with a timestamp, which defeats it on its own. **(2) Cap the four uncapped blocks** — `topicResources`, `topicUploadedResources`, `knowledgeBaseContext`, `memory_context` are `str(...)`'d whole (`context.py:96-104, 118, 208`) while everything in the learner-profile family is capped at 120–600 characters. Two are inert only because `attach_topic_resources` is a stub; cap them before it is implemented. **(3) Truncate history message bodies** — all 12 go in whole and thread images are re-sent as inline bytes. **(4) Fix `estimate_prompt_tokens`** (`ask_service.py:163-192`), which counts neither the system instruction nor the tool schemas, so the pre-flight credit check under-charges every turn — the same class of defect as the voice rate. *Content was rephrased for compliance with licensing restrictions.*
- [ ] **Still open:** Open Question 10, the points price of the 7-day pass. Cheap to change later, so it blocks only Phase 4b.

**Phase 1 — the money path is reachable.** `billing_router` and `webhooks_router` mounted; the four personal catalogue entries rewritten with the space-scoped two untouched; credit packs and the rewarded-ad path deleted; `TRIAL_DAYS_MAIGIE_PLUS = 3` with `TrialService.TRIAL_DURATION_DAYS` agreeing; `openapi.json` and the generated client types regenerated. Three things from it that still constrain:

- **`PlanId` deliberately still accepts the six withdrawn ids**, so a stale client gets `410 this was retired, here is what replaced it` rather than `422 not a valid plan`. A plan removed from the catalogue is not a plan that never existed.
- **Both rails read one plan list.** `paystack_service` imports `PLAN_IDS` and `DEPRECATED_PLAN_IDS` from `stripe_service` rather than holding a copy — the copy had already diverged when yearly Plus was withdrawn, so Stripe refused it while Paystack went on selling it.
- **`PlanItem` is a `CamelModel`**, changed safely only because the endpoint had never been mounted and no client was reading the old spelling.

**Phase 2 — one resolver.** `entitlement_service.resolve` is the only thing that decides whether a learner is Plus (Decision B). `require_premium`, `PremiumUser` and `PAID_TIERS` deleted; `feature_tier_service.get_effective_tier` and the personal branch of `feature_flags.effective_tier_for_request` repointed at it; `GET /billing/entitlement` serving `source` rather than leaving clients to infer it. Two properties worth keeping in mind:

- **`personal_tier` was removed from `effective_tier_for_request`'s signature, not merely left unread.** It was a pre-loaded `User.tier` offered as a way to skip a database read, and it can no longer answer the question — a trialling or pass-holding learner has `tier == "FREE"` and is entitled to Plus models. Leaving the parameter would have let a caller reintroduce drift 11 through the door marked optimisation, which is how the defect arrived the first time.
- **The pass branch was written and tested before passes existed.** `_compose` is pure over `(subscription_tier, subscription_period_end, active_pass, active_trial)` and `_read_active_pass` is a named seam returning `None` until Phase 4. An unknown pass product id falls to the **smallest** allowance, deliberately: under-granting is a support ticket, over-granting is COGS.

**Phase 2a — the review findings.** All eleven closed. The two that other sections depend on:

- **All three webhooks fail closed.** An unset secret refuses ingestion with `503` so a real event is redelivered rather than lost; Stripe refuses on an empty `STRIPE_WEBHOOK_SECRET`; the Paystack HMAC condition is inverted so an empty key fails rather than short-circuits; the Play RTDN endpoint verifies the Pub/Sub push **OIDC bearer token** against Google's certs and checks the token's `email` against `GOOGLE_PUBSUB_SERVICE_ACCOUNT_EMAIL` — a Google-signed token proves Google minted it, not that our subscription sent it. Handler exceptions answer `500` so the provider retries; **only a processed event answers `200`.** Before this, `webhooks.py:40-42` parsed an unverified Stripe body when the secret was empty, and that handler writes `User.tier` — an unset secret let any caller grant themselves `PREMIUM_MONTHLY`. Covered by `tests/test_billing_webhook_auth.py`.
- **`resolve()` is memoised per HTTP request** in a `ContextVar`, opened by `EntitlementScopeMiddleware`. The cache exists **only inside a scope**, which is correctness rather than a shortcut: a `study_voice` relay runs for minutes and bills every tick, so a task-scoped memo would let a pass expire mid-session and go on being honoured until the learner hung up. Websockets get no scope and resolve fresh. Pure ASGI, not `BaseHTTPMiddleware`. `trial_service.start_trial` calls `invalidate()` because it resolves `free` to check eligibility and then writes the trial.
- **The `usage_note` counts are out until Phase 3 can honour them.** They promised the §6.3 window allowances against a meter that implements a monthly token cap — for `plus_monthly`, ~19× the voice minutes the live meter funds. `test_no_note_promises_a_figure_the_meter_cannot_honour` forbids the words `turn`, `minute`, `message` and `credit` in a note, and **is written to be deleted by Phase 3** in the change that makes the figures true.

**Phase 2b — the NGN rail is open.** `paystack_service` ported off Prisma; both routes mounted; the webhook handlers fail loudly instead of returning `200` over a discarded event. `db_client` removed from all nine signatures, attributes snake_cased, `datetime.utcnow()` → `datetime.now(UTC)`. Two repository methods added: `find_user_by_paystack_subscription` (a `subscription.disable` identifies the learner by subscription code and nothing else) and `find_user_by_email`.

**The subscriber count, taken 2026-09-01 against production**, by `scripts/count_legacy_commercial_state.py` — read-only, committed and re-runnable, so it never has to be re-derived from memory:

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

**There is no payment relationship anywhere in the database.** Not one Stripe, Paystack or Play identifier. The single `PREMIUM_MONTHLY` row carries no subscription id, so it is a tier set by hand rather than a subscriber; its null `subscriptionCurrentPeriodEnd` is a live example of why `_subscription_lapsed` treats absent as "not lapsed". `LEGACY_PLUS_TIERS` is deleted and **the writers were narrowed in the same commit** — `_price_id_to_tier` and `_plan_code_to_tier` now emit only `PREMIUM_MONTHLY` or `FREE`, with a test asserting every tier a writer can produce is a subset of `PLUS_TIERS`. The resolver and the writers were only ever wrong apart.

**NGN amounts are read from config, not hardcoded.** `_plan_amount_kobo` was sending `"10000"` (₦100), harmless for subscriptions because the plan overrides the amount and **fatal the moment Phase 5 adds one-time pass charges**, where nothing overrides it. Fixed, with the ₦2 500 flat-fee reasoning recorded beside the constants. §5.7.3 carries the values, **two of which are still wrong in code**.

Open from Phase 2b:

- [ ] Delete `credit_purchase_service.fulfill_purchase` and `_send_purchase_receipt_email` — confirmed **zero callers**, so ~180 lines are dead. Deferred deliberately: it is dead rather than wrong, Phase 3 drops the tables it reads, and folding a large deletion into the commit that opened the NGN rail would have made both harder to review.
- [ ] Delete the four `STRIPE_PRICE_ID_STUDY_CIRCLE_*` / `_SQUAD_*` and four `PAYSTACK_PLAN_STUDY_CIRCLE_*` / `_SQUAD_*` settings (§5.1), and the retired branches of `_plan_code_to_tier`, `_price_id_to_tier` and `_assert_price_id_is_active`. `DEPRECATED_PLAN_IDS` keeps all six ids.
- [ ] Tests: initialize returns an authorization URL with the NGN amount from config; verify promotes `FREE` → `PREMIUM_MONTHLY` and sets the period end; `charge.success` on a renewal extends it; `subscription.disable` returns the user to `FREE`; a failed signature answers non-`200`; a retired plan id answers `410` on the Paystack door as well as the Stripe one.

### Phase 3 — Reprice voice, redenominate usage, add windows

**Runs first among the implementation phases.** The free-voice exposure in §6.2 is live and costs roughly $150/month per free user who finds it. The one-line mitigation — raise the voice price — can ship ahead of everything else in this phase.

- [x] **Live voice repriced. `GEMINI_LIVE_CREDITS_PER_MINUTE: 100.0 → 10_000.0`, and `GEMINI_LIVE_MIN_SESSION_CREDITS: 500 → 10_000`.**

  A conversational minute of `gemini-3.1-flash-live-preview` costs about **$0.023**; a text token on `gemini-3.5-flash` at the verified rate costs about $0.0000020 at a realistic 8 000-in / 600-out mix. So a voice minute cost what **~11 400 text tokens** cost and was billed as 100 — **under-priced by about 100×**. Free tier is 5 000 charged credits/day and `study_voice` has no tier gate at all (drift 5), so a free learner could run **250 minutes of live voice a day: roughly $170/month, at zero revenue.** The session floor moved with the rate because 500 credits was five minutes before and would have been **three seconds** after, silently removing the wall-clock minimum charge and the pre-start affordability check with it.

  **How it survived: nothing was broken and no test looked at the number.** `study_voice/billing.py`'s docstring described the 100 figure and called it "a pricing question flagged in the design document, not a bug here" — a comment that noticed the problem and gave every later reader permission to move on. Two tests now check the *configured* rate rather than the arithmetic: one that it stays within an order of magnitude of the cost basis, one that the session floor is at least a minute. Both deliberately loose, to catch drift rather than pin a figure Google will move.

  **Free voice went from effectively unlimited to ~2.5 min/day, and §6.3 has since taken it to zero** — set knowingly, which is the point. A meter that under-charges by 100× is not a free allowance, it is an accident.
*The items below were done in revisions 8–10 and left unticked, which made this checklist read as though Phase 3 had barely started. Corrected against the code rather than against memory: each claim below was checked by grep before the box was ticked, and the four that were **not** done are still open at the bottom.*

- [x] **Migration `067_usage_windows`** (not `063` — the numbering moved as other branches landed): `usageWindowStartedAt`, `usageWindowUnitsUsed`, `usageMonthStartedAt`, `usageMonthUnitsUsed` added; the nine retired credit columns dropped unconditionally. `usageWindowStartedAt` is nullable and **null means elapsed**, so the first billable operation opens the window and there is no initialisation step — and therefore no user who can be missing one. The drop carries no data forward, on the zero-payment-relationship measurement: the old figures are denominated in a token scaled by a 0.2 multiplier, and converting them would mean inventing an exchange rate for a currency being retired.
- [x] **The cost-denominated `usage_unit` is live.** `units_for_tokens` prices a completed generation from `cost_calculator`, rounding **up** — rounding to nearest would let an operation cheaper than half a unit cost nothing, and "free if small enough" is how an unmetered surface starts. `TOKEN_MULTIPLIER`, `apply_token_multiplier`, `CREDIT_COSTS` and `CREDIT_LIMITS` are gone; every remaining mention in `src` is prose in a docstring recording the deletion.
- [x] **The non-space path of `check_credit_availability` and `consume_credits` is rewritten** against window + monthly backstop, and `initialize_user_credits`, `reset_daily_credits_if_needed`, `ensure_credit_period` and `reset_credits_for_period_start` are deleted. **Decision F held**: the `space_id` early-return and the space branch are untouched, which is what made this separable.
- [x] **`min_session_credits` → `min_session_units`**, at the corrected rate, so a session cannot start that the allowance cannot fund. Renamed rather than reinterpreted: leaving the word "credits" on a quantity denominated in cost units is how two meanings end up in one name.
- [x] Deleted `billing.reset_credit_periods` and `progress.daily_credit_reset` (closes drift 13). A rolling window has no period boundary to sweep, so both jobs had nothing left to reset.
- [x] **The five referral-reward functions and `REFERRAL_REWARDS` are deleted.** Nothing tops up a window (§6.3). `generate_referral_code`, `get_or_create_referral_code`, `track_referral_signup` and `get_referral_stats` survive as the input to Phase 4b.
- [x] **`GET /billing/usage` ships, returning a percentage and a reset time.** One correction to this item's premise, found on doing it: **`GET /users/usage` never existed server-side** — the web client has been calling it and taking a `404`, so there was nothing to rewrite. Phase 7 points the client at the endpoint that exists.
- [x] Refusals carry `windowResetsAt` structurally rather than in the sentence, because the message is rendered in the learner's own timezone and a server-side "3:40 PM UTC" hands a small arithmetic problem to someone who has just been refused. The five-clause refusal reconciling a daily cap, a monthly cap and a purchased balance is gone.
- [x] **`LimitReachedEmailLog.periodEnd` → `windowDay`**, and this deviates from the item as written on purpose. The plan said the dedupe key moves from period to *window*; a 5-hour window permits 4.8 a day, so a per-window key would mail a heavy free learner up to five times daily where the old behaviour was once a month. The key is the calendar **day**. The in-app refusal already carries the reset time; an email is for the learner who is not looking at the app, and that learner needs telling once.
- [x] **The figures are back in `usage_note`, derived rather than retyped.** `_voice_minutes_note` computes them from `entitlement_service.WINDOW_ALLOWANCE_*`, so the copy cannot drift from the allowance — which was the whole reason Phase 2a pulled the counts. **But it derives voice from the *unit* window, which §6.3 says voice must stop being drawn from**, so the voice half of every note is honest about the running meter and does not yet describe the design. It becomes correct in the same change that adds the voice counter, below, and not before.
- [x] Tests: `tests/test_usage_window_meter.py`. Scope guard held — `test_circle_billing.py` and `test_seat_service.py` pass unmodified.
- [x] Rate card verified and corrected (Phase 0). This had to come first: the meter is denominated in measured COGS, so a 3× error in the rate is a 3× error in every allowance derived from it.
- [ ] **Add the voice counters**: `voiceSecondsRemaining` and `voiceAllowanceSourceId` on `User` (§8), and draw live voice from them rather than from the unit window (§6.3). The sweep must zero the balance when its source pass or subscription period ends, or a pass's voice minutes outlive the pass. `GET /billing/voice/balance` ships with them — a counter the learner cannot see is a counter they will be surprised by.
- [x] **The instrument exists.** `record_units` logs `usage: user=… operation=… units=… window=… month=…` on every charge, which is units per operation per learner — the measurement §6.7 needs. Ticked as *built*, not as answered.
- [ ] **Read it.** Every payer rate and consumption figure in §6.7 and §6.8 is still a guess, and **this remains the largest open risk in the plan** — it is now a question of a month of traffic and someone aggregating the logs, not of code. The forecast column that matters most is "typical COGS", because Decision Q means the *ceiling* figures cannot be wrong while the typical ones are entirely assumption. Nothing in the repo can close this item; it closes when a real distribution replaces the estimates.

### Phase 3b — Meter everything else (Decision L)

Without this, §6.7's contribution is roughly **−$270 at 10 000 MAU** rather than +$754. It is not optional and it is not a follow-up.

- [x] **Usage metadata is plumbed through.** `intelligence.reasoning.llm.generate_content_with_usage` returns `(text, GenerationUsage)` and `generate_content` delegates to it and drops the usage — one implementation, so the two cannot diverge. All three provider callables in `llm_resilient` now return a `ProviderReply(text, usage)`. Gemini reports usage reliably; OpenAI and Anthropic report it where the SDK exposes it. **`thoughts_tokens` is carried separately and billed with output**, because reasoning tokens draw from the output allowance and omitting them would undercharge the most expensive operations by the most.
- [x] **Metered inside the attempt loop.** `_meter` is called per provider call, so retries are charged (Decision L). It runs **before** the empty-reply check, because an empty reply still consumed tokens — charging on delivery instead of on spend is exactly how this cost stayed invisible.
- [x] **`record_units` added to `credit_consumption_service`: accounts, never refuses.** A deliberate second function rather than a flag on `consume_credits`, because they are different things — `consume_credits` is a *gate* called before an operation with an amount known in advance, and this is *accounting* called after a generation that already happened. Charge on success, absorb on failure.
- [x] **A reply with no usage is logged at `warning`, not charged zero.** Charging zero is the failure mode that lets an unmetered surface return: it looks like a working meter and costs the same as none.
- [x] **`UNCHARGED_OPERATIONS`** exempts onboarding and memory extraction on principle (§6.6). An *unlabelled* call is charged — the default has to be "charge", or exemption becomes what happens by forgetting.
- [x] Tests: `tests/test_llm_metering.py`, 10 assertions across charging, retry charging, exemptions and meter failure. **One of them found a real defect on first run:** `_meter` documented itself as never raising but only relied on `record_units` swallowing database errors. Anything else — pricing an unknown model, the import — escaped into the attempt loop, where `except Exception` counted it as a *provider* failure and retried it. **An accounting error presented as an outage.** `_meter` now catches for itself, and the test pins the call count as well as the result.

- [x] **An exhausted learner is refused before a provider is called.** `has_headroom` answers the only question a measured operation can ask in advance — *is there anything left at all* — and `_refuse_if_exhausted` runs it **once per logical operation, not once per attempt**, because a retry that re-gated could refuse an operation halfway through its own chain after we had already paid for two calls. It **fails open**: a gate that cannot read the meter must not become an outage, since failing closed would turn a database blip into a product-wide refusal. A window can still be exceeded by the cost of one operation in flight — bounded, self-correcting, and the price of measuring cost rather than inventing it.
- [x] **`ESTIMATED_OPERATION_UNITS` is deleted, along with the double charge it caused.** All three operations that read it — `voice_session_note`, `note_merge`, `study_diagram` — reach a provider through `llm_resilient` *and* charged a flat estimate of their own, so for one commit they were **billed twice: once estimated, once measured.** They now gate with `has_headroom` and let the chokepoint charge. `TestNothingTabulatesPricesAnyMore` replaces the class that guarded the table and guards its absence instead — a table is not a neutral thing to leave lying around, and the one before it priced a voice minute two orders of magnitude low for the life of the product.
- [x] **A refusal is never swallowed into a JSON fallback.** `generate_content_json` catches `Exception` and returns `fallback`, so a `SubscriptionLimitError` would have become an empty object and a learner out of allowance would have seen an empty quiz rather than being told why. It re-raises above the broad handler now.
- [x] Labels passed at the three converted call sites. 15 metering tests.

Remaining in this phase:

*Three items in this list were duplicated, each written twice in different words as the phase was revised — including the drift 23 item, twice, with different open sub-decisions attached. De-duplicated in the pass that closed it.*

- [x] **Every call site is labelled.** `operation` no longer defaults to `"unknown"` anywhere in `src`. This stopped being a reporting nicety the moment Decision P's split existed: the split is keyed on the operation, so an unlabelled call cannot be placed on either side of the threshold. It was a prerequisite, not tidying.
- [x] **The chokepoint is entitlement-aware and drift 23 is closed.** `model_for_operation` resolves the tier from `entitlement_service` — the one resolver, so a trial and a pass count — and returns `GENERATION_PREMIUM` or `GENERATION_STANDARD` from `registry`, for operations in `QUALITY_SPLIT_OPERATIONS` only. Resolved once per logical operation so a retry cannot switch rates mid-flight; fails **to the cheap model**, unlike the headroom gate beside it that fails open, because the wrong answer here is a cost rather than an outage. Both open sub-decisions had already been settled by Decision P, and the second one's premise was wrong anyway: `gemini-2.5-flash-lite` was never the candidate, because the whole 2.5 family shuts down in October 2026. Details and the three things the audit had understated are in drift 23.
- [x] **Six call sites were not on the chokepoint at all**, and are now: `document_impl`, `note_service` (rewrite and summary), `discovery_service`, `auto_setup_service` and the topic quiz/summary route all called `intelligence.reasoning.llm.generate_content` directly. So they had no meter *and* no gate *and* no retry, not just no tier — which means this item closed part of the metering gap as a side effect of the quality one.
- [x] Exempt onboarding auto-setup and memory extraction from charging, on principle (§6.6) — `UNCHARGED_OPERATIONS`. Decision P's threshold also keeps them off the degraded model without a second exception list, and a test asserts the two sets do not intersect rather than trusting the rule.
- [x] Charge on success, absorb on failure, following `study_voice/notes.py:172-220`.
- [ ] **Meter `generate_grounded_content`.** The resource search is the most expensive operation in the product (~1 600 units) and `GroundedResult` carries no token counts, so nothing can charge for it — the recorded cost of a recommendation is currently its cheaper formatting half. It now picks its *model* by tier through the exported `model_for_operation`, so the quality half is done and only the meter is missing. Needs the return shape widened.
- [ ] Redirect the four remaining in-scope stragglers through the chokepoint: `memory_impl.py:49`, `planning_impl.py:55`, `schedule_regen_impl.py:213` and `:227`. Leave `space_impl.py:1324` (Decision F).
- [x] **`LLM_ENABLED_PROVIDERS` reaches generation.** It only ever reached chat, so turning OpenAI off would have left it serving all 27 generation surfaces as a fallback — and as the *primary* for any learner whose `preferred_llm_provider` was `"openai"`. `enabled_providers()` now filters both, in the supported order rather than the configured one so the sequence is a property of the code and not of how an environment variable was typed. An empty or unrecognised list degrades to the default and logs at `error`: disabling every provider takes every AI surface down at once and is far likelier to be a typo than a decision. Six tests.
- [x] **A database read removed from every generation in the product.** `_resolve_provider` loads `LearningProfile` to honour `preferred_llm_provider`, and a preference between one option is not a preference — with Gemini the only enabled provider that read could not change its own answer. Short-circuited rather than deleted, and keyed on the *enabled* set rather than on `SUPPORTED_PROVIDERS`, so it re-enables itself along with the second provider. Worth noting how it surfaced: it was a **test** failure, not a profiling result. Redirecting six call sites onto the chokepoint made them read a table their fixtures do not create, which is the kind of signal that only appears when a wrapper is doing more than the call site needs.
- [x] **Gemini is the only enabled provider, and the fallback chain is no longer a cost question.** `LLM_ENABLED_PROVIDERS` defaults to `"gemini"`; no OpenAI or Anthropic key is provisioned. So the worst case for one operation is **three Gemini attempts, not nine mixed calls**, and the untiered-fallback exposure recorded in §6.3 is unreachable rather than merely rare — there is no second provider in the chain to fall out to. This also retires the "shorten the retry chain because retries are billable" item as a *cost* item: what remains is latency, which is a different argument and a smaller one.
- [ ] Cap total attempts across providers, for when a second provider is enabled again. `_MAX_RETRIES + 1` is per provider with no ceiling on the sum, so the nine-call worst case is dormant rather than removed — it returns with the next key that is provisioned, and the enabling change is one environment variable. Worth a cap now while the reasoning is written down.
- [ ] Delete the dead `CREDIT_COSTS` entries `ai_course_generation` and `ai_action` — cost is measured, not tabulated.
- [ ] **Cache home guidance.** `guidance_engine.py:267` fires on every home load; `growth_service` and `goal_insight_service` already avoid this through `narrative_cache`'s `inputs_hash`. Reuse it. Uncached this is ~$2.10/month per active learner, more than a Plus subscription's whole margin.
- [ ] **Set `thinking_budget` per operation class**, per Phase 0's three-class split. This replaces the withdrawn `max_tokens` audit — a ceiling is not a charge, and lowering these would have re-broken five separately-diagnosed truncations. Unowned; Open Question 1.
- [ ] Tests: every operation in the §6.5 table deducts units; a retry storm is counted not swallowed; a failed generation charges nothing; onboarding and memory extraction charge nothing.

*"Exempt onboarding auto-setup and memory extraction from charging" and "charge on success, absorb on failure" appeared twice each in this list, once ticked above and once not. Both are done; the unticked copies are removed rather than ticked, since a checklist that lists an item twice cannot be read as a count of what is left.*

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
- [ ] Celery beat: `billing.sweep_expired_passes` every 5 minutes. **The same sweep zeroes `voiceSecondsRemaining`** when the entitlement its `voiceAllowanceSourceId` names ends — one sweep, two counters, because a second sweep is a second thing that can fall behind.
- [ ] `voice_service.py`: `credit(user_id, seconds, source)`, `consume(user_id, seconds)`, `balance(user_id)`. `credit` **refuses without an active entitlement** (Decision R) and inherits the active entitlement's expiry rather than setting its own.
- [ ] `GET /billing/voice/balance`, and `plus_voice_30` accepted by `POST /billing/passes/checkout` with the `403 VOICE_PACK_REQUIRES_PLUS` refusal.
- [ ] Notifications: pass activated (with expiry time), 30 minutes remaining, pass ended. **Voice: at 5 minutes remaining, carrying the pack as the action** — this is the one notification in the plan that is allowed to name a purchase, because a learner mid-session who is about to lose the tutor needs the option before the silence, not after it.
- [ ] Tests: one-active invariant under concurrency, `PASS_REDUNDANT` for each of the three reasons, pass grants every Plus capability for its duration and none after, activation resets the window, expiry forfeits remaining time. **Voice pack: it stacks where a pass refuses** (two packs credit 3 600 seconds, two passes give `409`), a purchase without entitlement is refused rather than banked, bought minutes expire with the entitlement that was active at purchase, and **a voice pack does not create a `PlusPass` row**. Scope guard: an activated pass does not change any space-scoped read.

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

**Store and processor setup comes first, and §5.7 is the checklist.** Do not duplicate it here — it holds the ordering, the product matrix, the per-provider steps and the parity checks, and a second copy would be a second thing to keep true. Two properties of it that govern this phase:

- [ ] **§5.7.1–§5.7.5 are complete before any verification code is written.** Every server task below needs the product ids to exist, and the Apple prerequisites (§5.7.5) need a **Paid Applications agreement with banking and tax**, which is not an engineer's to supply and is the single item most likely to add a week.
- [ ] **§5.7.6's parity checks are done by hand and recorded with a date.** Nothing in the repo can read a console, so the trial being `3` in four places and every NGN price matching §6.8 are checks a person performs or nobody does.

Server rails:

- [ ] **Stripe**: one-time Checkout for the two USD passes and the voice pack (`mode: payment`), the new `$9.99` subscription price. `checkout.session.completed` → `PlusPurchase` → `pass_service.grant`, **or `voice_service.credit` for `plus_voice_30`**, which grants seconds and no entitlement (Decision R). **The Term Pass has no Stripe rail** and must not acquire one (§5.7.1).
- [ ] **Enforce `VOICE_PACK_REQUIRES_PLUS` at the checkout boundary on every rail**, not in the client. A cached catalogue can reach a Play or StoreKit purchase sheet, so a store purchase of `plus_voice_30` by a learner with no entitlement must be **verified, refunded-by-revocation and not credited** rather than silently banked — the one case in this plan where a valid store receipt is deliberately not honoured, and it needs a test.
- [ ] **Paystack**: NGN one-time charges for **all three** passes including the Term Pass, from the `PRICE_NGN_*` settings; extend `handle_paystack_webhook` to attribute a one-off `charge.success` to a pass grant rather than a subscription. **Set `PRICE_NGN_PLUS_PASS_7D = 150_000` and add `PRICE_NGN_PLUS_PASS_TERM = 550_000` first** — the former still holds the retired ₦1 800 and the latter does not exist, and for a one-off charge nothing overrides a wrong amount (§5.7.3).
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
- [ ] **Voice balance, shown separately from the window and only to learners who have voice.** Two meters side by side is the cost of Decision R's second counter, and the mitigation is that a free learner sees one meter, not two with one at zero. Buy-more action reads `packPurchasable` from the server rather than inferring entitlement client-side.
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
- [ ] Pass wallet screen; replace `src/app/earn/buy-credits.tsx`. Window meter and voice balance in `src/app/profile/usage.tsx`.
- [ ] **`StudyVoiceModal.tsx` shows minutes remaining before the session starts, and offers the pack at 5 minutes left.** It is the one surface where the voice balance is decision-relevant in the moment rather than informational, and it currently shows no billing state at all (drift 19).
- [ ] **Delete `src/app/earn/watch-ad.tsx`** and any ad-reward call site (Decision O). Rebuild `src/app/earn/` as the points wallet: balance, referral code and share sheet, pending referrals with days-active progress, next expiry date, redeem-to-pass action.
- [ ] Pass paywall copy says "your personal workspace", not "everywhere" (Decision F).
- **iOS.** The console and portal work is Phase 5 (§5.7.5), not here — it gates the server verification code, so it cannot sit in the last client phase. `expo prebuild` and "StoreKit capability" are **not tasks**; §5.6 says why. What remains:
  - [ ] **Add the StoreKit branch to `useStoreBilling`.** This is the real iOS work. `react-native-iap`'s iOS pod (`NitroIap`) is already linked in `ios/Podfile.lock`, so the native module is present and building — the only thing stopping iOS purchases is the `Platform.OS !== 'android'` early return at `usePlayBilling.ts:88-93`, which refuses the platform its own dependency supports.
  - [ ] **Add `submit.production.ios` to `eas.json`** (`appleId`, `ascAppId`, `appleTeamId`). The `submit.production` block currently holds `android` only.
  - [ ] Uncomment the iOS EAS jobs at `.eas/workflows/deploy-production.yml:72-108` (`get_ios_build`, `build_ios`, `submit_ios_build`, `publish_ios_update`).
  - [ ] Submit the first iOS build **with the four IAP products attached** (the three global ones plus the NGN-only `com.maigie.plus.passterm`) — Apple reviews in-app purchases against a build, and this is the highest rejection-risk submission the project will make. Budget 1–2 weeks and expect one rejection round.

### Phase 8 — Copy, and existing customers

- [ ] `maigie-public/plan-data.ts`: the personal products for the visitor's market, new prices, **`trialDays: 3`** on monthly only. Delete `CreditPacks.tsx`. **`CIRCLE_PRODUCTS` and `CircleProductsSection.tsx` stay** (Decision F).
- [ ] Referral and points copy states the qualification plainly — "when they've studied on 7 different days" — and the expiry plainly. A reward whose condition is in the small print produces support tickets from exactly the learners we most wanted to reward.
- [ ] Remove every "watch an ad" and "earn credits" claim from the public site and the FAQ (Decision O).
- [ ] Rewrite `PRICING_COMPARE_ROWS` against §5.3 — remove the five unenforced rows, state the window allowance instead of "unlimited", leave the three Circle rows alone.
- [ ] Fix the duplicated credit-pack prices in `landing/Pricing.tsx`; rewrite `content/faq/pricing-and-plans.yaml`, which still sells Study Circle at $9.99 and Squad at $14.99 — both retired personal tiers, not the live Circle Plan.
- [ ] Test asserting `plan-data.ts` matches `GET /plans/catalog` (Decision I).
- **No price migration is needed even though the price changed.** `PREMIUM_MONTHLY` goes $4.99 → $9.99, and with zero subscribers that is a new price rather than a migration: no Stripe price migration, no mandatory Play notice, no Apple consent flow. **This is only true until the first subscriber exists** (§6.1).
- **No subscriber migration is needed either.** Phase 2b counted zero payment relationships, so there is nobody to move and no grandfathering machinery left to delete. If the count ever comes back non-zero, this step returns along with `LEGACY_PLUS_TIERS`.
- [ ] **Public-site prices come from §6.1 and §6.8, and the NGN ladder is ₦700 / ₦1 500 / ₦2 400 / ₦5 500.** The consoles themselves are §5.7, not here — this item is copy only.
- [ ] Add `PlanItem.availability` and make the public site respect it, so the Term Pass is not advertised outside Nigeria (§6.1). A catalogue that offers an unbuyable product is a defect this plan has already recorded once.
- [ ] Make `GET /plans/catalog` currency-aware rather than USD-only, serving the `PRICE_NGN_*` values. Store-purchased products display the store's own `displayPrice` regardless (Decision I); this is for the web rail and for copy.

## 11. Open questions

**Settled, recorded so they are not reopened.** *Is there pass-versus-subscription arbitrage?* No — the per-day ladder is correctly ordered in both currencies and three 7-day passes exceed a month. *Should monthly be $5.00?* No (§6.1). *Should passes be unmetered?* No (Decision E). *Does the 7-day trial survive the 7-day pass?* No — the trial is 3 days. *Should referrals stay capped at 10/month?* No — the 7-day qualification is the control (§6.9). *Should rewarded ads be re-pointed at the window?* No, withdrawn (Decision O). *Can points buy the subscription?* No, and by construction rather than validation (Decision O). *Does iOS ship after Android?* No, together. *Is anything grandfathered?* No — zero payment relationships in the database (Phase 2b). *Is the rate card current?* It was not; corrected, and Decision Q means it no longer moves any margin here. *Was the `max_tokens` audit worth doing?* No — a ceiling is not a charge; `thinking_budget` is the real knob. *Does the 3-day trial need a 180-day cooldown?* No — 90 days, in `trial_service.TRIAL_COOLDOWN_DAYS` as the single source.

Still open, in rough order of what they cost:

1. **Who owns setting `thinking_budget` per operation class?** Unowned. This is where the output money goes, nothing in the codebase sets it, and Phase 0 has the three-class split ready to apply. The largest unclaimed cost saving in the plan.
2. **Who owns context-caching the chat prefix?** Also unowned, and likely the largest *input* saving — a 90% discount on the system instruction and tool declarations, both byte-stable across all learners. Requires splitting the prompt constant from its name suffix and moving the timestamp at `context.py:70` off the front, because Gemini caching is prefix-based.
3. **Is the free tier affordable at scale?** Free inference is $376 of $3 308 revenue at 10 000 MAU after the §6.5 fixes — still the largest single line item. It rests on two unmeasured assumptions: 50% of free MAU AI-active, and typical consumption around half the allowance. If either is materially higher, contribution goes negative. **Instrument before tuning** (Phase 3, last item).
4. **Is 250 points the right price for the 7-day pass?** 100 and 250 mirror the cash ratio, which is tidy but arbitrary — nothing says an earned currency should price like a sold one. 200 would make two referrals buy the better product cleanly and push learners toward the pass that actually establishes a habit; 300 would make the 5-hour pass the default redemption and leave a remainder to expire. Recommend 250 for launch and watch which pass gets redeemed. Blocks Phase 4b only.
5. **Refunds on an activated pass.** Apple and Google decide refunds unilaterally and neither asks first, so a learner can consume most of a pass and be refunded. Apple's `CONSUMPTION_REQUEST` lets us report usage and reduces this, but it is advisory. Recommend accepting the leakage and measuring it — a consumption cap that fires on a legitimate learner is worse than the loss.
6. **Is a 5-hour window right for Free?** Five hours permits up to 4.8 allowances a day, which the monthly backstop bounds but does not prevent. The length is shared with Plus for explainability and because it is the pass duration. A 12-hour Free window (~2/day) tightens it at the cost of two numbers to explain. Recommend 5h for both and let question 3's instrumentation decide.
7. **Should the 5-hour pass sit next to the 7-day pass?** $1.50 more buys 33× the duration, so the 5-hour pass is value-dominated for anyone uncertain how long they need. Its job is the sub-$1 impulse and the first card on file, not volume. Showing them side by side with equal weight makes the cheap one look silly; surfacing it contextually — at a paywall, mid-session — is probably where it earns its place.
8. **Should contributing a resource earn points?** Better than referral in principle, because it produces something other learners use. But "approved" implies a moderation process that does not exist, and points redeemable for real product make an unmoderated upload queue an attack surface. Deferred until moderation exists.
9. **What is the total points liability?** Every live point is deferred COGS at up to $0.003. Uncapped referrals make this unbounded in principle and 60-day expiry bounds it in practice; the missing number is qualified referrals per learner. A guess until Phase 4b's monitoring runs.
10. **What is the voice pack's real attach rate?** The revenue tables assume **12% of payers buy one pack a month**, and it is the weakest number in this document — voice has the least usage data behind it and the highest unit cost, so the plausible range spans an order of magnitude. At 30% the pack is a significant product; at 2% it is a rounding error that still needs four store SKUs and a second counter. **Build it anyway** — Decision R's argument is that it closes an unbounded exposure rather than that it earns $70 — but do not plan against the revenue, and let Phase 3's instrumentation set the real figure before anyone quotes it.
11. **Does the Term Pass cannibalise the subscription in Nigeria?** It nets $0.91/month against the monthly's $1.61, so a subscriber who switches costs us 43% of their revenue. The bet is that they were never a reliable subscriber — a mandate that fails in month two collects less than a term paid up front. Measurable the first month both exist, and if it is wrong the fix is pricing the Term Pass nearer four months of monthly, not withdrawing it.
