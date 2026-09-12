# Bug Hunt programme

A paid testing programme that runs in seasons: testers exercise the Maigie web, Android and iOS apps, submit bugs and feedback against their own Maigie account, and are paid per accepted submission — either as a Plus pass that appears in the Maigie app, or as cash to a Nigerian bank account. Season 1 is Nigeria-only and runs 14 days.

> **Status:** In progress · **Phases 1–7 complete** (foundation, seasons, intake, triage, admin dashboard, reward ledger, pass redemption, cash payouts, participant web app) · Phases 8–10 open · **M1–M5 shipped, the backend is feature-complete, and both client surfaces are built** · **M6 (Launch) now needs only Phase 8: emails, analytics, a staging walkthrough, `issues.maigie.com`, and Season 1 created through the admin editor** · updated 2026-09-12
> **Migration state:** `083_bug_hunt` **applied to staging 2026-09-12** — 8 tables, 3 partial unique indexes, 34 CHECK constraints, 22 foreign keys; 1 212 existing `User` rows untouched, since the migration is additive only (8 `CREATE TABLE`, 24 `CREATE INDEX`, no `DROP`, `ALTER` or `EXECUTE`). Read-only smoke test against the real schema returns `{"state":"between"}` from `GET /bug-hunt/program`. **Not applied to production**
> **Owner:** Product / engineering
> **Scope:** `maigie/apps/backend` (new `bug_hunt` domain) · `maigie-client/apps/bughunt` (new participant app) · `maigie-client/apps/admin` (triage + payouts) · `maigie-public` (one inbound link)
> **Testable surfaces:** web app (`maigie-client/apps/web`), Android and iOS (`maigie-mobile`)
> **Home:** `issues.maigie.com` — a standing surface, not a campaign microsite
> **Seasons:** recurring. **Season 2 is confirmed**, so multi-season is a build requirement rather than a future refactor (§3, Decision 12). Season 1 runs 14 days from launch
> **Depends on:** `identity` (accounts, `User.country`), `billing` (`pass_service.grant`, NGN pass prices), `admin` (`AuditLog`, staff/super-admin split), `shared.infrastructure.storage` (BunnyCDN, for attachments), `finance` (recording cash payouts as expense)
> **Related work:** [`notifications-platform-plan.md`](./notifications-platform-plan.md) · `maigie-public/docs/LANDING_REDESIGN_PLAN.md` (the `/educators` precedent: public page → backend persistence → admin review)

Update this doc as work lands. Checkboxes describe remaining programme work; the trailing bolded clause on a completed item records what actually shipped, with evidence and a date.

**Governing rules.**

1. Money is an append-only ledger in kobo. No mutable balance column, no floats, no staff-typed amounts on the reward path. Every figure a participant sees is a `SUM` over `BugHuntLedgerEntry`.
2. **A season is a row, never a deploy.** Opening Season 2 must be data entry in the admin dashboard, not a code change, a migration, or a redeployed frontend. Anything that would need editing to run a second season belongs on `BugHuntProgram`.
3. **The wallet belongs to the person, the season does not.** Balance, payout account and withdrawals are keyed on `userId` and outlive any single season. Only *earning* is scoped to a programme.

---

## 1. Purpose

Maigie ships three clients and has no systematic external test pass over any of them. Internal testing finds what the people who built it already expect to find. This programme buys two weeks of adversarial attention from real Nigerian students on real devices, and pays for it per finding rather than per hour — so the cost tracks the value delivered.

The secondary purpose is commercial: paying in Plus passes converts testing spend into product usage and gives us a cohort who has held Plus. Cash is the fallback for people who want cash, not the default.

## 2. Scope

### 2.1 In scope

- A public landing page describing the programme, the reward table, the rules and the dates.
- Registration by submitting a first bug or piece of feedback, reviewed and accepted or rejected. No participant list to maintain by hand.
- An authenticated participant dashboard: their submissions with status, their balance, redemption, withdrawal.
- Backend `bug_hunt` domain: programme config, applications, submissions, attachments, triage, reward ledger, pass redemption, withdrawal requests.
- Admin surfaces in `maigie-client/apps/admin`: application review, submission triage, ledger adjustments, withdrawal approval and payout recording.
- Country eligibility enforced server-side, per season. Season 1 is Nigeria-only.
- Recurring seasons: opening a season is an admin action, not a release (Decision 12).

### 2.2 Out of scope

- Automated payouts. Season 1 pays cash **by hand** from the existing business account and records the reference (§5.3, Phase 6). Paystack Transfers is a Phase 9 candidate, not a launch dependency — there is no payout code anywhere in the backend today, and building a money-moving integration is not a two-week-programme dependency.
- A public leaderboard, badges, or participant-to-participant visibility. Nobody sees anybody else's submissions.
- Programme access from inside the Maigie apps (no in-app "Bug Hunt" tab). The programme lives at its own origin so it can be closed without shipping a client release.
- Non-Nigerian participants, and any second currency.
- Automated duplicate detection. Duplicates are marked by a human against a canonical submission.

### 2.3 Why this ordering

The reward ledger (Phase 4) lands before either redemption rail, because both rails are debits against it and a debit path built before the ledger invariant is the way a participant gets paid twice. The participant app (Phase 7) is built against contracts that already exist rather than in parallel with them, because the one thing this programme cannot survive is a dashboard that shows a balance the server disagrees with.

Multi-season is built in Season 1, not retrofitted for Season 2. It costs almost nothing now — a `programId` on the right tables, season-varying values on the programme row, a wallet keyed on the user — and retrofitting it later means a data migration over rows that represent money people are owed. That is the worst possible thing to migrate.

## 3. Locked decisions

| # | Decision | Choice |
|---|----------|--------|
| 1 | Where the participant app lives | **New Nx app `maigie-client/apps/bughunt`**, port `4203`. Not `maigie-public` (needs authenticated state), not `apps/web` (must be deployable and versioned on its own) |
| 1a | The landing page | **Inside the new app, at its `/`.** `maigie-public` gets a short section plus a footer link pointing at it, and nothing else — one description of the programme, on the surface that owns the rules and the reward table it renders from the API |
| 1b | Domain | **`issues.maigie.com`.** Settled by Season 2 being confirmed: `bug-hunt.maigie.com` dates itself against a recurring programme, and a URL is the one piece of copy that ends up in screenshots, chat groups and printed material we cannot recall. `issues` also leaves room for ongoing reporting between seasons |
| 2 | Identity | **Existing Maigie account.** `bug_hunt` has no user table of its own; a participant is a row keyed on `User.id`. Login and signup reuse `/api/v1/auth/*` |
| 3 | Registration | **The application *is* the first submission.** One record, `isApplication = true`. Approving the participant and triaging that submission are separate acts |
| 4 | Eligibility | `User.country == "NG"`, checked server-side on every write. Unset country is prompted for and set via the existing `PUT /api/v1/users/me/country` |
| 5 | Money representation | **Append-only ledger, signed `amountKobo` integers**, modelled on the existing `points_service` / `PointsLedgerEntry` (`grant` / `balance` / `history` / `redeem`). Balance is a `SUM`. **Keyed on `userId`, not on a participation** (Decision 12). Debits are written under `SELECT … FOR UPDATE` on the wallet row |
| 6 | Reward amounts | Fixed matrix by category × severity (§5.2), **stored on the programme row** so each season records what it actually paid. **Staff never type an amount on the reward path** — they set category and severity, and the matrix decides. Off-matrix money is an `adjustment` and requires super admin |
| 7 | Pass redemption | `pass_service.grant(source="bug_hunt")` — grants into inventory, does not activate, exactly like a purchase. Priced from `PRICE_NGN_PLUS_PASS_*` in config, never hardcoded in the domain |
| 8 | Cash payouts | **Transferred by hand by an admin, then recorded.** Request → super-admin approval → admin transfers from the business account → admin marks the request `paid` with the bank reference → the participant's dashboard shows **Paid** → mirrored into `finance` as an expense (§5.3) |
| 9 | Attachments | BunnyCDN via the existing `storage_service`, prefix `bug-hunt/{participantId}/`. Max 3 files, 10 MB each, `image/*` and `video/mp4` only |
| 10 | Programme lifecycle | A `BugHuntProgram` row with `startsAt`/`endsAt`, a budget, a country allowlist and its own reward matrix. `draft` → `open` → `closed`. Closing stops intake and nothing else — redemption and withdrawal of an already-earned balance stay open permanently |
| 11 | Audit | Every admin mutation writes an `AuditLog` row through `log_admin_action`, same as the rest of `/api/v1/admin` |
| 12 | **Multi-season** | **Seasons recur; Season 2 is confirmed.** Every season-varying value lives on `BugHuntProgram` — dates, budget, per-participant cap, country allowlist, reward matrix. Opening a season is a `POST /admin/bug-hunt/programs` and a status change, with no deploy. The **wallet is per user**: one balance, one payout account, one withdrawal queue across all seasons (§6.1) |
| 13 | Participation carries forward | An approved participant from a previous season is **seeded into the next as `approved`**, in bulk, at programme creation. A proven reporter does not re-audition. Newcomers still apply with a first submission; `suspended` participants do not carry |
| 14 | Naming and home | The programme brand is **Bug Hunt**; the surface is `issues.maigie.com` and is permanent. Copy refers to "Season *n*", and no URL, page title or email carries a season number |

### 3.1 Decisions that still need a call (blocking Phase 0 sign-off)

| # | Question | Recommendation |
|---|----------|----------------|
| ~~A~~ | ~~Reward amounts~~ | **Resolved 2026-09-12.** Critical ₦2,000 · high ₦1,500 · medium ₦1,000 · low ₦500 · high-value feedback ₦1,500 · standard feedback ₦500 (§5.2). Season budget ₦300,000 and per-participant cap ₦15,000 derived from them and still worth a glance |
| B | Pass-over-cash uplift | **Yes, 25%.** A participant redeeming a pass spends 80 kobo of balance per ₦1 of pass price. It costs us COGS rather than cash and it seeds Plus usage. At the signed-off amounts this makes one high-severity bug (₦1,500) buy a 7-day pass (₦1,200 charged) with change — a legible, quotable offer. Implemented as one config constant so it can be turned off |
| C | Minimum age | 18+, self-declared at application, in the terms. We have no verification and should not pretend to |
| D | Whether staff (non-super) can triage-and-award | **Yes**, because the amount is not theirs to choose (Decision 6) and a two-click money flow across a 14-day programme will not be used. Adjustments stay super-admin |
| E | Can a rejected applicant reapply | Once, after 48 hours. `attemptCount` capped at 2 |
| ~~F~~ | ~~Mobile-platform bonus~~ | **Dropped.** A flat ₦500 would double a low-severity award for choosing a platform (§5.2) |
| G | Do we publish accepted findings | No for Season 1 |
| H | Which domain | `bug-hunt.maigie.com` or `issues.maigie.com` (Decision 1b) |

## 4. Product flow

### 4.1 Participant

1. Lands on the programme site (Decision 1b) — what the programme is, what pays what, dates, rules, "Start with your first bug". Either directly, or from the section and footer link on `maigie-public` (§8.2).
2. Signs in with their Maigie account, or creates one (email + OTP, the existing flow). If `country` is unset, they are asked; if it is outside the season's allowlist, they see a plain "this season is open to Nigeria only" state and nothing else. **A returning participant skips to step 5**, pausing only to re-accept the season's terms.
3. Fills the application form: platform (web / Android / iOS), app version, device and OS, title, steps to reproduce, expected, actual, up to 3 attachments, and the terms acknowledgement.
4. Sees `pending`. One screen, honest about the review window (target: 48 hours).
5. On approval: the dashboard. Submissions with status and award, a "New submission" form, and a wallet.
6. On rejection: the reason, and one reapplication path (Decision E).
7. Wallet: balance, lifetime earned, ledger history. Two actions — **redeem a Plus pass** (appears in their Maigie app immediately, unactivated) or **request cash** (bank details, minimum ₦1,000, one open request at a time). The withdrawal then tracks Requested → Approved → **Paid** on this same screen, so nobody has to email us to ask where their money is.

### 4.2 Staff

1. **Applications** queue → read the submission, approve or reject with a reason.
2. **Submissions** queue → set category, type, severity, status; link a duplicate to its canonical, or mark a re-report of an unfixed earlier finding `known_issue`; write a response the participant sees and a private note they do not. Accepting writes the award to the ledger at that season's matrix amount.
3. **Withdrawals** queue (super admin) → approve or reject, pay by hand, record the reference and paid-at. The debit was already held at request time.
4. **Season** page → dates, budget, spend to date, per-platform submission counts, top participants, and the actions that open the next season and carry participants into it (§9).

### 4.3 Notification points

Email is the floor; in-app notification for the pass credit is the upside. Adding an in-app type means an entry in the notifications taxonomy (`src/domains/notifications/taxonomy.py`) — a real dependency, not a free call, so Phase 7 scopes email first.

- Application decided (approved / rejected, with reason).
- **A new season is open** — to previous seasons' approved participants and to anyone holding a balance. This is the highest-value send in the programme and the reason participation carries forward: a warm list of proven reporters beats a fresh recruitment push.
- Submission triaged and accepted, with the amount.
- Submission rejected or marked duplicate, with the reason.
- Withdrawal approved, and again when paid with the reference.
- Programme closing in 48 hours (one send, to approved participants).

## 5. Reward model

### 5.1 Categorisation

Every submission carries a `category` and, for bugs, a `severity`. Both are set by staff at triage, never by the submitter — the submitter's own guess is captured separately as `reportedSeverity` so we can see how well they calibrate, and so a good reporter can be recognised later.

| category | `type` values |
|----------|---------------|
| `bug` | `crash`, `functional`, `data_loss`, `security`, `performance`, `ui` |
| `feedback` | `usability`, `copy`, `suggestion` |

Severity definitions, written down because "high" is otherwise whatever the triager felt that morning:

| severity | Means |
|----------|-------|
| `critical` | Data loss, security exposure, payment or entitlement incorrectness, or a crash on a core path with no workaround |
| `high` | A core flow (auth, Learn, Ask, Prepare, Reflect, checkout) is broken or produces wrong output; workaround is non-obvious |
| `medium` | A non-core feature is broken, or a core flow is degraded with a workaround |
| `low` | Cosmetic, copy, or an edge case a real learner is unlikely to hit |

**`known_issue` exists because seasons recur.** A bug found in Season 1 that we never fixed will be found again in Season 2, and calling that a `duplicate` blames the reporter for our backlog. `duplicate` means "someone else already reported this **in this season**"; `known_issue` means "this was reported in an earlier season and is still open". Both pay ₦0, but the participant-facing copy is different, and the second one is an admission rather than a rejection. `duplicateOfId` may point at a submission from any season — that is how a triager marks it.

### 5.2 Reward matrix — **signed off 2026-09-12**

| Category | Severity / tier | Award | kobo |
|----------|-----------------|-------|------|
| `bug` | `critical` | ₦2,000 | `200_000` |
| `bug` | `high` | ₦1,500 | `150_000` |
| `bug` | `medium` | ₦1,000 | `100_000` |
| `bug` | `low` | ₦500 | `50_000` |
| `feedback` | high-value (acted on) | ₦1,500 | `150_000` |
| `feedback` | standard | ₦500 | `50_000` |
| either | `duplicate`, `invalid`, `wont_fix`, `known_issue` | ₦0 | `0` |

**These are Season 1's amounts, and they live on Season 1's programme row** (Decision 6, §6). A module constant supplies the default when a season is created; after that the row is authoritative. Season 2 may pay differently, and when it does, the closed Season 1 page must still show what Season 1 paid — a matrix in code cannot do that, because changing it rewrites history on every closed season at once.

Guards:

- **Per-participant season cap:** ₦15,000 — roughly 7 criticals or 30 standard findings, which no honest participant reaches in 14 days. Reached, further accepted submissions are recorded and credited ₦0 with a stated reason, because the finding still has value even when the budget line does not.
- **Season budget:** ₦300,000. Held on `BugHuntProgram.budgetKobo`, checked against `awardedKobo` **inside the award transaction**. A budget that is only in a spreadsheet is not a budget.

**What this ladder implies, stated plainly.** The spread between a critical and a low is 4×, and the absolute numbers are small — a full afternoon of careful testing that surfaces three medium bugs pays ₦3,000. That is a real risk to submission *quality*: the rational response to a ₦500 floor is volume, and volume is what the 10-per-day rate limit and the ₦0 duplicate/invalid outcome exist to absorb. The lever if quality disappoints in week one is the high-value feedback tier and `adjustment`, not a mid-season rewrite of the matrix — changing published amounts mid-programme is the fastest way to lose the cohort.

The counterweight is redemption: ₦1,500 for one good bug buys a 7-day Plus pass outright (§5.3). For a student the pass is worth more than the cash, which is the offer the landing page should lead with.

Note that the previous draft's flat ₦500 mobile bonus is **dropped**. At a ₦500 low-bug floor it would have doubled the award for choosing a platform rather than for finding anything, which is not what we want to pay for. Mobile coverage is instead encouraged in the copy and watched in the per-platform stats (§13); if Android and iOS stay under-reported after a few days, the answer is a targeted email to approved participants, not a standing multiplier.

### 5.3 Redemption

**Plus pass.** Priced from config in kobo — `PRICE_NGN_PLUS_PASS_5H` (₦700), `PRICE_NGN_PLUS_PASS_7D` (₦1,500), `PRICE_NGN_PLUS_PASS_TERM` (₦7,200) — read at redemption time via a helper rather than duplicated, so a price change does not leave the programme selling at a stale figure. With Decision B's 25% uplift the balance charged is 80% of price: ₦560 / ₦1,200 / ₦5,760.

Order of operations, and it matters: debit the ledger first, then `pass_service.grant`. If `grant` raises, write a compensating `pass_redemption_reversal` entry. Granting first and failing to debit hands out free passes; there is no version of this where the grant is the first irreversible step.

**Cash.** Minimum **₦1,000** per request — lowered from ₦2,000 now that a critical bug pays ₦2,000; a ₦2,000 floor would have put cash out of reach of anyone with fewer than two good findings and made "or redeem cash" a claim the product mostly refuses. The debit is written when the request is created — the balance must not be spendable twice while a request is open — and a rejection writes a compensating credit rather than deleting the debit.

**Cash is moved by hand, and the dashboard is what tells the participant.** There is no transfer API in this system (§2.2). The full sequence:

1. Participant submits a request. Ledger debit written, status `requested`, and their dashboard shows **Requested**.
2. Super admin approves in `/bug-hunt/withdrawals`. Status `approved`; the dashboard shows **Approved — payment on the way**. This step also reveals the full account number, in that one view, audited.
3. The admin makes the transfer from the business bank account.
4. The admin returns and marks it paid with the bank's transaction reference. Status `paid`, `paidAt` stamped, and the participant's dashboard row flips to **Paid**, carrying the reference so the two sides can reconcile without a support thread.
5. The payout is mirrored into `finance` as an expense.

Marking paid is therefore a **record of a transfer that already happened**, not a trigger for one. The UI must not imply otherwise: the button is "Record payment", not "Pay". If a transfer fails at the bank, the withdrawal goes back to `approved` and nothing in the ledger moves, because nothing left the account.

## 6. Data model

New tables, all in `apps/backend/src/domains/bug_hunt/db_models.py`, created by **migration `083_bug_hunt`**. PascalCase tables and camelCase columns, mapped to snake_case attributes, matching the rest of this schema. `alembic/env.py` needs the new `db_models` import or autogenerate will not see them.

Two groups, and the split is Decision 12: **season-scoped** tables carry a `programId`, **wallet** tables carry a `userId` and no programme at all.

**Season-scoped**

| Table | Purpose | Notable columns and constraints |
|-------|---------|--------------------------------|
| `BugHuntProgram` | One season, and **everything that varies between seasons** | `slug` unique, `seasonNumber` unique, `name`, `status` (`draft`/`open`/`closed`), `startsAt`, `endsAt`, `budgetKobo`, `awardedKobo`, `perParticipantCapKobo`, `countryAllowlist text[] default '{NG}'`, `rewardMatrix jsonb`, `minWithdrawalKobo`, `passUpliftPercent`, `rulesVersion`. If a value would differ between Season 1 and Season 2, it belongs here — that is the whole test |
| `BugHuntParticipant` | A person **in a season** | `programId`, `userId` → `User.id` `ON DELETE CASCADE`, `status` (`pending`/`approved`/`rejected`/`suspended`), `attemptCount`, `carriedFromProgramId` (set when seeded per Decision 13), `decidedByUserId`, `decidedAt`, `rejectionReason`. **Unique `(programId, userId)`** — one participation per season, several across seasons |
| `BugHuntSubmission` | A bug or piece of feedback | `programId`, `participantId`, `userId` (denormalised, so a wallet query never has to walk participations), `platform` (`web`/`android`/`ios`), `appVersion`, `buildNumber`, `deviceModel`, `osVersion`, `route`, `title`, `stepsToReproduce`, `expectedResult`, `actualResult`, `reportedSeverity`, `category`, `type`, `severity`, `status` (`submitted`/`in_review`/`accepted`/`rejected`/`duplicate`/`known_issue`), `duplicateOfId` self-FK **not constrained to the same programme** (§5.1), `publicResponse`, `adminNotes`, `isApplication`, `triagedByUserId`, `triagedAt` |
| `BugHuntAttachment` | Screenshot or recording | `submissionId` `ON DELETE CASCADE`, `url`, `contentType`, `sizeBytes` |

**Wallet — per user, spanning every season**

| Table | Purpose | Notable columns and constraints |
|-------|---------|--------------------------------|
| `BugHuntWallet` | The lock target and the identity of a balance | `userId` **unique** → `User.id`. Holds no balance. Exists so a debit has one row to `SELECT … FOR UPDATE`, rather than locking a participation that a cross-season debit does not belong to |
| `BugHuntLedgerEntry` | **The money.** Append-only | `walletId`, `userId`, **nullable `programId` and `participantId`** (set on `award`, null on a wallet-level debit), `kind` (`award`/`adjustment`/`pass_redemption`/`pass_redemption_reversal`/`withdrawal`/`withdrawal_reversal`), signed `amountKobo`, nullable `submissionId` / `withdrawalId` / `passId`, `note`, `createdByUserId` (null = system). CHECK: credits positive, debits negative, `amountKobo <> 0`. **Unique `(submissionId, kind)` where `kind = 'award'`** so one submission cannot be awarded twice |
| `BugHuntPayoutAccount` | Nigerian bank details | `userId` **unique**, `bankCode`, `bankName`, `accountNumberEnc`, `accountNumberLast4`, `accountName`, `verifiedAt`. Entered once, reused every season. Full number never returned by any endpoint (§10) |
| `BugHuntWithdrawal` | A cash request | `walletId`, `userId`, `amountKobo > 0`, `status` (`requested`/`approved`/`paid`/`rejected`), `payoutAccountId`, `providerReference`, `decidedByUserId`, `decidedAt`, `paidAt`, `rejectionReason`, `financeEntryId`. **Partial unique index on `userId` where `status IN ('requested','approved')`** — one open request at a time, enforced by the database rather than by a check-then-insert |

### 6.1 Why the wallet is not season-scoped

The obvious model keys the ledger on `participantId`, and it breaks the first time Season 2 opens. A participant who earns ₦900 in Season 1 and does not withdraw it — likely, given a ₦1,000 minimum — either has that money stranded in a closed season, or ends up with two balances, two minimums to clear, and two withdrawal queues. Neither is defensible to the person who is owed the money, and merging them later is a migration over exactly the rows we least want to migrate.

So: earning is season-scoped, holding and spending are not. Consequences worth stating, because each is a place the naive model and this one diverge:

- The **per-participant cap** is per season, computed from `award` entries filtered to that `programId`. That still works — the entries carry the programme.
- The **season budget** is `BugHuntProgram.awardedKobo` against `budgetKobo`, also from programme-scoped credits only. A withdrawal never touches a season's budget figure, because paying out is not awarding.
- **Balance and history are lifetime.** The wallet screen groups awards by season so the ledger reads as a history rather than a heap.
- Debits lock `BugHuntWallet`, and carry `programId = NULL` — a redemption is not attributable to a season and pretending otherwise would double-count against a budget.

`BugHuntWallet` holds no columns beyond identity, which looks pointless until you need something to lock. Locking a participation row for a debit that spans seasons is a lock on the wrong thing; locking `User` reaches outside the domain to serialise unrelated writes.

### 6.2 No mirrors

`BugHuntSubmission` deliberately carries **no** `awardKobo` mirror, and there is no cached balance column. The ledger is the only place an amount lives; list endpoints join it. `points_service` does keep a rebuildable `User.pointsBalance` cache and is explicit that the cache never raises because it can be rewritten from the ledger — that trade is worth it at learner-wide read volume and is not worth it here, where a season has tens of participants and one wallet screen.

**`points_service` is the model for this whole layer**, not just an analogy: `PointsLedgerEntry` with `grant` / `balance` / `history` / `redeem`, FIFO consumption, expiry entries written rather than rows mutated, and `POINTS_COST` mapping a pass product to a price. Read it before writing `ledger_service.py`. The differences are that kobo do not expire, and that this ledger has a second debit rail (cash) which points does not.

**One deliberate exception: `BugHuntProgram.awardedKobo`.** It is a mirror of `SUM(amountKobo)` over that programme's `award` and `adjustment` credits, and it exists because the budget check happens *inside* the award transaction, where a full aggregate over a growing table is the wrong thing to do on every triage. It is incremented in the same transaction as the entry it counts, so it cannot drift by one, and it is recomputable from the ledger if it ever does. That is the same reasoning `points_service` gives for `User.pointsBalance`. Every *other* figure is derived.

The existing `Feedback` table is left alone. It is the in-app feedback channel with its own admin pages, it has no participant, no money and naive timestamps, and overloading it would put programme rewards next to anonymous feedback rows.

## 7. API contract

### 7.1 Public — `/api/v1/bug-hunt`, mounted from `bug_hunt.routes:router`

- `GET /program` — **unauthenticated.** The **current** season: number, name, status, dates, reward table, per-participant cap, minimum withdrawal, rules version. All from the programme row, so the page cannot advertise a table the server disagrees with. Between seasons it returns the next `draft` season with its dates if one exists, and an explicit closed state if not — "no season is open right now, the next starts on …" is a state the landing page must render rather than crash on.
- `GET /seasons` — **unauthenticated.** Past seasons with their dates and their own reward tables, so a closed season still shows what it paid.
- `GET /me` — `CurrentUser`. Eligibility (`country` against the current season's allowlist), participation in the current season, lifetime participation list, cap remaining this season, wallet summary. The single call the app boots on.
- `POST /applications` — creates participant (`pending`) + submission (`isApplication = true`) in one transaction, against the current open season. Refused with a named code when the caller is already `approved` this season, including by carry-forward — a returning participant goes straight to `/submit` and must never be shown an application form.
- `POST /submissions` — approved participants only, current season `open` only.
- `POST /submissions/{id}/attachments` — multipart, own submissions only, limits per Decision 9.
- `GET /submissions` — paginated, own only, filters `status` / `platform` / `programId`, defaulting to the current season.
- `GET /submissions/{id}` — own only, any season. 404 rather than 403 for someone else's.
- `GET /wallet` — **lifetime, not per season.** `balanceKobo`, `lifetimeAwardedKobo`, `openWithdrawalKobo`, `capRemainingKobo` (current season), `earnedThisSeasonKobo`.
- `GET /wallet/ledger` — paginated entries across every season, each carrying its season for grouping.
- `GET /wallet/redemption-options` — pass products with the charged balance, from config and the current season's uplift. **Available whether or not a season is open** — an earned balance is spendable in the gap between seasons.
- `POST /wallet/redeem-pass` — `{ productId }`.
- `GET /banks` — Nigerian bank list for the picker, proxied and cached 24 h.
- `GET|PUT /payout-account`.
- `GET /withdrawals`, `POST /withdrawals` — `{ amountKobo }`. The list carries `status`, `paidAt` and `providerReference`, which is what turns the dashboard row into **Paid** with something the participant can check against their own bank alert. Never the account number, only `accountNumberLast4`.

### 7.2 Admin — `/api/v1/admin/bug-hunt`, from `bug_hunt.routes:admin_router`

`StaffUser` for reads and triage; `SuperAdminUser` for anything that moves money or changes the programme.

- `GET /programs`, `GET /programs/{id}`, `POST /programs` *(super)*, `PATCH /programs/{id}` *(super)*. `POST` takes dates, budget, cap, country allowlist, reward matrix and uplift, defaulted from the previous season so opening Season 2 is a form with the last season's numbers already in it
- `POST /programs/{id}/open` and `/close` *(super)* — the status transitions, separate from `PATCH` so opening a season is a deliberate act with its own audit entry. `open` refuses if another season is open, if the matrix is empty, or if the budget is zero
- `POST /programs/{id}/carry-forward` *(super)* — `{ fromProgramId }`. Bulk-seeds the previous season's `approved` participants as `approved` here, skipping `suspended` ones and anyone already present. Idempotent, and reports how many it added (Decision 13)
- `GET /stats?programId=` — queue depths, spend against budget, per-platform counts, acceptance rate. Defaults to the current season; a `programId` gives a closed season's final figures
- `GET /participants?programId=`, `GET /participants/{id}` — the detail view shows **every season this person has taken part in**, their lifetime earnings and their triage history, because "is this a good reporter" is the question a returning applicant raises and it is not answerable from one season
- `POST /participants/{id}/decision` — `{ decision, reason }`
- `POST /participants/{id}/suspend` *(super)* — suspends this season's participation and blocks carry-forward into the next. Does **not** confiscate an earned balance
- `POST /participants/{id}/adjustment` *(super)* — `{ amountKobo, note }`, the only free-typed amount in the system. Written against the wallet, attributed to the season it was granted in
- `GET /submissions?programId=&status=&platform=&severity=`, `GET /submissions/{id}`
- `POST /submissions/{id}/triage` — `{ status, category, type, severity, duplicateOfId, publicResponse, adminNotes }`. Idempotent on the award via the unique index. Awards at **the submission's own season's** matrix, not the current one, so a late triage after Season 2 opened still pays Season 1 rates
- `GET /known-issues?platform=` — the open accepted findings from previous seasons, so a triager can mark `known_issue` without searching by hand. This is the endpoint that keeps the recurring-season fairness rule (§5.1) practical rather than aspirational
- `GET /withdrawals`, `GET /withdrawals/{id}` *(super — the only place the full account number is returned)*
- `POST /withdrawals/{id}/decision` *(super)* — `{ decision, reason }`
- `POST /withdrawals/{id}/mark-paid` *(super)* — `{ providerReference, paidAt? }`. Records a transfer already made; `providerReference` is required, because a payout with no reference cannot be reconciled against a bank statement
- `POST /withdrawals/{id}/revert-to-approved` *(super)* — for a transfer that failed at the bank. Moves no money and touches no ledger entry

Wire contract is camelCase, built field by field, per the convention in `admin/routes.py:_user_response`.

## 8. Participant web app — `maigie-client/apps/bughunt`

New Nx app, `apps/admin` as the template. `vite.config.mts` with `root: __dirname`, `cacheDir: ../../node_modules/.vite/apps/bughunt`, ports `4203`, `build.outDir: ../../dist/apps/bughunt`, plugins `[react(), nxViteTsPaths(), nxCopyAssetsPlugin(['*.md'])]`; empty `targets` in `project.json` and let the Nx Vite plugin infer. `"apps/bughunt"` added to the root `workspaces` array — this repo lists apps explicitly rather than globbing.

Copy `src/lib/apiClient.ts` from `apps/admin` with its **own** persist key `bughunt-auth-storage`, so signing out of the bug hunt app does not sign the learner out of `app.maigie.com`. `VITE_API_BASE_URL` per `env.example`, including the `/api/v1` suffix.

Routes:

| Route | Gate | Contents |
|-------|------|----------|
| `/` | public | Landing: what the programme is, reward table from `GET /program`, dates, rules, CTA |
| `/login`, `/signup`, `/verify` | public | Existing identity endpoints; screens lifted from `apps/web/src/features/auth` |
| `/apply` | signed in, eligible, not already approved | Application form = first submission |
| `/pending`, `/rejected` | signed in | Status states |
| `/dashboard` | approved this season, or any past season | Current season's submissions with status and award, plus a season switcher once more than one exists |
| `/submit` | approved, season `open` | New submission form |
| `/submissions/:id` | own submission, any season | Detail, staff response, attachments |
| `/wallet` | any past or present participant | **Lifetime** balance, ledger grouped by season, redeem pass, request cash, withdrawal history with live status. Reachable between seasons |
| `/seasons` | public | Past seasons and what each paid |
| `/rules` | public | Full terms, for the current season's `rulesVersion` |
| `/not-eligible` | signed in | Country state, against the current season's allowlist |
| `/closed` | public | Between seasons: what the programme is, when the next one opens, and a link to the wallet for anyone holding a balance |

Tailwind with the brand tokens already in `apps/web/tailwind.config.js` — the same `#7568ed` primary, so the programme does not look like a third-party site asking for bank details.

### 8.1 The landing page

It lives here, at `/`, and it is the only full description of the programme. It renders the reward table, dates and status from `GET /program`, so the amounts on screen are the amounts the server will pay — a copy-pasted table is a promise nobody is enforcing.

Sections: what the programme is · what pays what (from the API) · the three surfaces to test, with store links and the web URL · how registration works (your first bug *is* the application) · how you get paid, both rails, with the "one good bug = a week of Plus" line · who can take part and the season dates · CTA.

**It has three states, and the between-seasons one is not an afterthought.** A season is `open` (apply or submit), a season is announced but not yet started (dates, and an email capture for the opening notice), or nothing is scheduled (what the programme is, and the wallet link for anyone still holding a balance). Getting this wrong is how a returning participant who followed a link from a friend's WhatsApp lands on a dead page, and it is the most likely single failure between Season 1 closing and Season 2 opening. The season number is copy pulled from the API — never a hardcoded string, and never in the URL.

### 8.2 `maigie-public` touchpoint

A short section plus a footer link, and nothing more.

- A compact section with three lines and a link out. **Shipped on the landing page (`/`) rather than `/careers`, which is a change from this section's original call.** The reasoning that put it on `/careers` — paid work with Maigie — describes the section accurately and describes the audience wrongly: nobody reaches a careers page looking for an evening's testing, and the people who would take part are students already on the homepage. It sits at position 11, below pricing, download, community and the educator invite, so a visitor working out what Maigie is is never offered a side job first. Static Astro markup like the educator section beside it, with `data-track="landing_cta_bug_hunt"` picked up by the existing delegated click handler.
- A footer entry, in the existing `Company` column alongside Careers.
- No reward table, no rules, no dates and **no season number** on `maigie-public`. Those live in one place and are fetched from the API. A second copy is a second thing to correct every season, and the season it will be wrong about is Season 2.
- The link target is `issues.maigie.com`, referenced from `src/config/apps.ts` rather than inlined twice, matching how the store URLs are already centralised there.
- Written to survive being ignored: the section says the programme runs in seasons and links out, so it needs no edit when a season opens or closes. `maigie-public` deploys on its own cadence and nobody will remember to touch it on the day Season 2 opens.

## 9. Admin dashboard surface

In `maigie-client/apps/admin`, following the two-edit pattern: pages under `src/features/admin/pages/`, methods appended to `src/features/admin/services/adminApi.ts`, types to `src/features/admin/types/`, routes in `src/app/app.tsx` wrapped `AdminRoute > AdminLayout > [SuperAdminRoute] > Page`, and a new `navGroups` group in `AdminLayout.tsx`.

| Page | Route | Gate |
|------|-------|------|
| `AdminBugHuntOverviewPage` | `/bug-hunt` | staff |
| `AdminBugHuntSeasonsPage` | `/bug-hunt/seasons` | staff (mutations super) |
| `AdminBugHuntSeasonEditorPage` | `/bug-hunt/seasons/new`, `/bug-hunt/seasons/:id` | **super only** |
| `AdminBugHuntApplicationsPage` | `/bug-hunt/applications` | staff |
| `AdminBugHuntSubmissionsPage` | `/bug-hunt/submissions` | staff |
| `AdminBugHuntSubmissionDetailPage` | `/bug-hunt/submissions/:id` | staff |
| `AdminBugHuntParticipantsPage` | `/bug-hunt/participants` | staff |
| `AdminBugHuntWithdrawalsPage` | `/bug-hunt/withdrawals` | **super only** |
| `AdminBugHuntWithdrawalDetailPage` | `/bug-hunt/withdrawals/:id` | **super only** |

Nav group `Bug Hunt`, icon `Bug` from `lucide-react`, with `superOnly: true` on Seasons and Withdrawals.

**The season editor is what makes Season 2 a form rather than a release.** It creates a programme from the previous season's values — dates, budget, cap, country allowlist, reward matrix, uplift — with every field editable, then opens it, then offers the carry-forward action with a count of who would be seeded. If any part of opening a season requires an engineer, this page is not finished. Every queue on the other pages carries a season filter defaulting to the current one, so triage never silently spans two seasons.

The withdrawal detail page is the payout console, and it is built for the person doing the transfer with a banking app open beside them: amount in naira, bank name, account name, full account number with a copy button, and the participant's payout history so a first-time payee is visibly a first-time payee. Then one field for the bank reference and a **Record payment** button. The wording matters — this records a transfer that has already happened (§5.3), and a button labelled "Pay" invites someone to click it first and transfer afterwards, which is how a participant ends up marked paid with no money.

## 10. Eligibility, anti-abuse, PII

- **Nigeria** is checked on the server on every write, not once at application. A country change after approval suspends nothing automatically but is surfaced in the admin view.
- **One participant per user per programme**, by unique index. Multi-accounting is caught at triage — the same finding from two accounts is a duplicate, and duplicates pay nothing.
- **Submission rate limit:** 10 per participant per day. Volume is not the metric; accepted findings are.
- **Attachments** are validated on content type and size, stored under the participant's prefix, and never served from a path a participant can guess their way across.
- **Bank details are PII and get treated as such.** `accountNumberEnc` encrypted at rest, `accountNumberLast4` for display, the full number returned by **no** endpoint — the person paying reads it from the withdrawal detail view, which is super-admin and audited. Changing the payout account holds new withdrawals for 24 hours.
- **Security findings** (`type = 'security'`) are visible only to staff, never quoted in an email, and route to private handling.
- **Terms** are versioned (`BugHuntProgram.rulesVersion`) and **re-accepted each season**, including by carried-forward participants — the amounts, dates and possibly the country scope differ, so consent from Season 1 is not consent to Season 2. A returning participant sees a one-screen acknowledgement, not a fresh application. Terms cover: 18+, eligible country, rewards at Maigie's discretion against the published matrix for that season, no Maigie staff or contractors, findings and their contents assigned to Maigie, participant responsible for their own tax, a season may close early, and **balances earned survive both the season's closure and the gap before the next one**.
- **Retention:** submissions are kept — they are engineering records, and cross-season `known_issue` marking depends on them. Payout accounts are kept while the holder has any balance or any participation in an open or scheduled season, and deleted 90 days after the last paid withdrawal once neither is true. The naive "90 days after last payout" rule would delete the bank details of every returning participant during the gap between seasons and make them re-enter them, which is both worse privacy theatre and worse product.

---

## Phase 0 — Decisions and programme setup

- [x] Reward matrix signed off — **critical ₦2,000 · high ₦1,500 · medium ₦1,000 · low ₦500 · high-value feedback ₦1,500 · standard feedback ₦500, 2026-09-12 (§5.2).**
- [x] Domain settled — **`issues.maigie.com`, 2026-09-12, decided by Season 2 being confirmed (Decision 1b).**
- [ ] Confirm the Season 1 budget (₦300,000) and per-participant cap (₦15,000) derived from those amounts
- [ ] Sign off the pass-over-cash uplift (Decision B)
- [ ] Write the rules and terms copy, including tax and IP language (§10), as `rulesVersion` 1
- [ ] Confirm the funding source and who executes manual payouts
- [ ] Decide the Season 1 window and the review-turnaround promise the landing page makes
- [ ] Sketch the Season 2 window now, even loosely. It changes nothing in code but it decides what the between-seasons landing state says, and that state ships in Phase 7
- [ ] Agree the recruitment channel: `maigie-public` link, blog post, existing-learner email, or all three
- [ ] Name the triage owner. A 14-day programme with a 48-hour promise needs one person accountable for the queue

## Phase 1 — Backend foundation

- [x] `src/domains/bug_hunt/` package — **`db_models.py`, `models.py`, `routes.py`, `rewards.py`, `exceptions.py`, `services/{program_service,eligibility_service}.py`, 2026-09-12.**
- [x] Eight tables per §6, migration `083_bug_hunt` — **`programId` on every season-scoped table, `userId` on every wallet table, 34 CHECK constraints and **three** partial unique indexes (one open season, one award per submission, one open withdrawal per tester). Applied and downgraded cleanly against a scratch Postgres, and all 25 invariants exercised by hand: every violation refused, every legitimate operation allowed. 2026-09-12.**
- [x] Add the `db_models` import to `alembic/env.py` — **done; asserted by a test, since without it autogenerate silently drifts from the models.**
- [x] `services/program_service.py` — **season resolution (`current`/`next_scheduled`/`latest_any`), `require_open` with a named refusal, `create` defaulting from the previous season, `edit` (closed seasons immutable), `open_season`/`close_season` with preconditions, `carry_forward` + preview, participation lookups. 2026-09-12.**
- [x] `services/eligibility_service.py` — **country against the current season's allowlist, participation status, terms version; `require_participant` composes all four checks and returns the season and the participation so a caller cannot act on a different season than it was authorised against. 2026-09-12.**
- [x] `GET /program`, `GET /seasons` (both unauthenticated) and `GET /me` — **`/program` answers all three landing states with an explicit `state` discriminator; `/me` is one round trip covering season, eligibility, participation, cross-season history and a `wallet` field declared null until Phase 4. 2026-09-12.**
- [x] Season admin — **`GET /seasons`, `GET /seasons/defaults`, `GET /seasons/{id}`, `POST /seasons`, `PATCH /seasons/{id}`, `POST /seasons/{id}/open|close`, `GET|POST /seasons/{id}/carry-forward`. Staff read, super admin for anything that sets a budget or moves a season; every mutation writes an `AuditLog` row. 2026-09-12.**
- [x] Tests — **157 tests across `test_bug_hunt_{rewards,schema,routes_mounted,eligibility}.py`, all passing with no database. Full backend suite 5 028 passed / 203 skipped, Ruff and targeted mypy clean. Two real defects found and fixed by the verification rather than by review: a `CASCADE` that erased **paid** payout records when an account was deleted (now `SET NULL`, because real money left the business account and has to stay accountable), and camelCase attribute reads flagged by `test_orm_attribute_names`, which also removed an N+1 in `/me`. 2026-09-12.**
- [x] Behavioural tests for the season lifecycle against a live database — **`tests/test_bug_hunt_lifecycle.py`, 74 tests, run green and repeatably. The alembic chain cannot build a database from base (it assumes the Prisma-era `User` baseline), so the harness builds the schema from SQLAlchemy metadata instead — 131 tables — which is what made local `RUN_DB_TESTS=1` possible at all. Covers the full Season 1 → Season 2 handover through the admin API over HTTP, and closes the gap this line was opened for. 2026-09-12.**
- [ ] Mount `router` at `/api/v1/bug-hunt` and `admin_router` at `/api/v1/admin/bug-hunt` in `app.py:_register_domains`, plus tag descriptions in `_openapi_tags`
- [ ] Mount smoke test, in the style of `tests/test_billing_routes_mounted.py`

## Phase 2 — Application and submission intake

- [x] `POST /applications` — **participant + application submission in one transaction; refused for `approved`/`pending`/`suspended`; a rejected applicant retries once after a 48-hour cooldown, reusing the participation row so the unique `(programId, userId)` stays the guard. Stale `acceptedRulesVersion` refused, so consent is never recorded against terms the applicant did not read. 2026-09-12.**
- [x] Returning-participant terms re-acceptance — **`POST /terms-acceptance`. Not a route *into* a season: approval is a precondition, so it cannot be used to skip applying. 2026-09-12.**
- [x] `POST /submissions`, `GET /submissions`, `GET /submissions/{id}` — **scoped in the query rather than checked after, so another tester's id answers 404 and not 403. Unfiltered reads span every season, because a tester between seasons still has a history. 2026-09-12.**
- [x] `POST /submissions/{id}/attachments` — **`storage_service.upload_upload_file` at `bug-hunt/{userId}/{submissionId}`, 6 content types, 10 MB, 3 per finding. Authorised by **ownership, not approval**, since the application's own finding belongs to a `pending` applicant. Refused once triaged. 2026-09-12.**
- [x] Per-participant daily submission rate limit — **counted from rows over a rolling 24 hours, not from Redis: it is a published property of the season (`submissionDailyLimit`), and `check_rate_limit` degrades *open*, so a cache blip would silently suspend the rule. Refusal is a 429 naming the retry time. 2026-09-12.**
- [x] Reject writes when the programme is not `open` — **`NO_OPEN_SEASON`, carrying the next season's start date so a refused client can render the between-seasons page without a second request. 2026-09-12.**
- [x] A submitter cannot grade their own finding — **`category`, `severity`, `status`, `publicResponse` and `isApplication` are absent from the request contract and dropped by the field mapper; `reportedSeverity` is captured and never used for money. Tested per field. 2026-09-12.**

## Phase 3 — Triage

- [x] `GET /admin/bug-hunt/participants`, `/participants/{id}`, `POST /participants/{id}/decision`, `/suspend` — **queues ordered oldest-first, because a queue worked newest-first starves whoever has waited longest. Detail spans every season the person has taken part in, with lifetime earnings from the ledger. A rejection without a reason is refused; suspension is super admin and **does not touch the balance**. 2026-09-12.**
- [x] `GET /admin/bug-hunt/submissions`, `/submissions/{id}`, `POST /submissions/{id}/triage` — **`TriageRequest` carries no amount field: a triager sets category and severity, the season's matrix decides the kobo. Accepting a grading the season does not price is **refused** rather than paid as zero. Grading reads the submission's *own* season's matrix, so a late triage pays the rates the finding was reported under. 2026-09-12.**
- [x] `GET /admin/bug-hunt/known-issues` — **prior seasons' accepted findings, so `known_issue` is a dropdown rather than a memory test. Justified in `test_owner_scoped_reads.py`'s allowlist, which is where cross-learner staff reads must declare themselves. 2026-09-12.**
- [x] `GET /admin/bug-hunt/stats?programId=` — **queue depths, spend against budget, per-platform/status/severity counts, acceptance rate and median turnaround. Rates are `null` before anything is decided, never `0`. 2026-09-12.**
- [x] `log_admin_action` on every mutation — **verified over HTTP, not by reading. 2026-09-12.**
- [x] Admin UI: applications queue, submissions queue with filters, submission detail with the triage console and attachment viewer — **`maigie-client/apps/admin`: `bugHuntApi.ts`, `bugHunt.types.ts`, a `money.ts` with the single kobo↔naira conversion, shared status badges, and six pages. The triage console shows what a grading pays **before** it is committed, read from that finding's own season's table. `known_issue` gets its own picker and its own colour, because a triager who cannot tell it from `duplicate` at a glance will reach for the wrong one under pressure. 2026-09-12.**
- [x] Admin UI: season list and season editor per §9, including carry-forward with its preview count — **the editor is pre-filled from `GET /seasons/defaults` rather than from the browser, so there is one rule for what Season *n+1* starts from. A closed season renders read-only. Amounts are entered in naira and converted once. 2026-09-12.**
- [x] Nav group and routes registered per §9 — **its own `Bug Hunt` group rather than an entry under Communications: during a season this is a daily workflow with five surfaces, and burying the triage queue under a heading about email is how a 48-hour promise gets missed. `nx build admin` green, zero new TypeScript errors against a pre-existing 30-error baseline. 2026-09-12.**
- [ ] Notify the reporter when their finding is triaged, and the applicant when they are decided. Phase 8 owns the email; until it lands, a tester learns their outcome by opening the dashboard

## Phase 4 — Reward ledger

- [x] Read `billing/services/points_service.py` first and mirror its shape (§6) — **done, and it turned up a defect worth not copying: `points_service` reads the spendable balance in one session and writes the redemption in another, which is a check-then-act with no lock between the steps. Two concurrent redemptions both succeed. It survives because points are cheap and low-volume; kobo are neither, and this domain has two independent spend rails. 2026-09-12.**
- [x] Wallet — **`ledger_service.get_or_create_wallet`, created lazily on first need rather than at signup, with the unique index making the concurrent-create race benign. 2026-09-12.**
- [x] `services/ledger_service.py` — **`credit`, `debit`, `balance`, `summary`, `history`, `reverse`. Debits take `SELECT … FOR UPDATE` on the wallet row and sum the ledger *inside* that transaction, so the second concurrent debit re-reads a balance that already reflects the first and is refused. Spends carry `programId = NULL`, enforced by a CHECK. A reversal is a new row, never a deletion — a restored balance with no trace of the two events that produced it is a number with no story. 2026-09-12.**
- [x] `services/reward_service.py` — **awards read the matrix from the submission's **own** programme; the per-season cap and the budget are checked under `FOR UPDATE` on the season row, with `awardedKobo` incremented in the same transaction as the entry it counts. 2026-09-12.**
- [x] Award written from `triage` on `accepted`, idempotent via the unique `(submissionId, 'award')` index — **and deliberately in a *second* transaction: a grading is a fact about the finding, an award is a consequence, and doing both in one would let an exhausted budget silently un-accept a real bug. 2026-09-12.**
- [x] `POST /admin/bug-hunt/participants/{id}/adjustment` *(super admin)* — **either sign, mandatory note, counts against the season cap, and a clawback takes the wallet lock so it cannot leave a tester owing us money. 2026-09-12.**
- [x] `GET /wallet`, `GET /wallet/ledger` — **lifetime, reachable between seasons, each entry carrying its season (`null` for a spend). `openWithdrawalKobo` sits beside the balance rather than inside it. 2026-09-12.**
- [x] Tests — **49 in `test_bug_hunt_ledger.py`. The races are run for real with `asyncio.gather` against Postgres rather than asserted in prose: 4 concurrent ₦1,500 redemptions against ₦2,500 leave exactly one success and a balance of ₦1,000; 4 concurrent awards of one finding pay once; 3 concurrent awards against a ₦2,000 budget fit exactly one. A lock you have not raced is a lock you are hoping for. 2026-09-12.**
- [x] **Cross-season tests** — **a Season 1 balance is spendable in Season 2 and in the gap; a spent Season 1 cap does not limit Season 2 earning; a late triage is paid at its own season's rate all the way to the ledger. 2026-09-12.**
- [x] **The cap clamps, the budget refuses** — **two limits, two behaviours, on purpose. A tester ₦500 short of their cap who files a critical gets the ₦500, because the cap is a ceiling on what we pay one person. But paying a tester *less than the published amount because we ran out of money* is the one failure that would damage the programme, so an over-budget award is refused rather than reduced: the finding stays accepted, the money stays owed, and the console links to the season to raise it. 2026-09-12.**
- [ ] Correcting a mis-grade upward cannot pay the difference. An award is written once (that is the property worth having), so the shortfall is stated in the triage response and settled with an adjustment. Acceptable, but it is a manual step somebody has to notice

## Phase 5 — Pass redemption

- [x] `services/redemption_service.py` — **prices read from `PRICE_NGN_PLUS_PASS_*`, the same constants Paystack charges against, so the programme cannot quietly sell a pass at a price the catalogue abandoned. Uplift from the season's `passUpliftPercent`, clamped at 90 so a pass can never be free, falling back to the config default between seasons. Integer arithmetic, floored — a percentage that does not divide cleanly rounds in the tester's favour. 2026-09-12.**
- [x] `GET /wallet/redemption-options`, `POST /wallet/redeem-pass` — **both available with no season open, because an earned balance is permanent and the gap between seasons is most of the year. `affordable` is computed server-side so the wallet never offers a pass it will then refuse. Both the list price and the charged amount are returned: the discount *is* the offer, and "₦1,125 of findings buys a ₦1,500 pass" cannot be written from one number. 2026-09-12.**
- [x] Debit before `pass_service.grant(source="bug_hunt")`; compensating reversal if the grant raises — **debit → grant → annotate. Granting first is the ordering that hands out free passes when the debit is refused. Tested by making the grant fail on purpose rather than by reading the `except` block: the balance comes back whole, and it comes back as a *new row* so the history shows what was attempted. 2026-09-12.**
- [x] Verify the granted pass appears in `GET /api/v1/billing/passes`, unactivated — **verified over HTTP from the tester's own token: `inventoryCount: 1`, `status: inventory`, `source: bug_hunt`, no fabricated `PlusPurchase`, and the **NGN** unit allowance (4 500 on a 7-day, not the global 10 000) — passes are sized by market and inheriting the global total would give away something the product does not sell here. The tester starts the clock themselves; activating on redemption would burn a 5-hour pass at the moment it was bought. 2026-09-12.**
- [x] Tests — **37 in `test_bug_hunt_redemption.py`, including four concurrent attempts at a ₦1,125 pass on a ₦2,000 balance leaving exactly one pass. Also pins the marketing claim: one high-severity finding (₦1,500) buys a 7-day pass (₦1,125), so if a reward or a price moves and that stops being true, the test says so before the landing page does. 2026-09-12.**
- [x] **Found and fixed: a season-attributed adjustment bypassed the budget entirely.** It counted against the tester's cap but never touched `awardedKobo`, so the budget covered awards only — and a super admin, who is also the person able to raise the budget, could spend past it silently through the one endpoint that takes a free-typed amount. Adjustments now take the same lock and the same check an award does, and a clawback returns the budget. Caught by a redemption test that asserted a season's spend and got zero. 2026-09-12.**

## Phase 6 — Cash withdrawals

- [x] `GET|PUT /payout-account` — **keyed on `userId`, entered once, reused every season. AES-GCM at rest with the key derived from `SECRET_KEY` via HKDF under a distinct `info` label — **the same scheme `notifications/subscription_crypto.py` already uses**, which answers the open "where does the key live" question by pointing at the answer this codebase had already given. Ten-digit NUBAN validated and normalised before encryption. Only the last four are ever read back, and `PayoutAccountView` has no field for the full number, which is a stronger guarantee than remembering not to include it. 2026-09-12.**
- [x] 24-hour hold after a change — **on the bank or the number, not on a spelling fix in the account name: that is not a redirection, and holding a payout for a typo correction would be friction with nothing behind it. First entry is not held, because there is no previous account for an attacker to be redirecting money away from. 2026-09-12.**
- [x] `GET /banks` — **Nigerian bank list from Paystack, cached 24 h, and it **degrades to an empty list** rather than erroring. The client turns an empty list into a free-text bank name field: an outage at a third party must not be the reason somebody cannot enter the details they get paid with, and the admin making the transfer reads a bank name either way. Built in Phase 7, when the picker that needed it existed. 2026-09-12.**
- [x] `POST /withdrawals` — **minimum from the season (default ₦1,000), debit written **at request time** so a pending request cannot be spent twice, one open request enforced by the partial unique index and raced for real. The awkward corner is handled and tested: the request row must exist before the ledger entry can name it, so a refused debit deletes the row — leaving it would occupy the one-open-request slot with a request that holds nothing and block every future request for no visible reason. 2026-09-12.**
- [x] `GET /withdrawals` — **status, `paidAt` and `providerReference`, plus the season's minimum and the balance in one call, because the withdrawal form needs all three. Reads only the snapshot fields, never the payout account, so this shape has no path to the ciphertext. 2026-09-12.**
- [x] `POST /admin/bug-hunt/withdrawals/{id}/decision` *(super)* — **approval moves nothing; a refusal writes a compensating credit rather than deleting the debit, so the tester's history shows the request and its refund. A refusal without a reason is refused. 2026-09-12.**
- [x] `POST /admin/bug-hunt/withdrawals/{id}/mark-paid` *(super)* — **reference required, `paidAt` stamped, and **no ledger entry written**: the debit happened at request time, and writing one here would pay the same request twice, once out of the wallet and once out of the bank. There is a test asserting the ledger is byte-identical before and after. 2026-09-12.**
- [x] `POST /admin/bug-hunt/withdrawals/{id}/revert-to-approved` *(super)* — **for a transfer the bank refused after we wrote it down. Asserts no ledger movement. 2026-09-12.**
- [x] Admin withdrawals queue + detail payout console per §9 — **oldest first, the full account number with a copy control in the detail view only, **audited on read** because the disclosure is the sensitive act and the transfer itself happens where we cannot see it. A first-time payee is visibly a first-time payee. The button reads *Record payment*, not *Pay*. An unreadable ciphertext degrades to "ask them to re-enter their details" rather than a 500. 2026-09-12.**
- [x] Tests — **62 in `test_bug_hunt_withdrawals.py`, including the encryption round trip, a wrong key detected rather than returning rubbish, four concurrent requests leaving exactly one open, and the no-orphan-row case. 2026-09-12.**
- [x] Participant-side withdrawal history on the wallet screen — **shipped in Phase 7. All four states rendered, with the bank reference on a paid row so a tester can check it against their own bank alert, and the refusal reason plus "returned to your balance" on a rejected one. 2026-09-12.**
- [x] **Dropped, with the reason recorded: the automatic `finance` mirror.** `finance.routes._resolve_gbp` is explicit that it *never invents an FX rate* — a non-GBP ledger line requires an operator-entered GBP figure, and `amountGbp` is `NOT NULL`. An automatic mirror could only satisfy that by inventing the number the finance domain declines to invent, from inside a `try/except` that swallows its own failures. A wrong number in the books is worse than no line. So `financeEntryId` stays null, the payout console tells the operator exactly what expense line to add and with which reference, and a test asserts the mirror does not exist so that anyone re-adding it reads the reason first. 2026-09-12.**

## Phase 7 — Participant web app

- [x] Scaffold `apps/bughunt` per §8; `nx build bughunt` green — **Vite on port 4203, its own `tailwind.config.js` carrying the `#7568ed` primary from `apps/web`, and its own copy of the API client. The copy is the point: the persist key is `bughunt-auth-storage`, not `auth-storage`, so signing out of the Bug Hunt site does not sign a tester out of the learner app they are being paid to test. `GET /program` and `GET /seasons` are on the client's public-path list, so a stale token cannot trigger a refresh-and-redirect on a marketing page. 2026-09-12.**
- [x] Landing page at `/` per §8.1, reward table and dates rendered from `GET /program` — **every amount, date, cap, daily limit, country and uplift percentage read from the season row. Nothing in the file is a hardcoded figure. 2026-09-12.**
- [x] Auth: login, signup, OTP, country prompt, `not-eligible` state — **signup sends `country: 'NG'` so the eligibility check passes on the first attempt, and `COUNTRY_NOT_SET` gets a one-tap fix rather than a refusal: telling a Nigerian tester they are ineligible because we never asked where they live turns our missing data into their dead end. `COUNTRY_NOT_ELIGIBLE` is a different screen, because the remedy is different. 2026-09-12.**
- [x] Application form with attachment upload and terms acknowledgement — **one shared form component with the ordinary submit page, because the application *is* a submission and two forms would drift into asking different questions. Steps / expected / actual are three required boxes rather than one "describe the bug" textarea: a report we cannot reproduce is a report we cannot pay for, and the separation does a rubric's work without lecturing anybody. `reportedSeverity` is optional and labelled explicitly as an opinion that does not set the amount. Attachments live on the detail page the form lands on, not inside the form, because the server keys uploads by submission id — there is nothing to attach a file to until the finding exists. 2026-09-12.**
- [x] Returning-participant path: terms re-acceptance screen instead of an application form — **one screen showing this season's dates, cap, limit and minimum, with a link to the full rules and the version number the acceptance is recorded against. 2026-09-12.**
- [x] Between-seasons and pre-season landing states, and the `/closed` route (§8.1) — **built now, while unreachable, per the plan. `state` from the API is the discriminator and no screen infers a page from a null. Both states lead with "your balance is still yours and still spendable", because that is the thing somebody is quietly worried about the morning after a season closes. 2026-09-12.**
- [x] `/seasons` page listing past seasons and what each paid — **each season renders its own reward table from its own row, so a closed season keeps showing what it actually paid. A tester who earned ₦2,000 for a critical in Season 1 must never open this page and read that criticals pay ₦1,000. 2026-09-12.**
- [x] `pending` / `rejected` states — **the rejection page states the one retry plainly and shows the triager's reason verbatim; a second attempt with no idea what was wrong wastes their evening and our queue. When both attempts are used it says so and points at the next season rather than leaving a button that will refuse. 2026-09-12.**
- [x] Dashboard: submission list with status and award — **lists **every** season rather than the open one, which is the API's own default: between seasons is exactly when somebody logs in to look at what they earned, and filtering to a season that does not exist would greet them with "no findings yet" after fourteen. Award and status sit on every row, because "did that one pay" is the actual question. No season switcher: with the list already spanning seasons and each row carrying its season number, the control would filter away the thing it was added to reveal. 2026-09-12.**
- [x] New-submission form — **platform-scoped environment fields (build, device, OS for mobile; page for web), because asking a browser user for their build number gets a blank. Not prefilled from the user agent: the tester is reporting a bug on a *different* device from the one they are typing on about half the time, and a wrong prefilled device model is worse for a triager than an empty field. The daily limit is rendered as its own panel, not a red error — hitting it means somebody has been working. 2026-09-12.**
- [x] Submission detail with the staff response — **`publicResponse` only; there is no `adminNotes` on this page because there is none on the wire. The grade is explained rather than displayed: category, grade, and the published amount that grade carries, so a tester who called it critical and got low is checking arithmetic against a table they agreed to rather than absorbing an anonymous decision. `duplicate` and `known_issue` get different colours *and* different copy — one is somebody else's speed, the other is us admitting we knew and had not fixed it. 2026-09-12.**
- [x] Wallet: balance, ledger, pass redemption, withdrawal request with the bank picker, withdrawal history. Reachable with no season open — **auth is the only gate on this page: not participation, not an approved status, not an open season. Every other page routes people away and this one deliberately does not, which is the whole reason the wallet is keyed on `userId`. Passes are listed first with the discount shown as arithmetic (cash price struck through beside the balance cost) rather than as a percentage somebody has to compute. `openWithdrawalKobo` is reported beside the balance and never inside it. The bank form states the 24-hour hold before they type rather than after they are refused, and re-entering the full number is explained by the fact that we cannot show it back. 2026-09-12.**
- [x] Rules page rendering the signed-off terms for the current `rulesVersion` — **cap, minimum, daily limit, dates, countries, uplift and the whole reward table read from the season. Falls back to the most recent season between seasons rather than going blank, because that is exactly when somebody with a dispute about last season wants to read it. Prints the `rulesVersion` their acceptance is recorded against. 2026-09-12.**
- [x] `maigie-public` section + footer link per §8.2, target read from `src/config/apps.ts` — **`BUG_HUNT_URL = 'https://issues.maigie.com'`. Landing page rather than `/careers`; the reason for the change is recorded in §8.2. No amounts, dates or season number on `maigie-public`. 2026-09-12.**
- [x] Accessibility pass — **real `<label>` on every input, hints and errors tied with `aria-describedby`, `role="alert"` and `aria-live="assertive"` on the error notice because a refusal that appears only visually is invisible to a screen reader and every refusal on this surface needs acting on, `aria-pressed` on the filter pills, a global `:focus-visible` ring rather than one per component, and `prefers-reduced-motion` collapsing durations rather than setting `animation: none` — the latter is what leaves elements stuck at zero opacity. Status is never conveyed by colour alone: every pill carries its own words. 2026-09-12.**

## Phase 8 — Launch readiness

- [ ] Programme email notifications for the §4.3 points, including the season-opening send to past participants
- [ ] Recruitment: `maigie-public` inbound link and the announcement
- [ ] Analytics events (§13)
- [ ] Run `083` against staging, create Season 1 **through the admin season editor** rather than by SQL — if that path does not work, multi-season does not work
- [ ] Walk the whole flow on all three surfaces, including a real pass redemption and a real ₦1,000 transfer to one internal tester — bank transfer made, reference recorded, dashboard confirmed showing **Paid**
- [ ] Register `issues.maigie.com`, add the CORS origin to backend settings, deploy
- [ ] Triage rota and the 48-hour promise staffed for 14 days
- [ ] Open Season 1

## Phase 9 — Season 1 wrap

- [ ] Close intake at `endsAt` via `/programs/{id}/close`. Redemption and withdrawal stay open indefinitely (Decision 10) — there is no cutoff to communicate, because the wallet is permanent
- [ ] Clear the triage queue. Late triage pays Season 1 rates by construction, so there is no deadline pressure on fairness
- [ ] Season report: submissions by platform and severity, acceptance rate, spend against budget, cost per accepted finding, cash-vs-pass split, and the one that matters — **how many findings changed the product**
- [ ] Retention sweep per the revised §10 rule (holders of a balance and future participants keep their payout account)
- [ ] Verify the between-seasons landing state is live and correct on the day intake closes

## Phase 10 — Season 2

The point of Decision 12: this phase should contain **no engineering work**. If it does, Phase 1 or 3 was not finished, and the task is to fix that rather than to hand-hold a season.

- [ ] Review Season 1's data and decide Season 2's amounts, budget, cap, dates and country scope
- [ ] Create the season in the admin editor, defaulted from Season 1, and adjust
- [ ] Carry forward Season 1's approved participants; confirm the count before committing
- [ ] Publish `rulesVersion` 2 if anything material changed; confirm returning participants are asked to re-accept
- [ ] Open the season. Announcement email to past participants and anyone holding a balance
- [ ] Reassess Paystack Transfers with two seasons of payout volume in hand — recurring manual transfers are exactly the case that justifies building it, and one season of data is not enough to size it
- [ ] Reassess whether Season 2 stays Nigeria-only (`countryAllowlist` is already an array; widening it is data, not code)

---

## 11. Milestones

Each is independently deployable and independently useful.

| Milestone | Contents | Value if the next never lands |
|-----------|----------|------------------------------|
| **M1 — Intake** | Phases 1–2 + a minimal `apps/bughunt` with landing, auth and the application form | Findings arrive and are stored. Triage and payment could be done by hand from the admin DB |
| **M2 — Triage** | Phase 3 | Staff work a real queue; participants get decisions |
| **M3 — Earning** ✅ | Phase 4 + the dashboard and wallet read views | Participants see what they earned. Payment still manual |
| **M4 — Passes** ✅ | Phase 5 | The cheap reward rail works end to end. Cash could stay manual all season |
| **M5 — Cash** ✅ | Phase 6 | The full promise |
| **M6 — Launch** | Phases 7–8 complete | Season 1 opens |
| **M7 — Season 2** | Phase 10, which should be zero engineering | Proves Decision 12. If M7 needs a deploy, the seasoning work was not done |

Multi-season is **not** a milestone, deliberately. It is a property of M1 through M6 — `programId` on the season-scoped tables, season-varying values on the programme row, a per-user wallet, and the season editor in M2. Making it a later milestone is how it becomes a migration over money.

## 12. Acceptance criteria

**Correctness**

- [ ] A participant's displayed balance equals `SUM(amountKobo)` over their ledger, in every state, including mid-withdrawal
- [ ] No path produces a negative balance; concurrent redemption and withdrawal cannot both spend the same kobo
- [ ] Triaging the same submission twice awards once
- [ ] Rejecting a withdrawal returns exactly the amount held
- [ ] Marking a withdrawal paid changes the participant's dashboard to **Paid** with the bank reference, and moves no ledger entry — the debit was written at request time
- [ ] Every naira figure in the system is an integer number of kobo. No float appears on any money path
- [ ] A pass granted by redemption is indistinguishable from a purchased one in the Maigie app, except for `source`
- [ ] The season budget and the per-participant cap are enforced in the award transaction, not in a report

**Multi-season (Decision 12)**

- [ ] **Season 2 can be created, opened and populated entirely from the admin dashboard**, with no code change, no migration and no redeploy of any frontend. This is the test; the rest are its consequences
- [ ] A balance earned in Season 1 is spendable during Season 2 and during the gap between them
- [ ] A submission triaged after Season 2 opens is awarded at **its own** season's matrix
- [ ] Changing Season 2's reward matrix leaves Season 1's published table unchanged on `/seasons`
- [ ] A carried-forward participant is never shown an application form, and is asked to re-accept the terms
- [ ] The landing page renders correctly with no season open, with a scheduled season, and with none scheduled
- [ ] Widening `countryAllowlist` for a future season requires no code change
- [ ] No season number appears in a URL, a page title, an email subject, or a hardcoded string anywhere in `apps/bughunt` or `maigie-public`

**Access**

- [ ] An account outside the current season's `countryAllowlist` cannot apply or submit, and is told why
- [ ] No participant can read another participant's submission, ledger or payout account
- [ ] Every admin mutation appears in `AuditLog` with the acting staff id
- [ ] Withdrawal approval and payout recording are super-admin only
- [ ] Full bank account numbers appear in exactly one super-admin view and in no list response

**Surfaces**

- [ ] Submission capture works and is tested from web, Android and iOS as the reported platform, with version and device recorded
- [ ] The landing page reward table comes from the API, not from copy in the app, and `maigie-public` carries no second copy of the amounts (§8.2)
- [ ] `nx build bughunt`, `nx build admin`, `maigie-public`'s `npm run build`, and the backend suite are all green

## 13. Analytics

Record before opening, so "the programme worked" is measurable rather than asserted.

Every event and every metric carries `seasonNumber`, so Season 2 is comparable to Season 1 rather than merely following it.

- [ ] `bughunt_landing_view`, `bughunt_apply_start`, `bughunt_apply_submit`
- [ ] `bughunt_submission_create` with `platform`
- [ ] `bughunt_redeem_pass` with `productId`, `bughunt_withdrawal_request`
- [ ] Per-season metrics: applications, approval rate, submissions per approved participant, acceptance rate by platform, **cost per accepted finding**, cash-vs-pass split, median triage turnaround
- [ ] **Season-over-season, which is what recurring seasons buy us:** retention of approved participants into the next season, whether returning reporters produce higher-severity findings than newcomers, whether cost per accepted finding falls as the obvious bugs run out, and how many findings are `known_issue` — a rising `known_issue` rate means we are collecting reports faster than we are fixing them, which is a signal about engineering rather than about testers
- [ ] The one that decides whether there is a Season 3: how many accepted findings actually changed the product

## 14. Reference paths

**Backend** — `maigie/apps/backend`

- `src/app.py` `_register_domains` / `_openapi_tags` — where the two routers mount
- `src/shared/auth/dependencies.py` — `CurrentUser`, `StaffUser`, `SuperAdminUser`
- `src/domains/billing/services/points_service.py` — **the closest existing thing to this ledger.** `grant`/`balance`/`history`/`redeem`, `POINTS_COST`, FIFO spend, expiry as entries. Copy its shape
- `src/domains/billing/services/pass_service.py` — `grant()`, `PASS_PRODUCTS`, `source` precedents (`points`, `admin_comp`)
- `src/domains/billing/routes.py:163` `GET /billing/passes`, `:557` `GET /billing/points` — where a redeemed pass and a wallet already surface
- `src/domains/billing/services/purchase_service.py:configured_store_amount` — the NGN-price-from-config pattern to reuse
- `src/config.py` — `PRICE_NGN_PLUS_PASS_5H` (70 000), `_7D` (150 000), `_TERM` (720 000), all kobo
- `src/domains/admin/routes.py:580` `grant_comp_pass` — the grant + audit precedent
- `src/domains/admin/services/audit_service.py:log_admin_action`
- `src/domains/careers/` — smallest clean domain to copy for structure
- `src/domains/research/routes.py` — the public-router + admin-router split
- `src/domains/identity/routes.py` — signup, OTP, `PUT /users/me/country`
- `src/shared/infrastructure/storage.py` — `storage_service.upload_upload_file`
- `src/domains/finance/` — where a cash payout is recorded as an expense
- `alembic/env.py`, `alembic/versions/082_educator_survey.py` — migration conventions; next is `083`
- `tests/conftest.py` — `client`, `auth_headers`, `RUN_DB_TESTS=1`

**Clients**

- `maigie-client/apps/admin/src/app/app.tsx`, `src/components/layout/AdminLayout.tsx` — route and nav registration
- `maigie-client/apps/admin/src/features/admin/services/adminApi.ts`
- `maigie-client/apps/admin/src/lib/apiClient.ts`, `apps/web/src/lib/apiClient.ts` — the client to copy
- `maigie-client/apps/web/src/features/auth/` — login, signup, OTP, `CountryPrompt`
- `maigie-client/apps/web/tailwind.config.js` — brand tokens
- `maigie-client/nx.json`, `package.json` — inferred targets, `workspaces` array
- `maigie-mobile/src/hooks/useStoreBilling.ts` — where a redeemed pass has to show up

## 15. Open items

| Item | Status |
|------|--------|
| Reward amounts | **Resolved 2026-09-12** (§5.2) |
| Domain | **Resolved 2026-09-12: `issues.maigie.com`** (Decision 1b), settled by Season 2 being confirmed |
| Recurring seasons | **Resolved 2026-09-12: Season 2 confirmed**, so multi-season is built in Season 1 (Decision 12) |
| Season 1 budget ₦300,000 and per-participant cap ₦15,000 | Derived from the signed-off amounts. Confirm, or give the real numbers. Both are per-season columns, so Season 2 can differ freely |
| Season 2 dates, even approximate | Needed for the between-seasons landing state, which ships in Phase 7 and goes live the day Season 1 closes |
| Season 2 country scope | `countryAllowlist` is an array, so widening beyond `NG` is data. But cash payouts are Nigerian bank transfers by hand — a second country needs a second payout method, which is a real cost, not a config change |
| Pass uplift percentage | Recommended 25% (Decision B); needs confirmation. Per-season, so it can be tuned between seasons |
| Terms and tax language | Needs review by whoever owns Maigie's contracts |
| Bank-account encryption key management | **Resolved 2026-09-12.** AES-GCM with the key derived from `SECRET_KEY` via HKDF under a distinct `info` label — the scheme `notifications/subscription_crypto.py` already uses. There *was* field-level encryption in the backend; the earlier note was wrong. Rotation is not supported: rotating `SECRET_KEY` makes stored account numbers unreadable and testers re-enter them, which is acceptable for tens of rows and a manual payout process, and `MultiFernet`-style key lists are the way out if it grows |
| Paystack account-name resolution | Optional, and more attractive now that payouts are manual — it catches a typo'd account number before someone transfers to a stranger |
| Paystack Transfers for automated payout | **Stronger now that seasons recur** — a manual payout queue that returns every season is the case that justifies building it. Still not a Season 1 dependency. Reassess in Phase 10 with two seasons of volume |
| Whether the programme accepts reports between seasons | `issues.maigie.com` invites it, and a permanent surface that refuses reports for ten months of the year is odd. Cheap version: an unpaid submission route that stays open with no season, triaged at leisure. Not scoped here |
| In-app notification types | Requires a `notifications/taxonomy.py` entry; email-only for Season 1 |
| Hosting target for the new app and its CORS origin | Needs infra; follows the domain decision |
| Whether approved participants get a Plus pass on approval | Would raise engagement and cost; not decided |
| Recruitment volume target | Unknown. Sizes the triage rota, which is the real constraint |
| Whether the ₦500 floor holds up in week one | Unknowable in advance (§5.2). Watch acceptance rate and submissions-per-participant; the lever is the feedback tier and adjustments, not a mid-season rewrite |

## 16. Changelog

| Date | Change |
|------|--------|
| 2026-09-12 | Initial plan. Scope, decisions, data model, contracts and Phases 0–9 drafted from a cross-repo review. Nothing implemented. Grounding facts confirmed in code: no payout rail exists anywhere in the backend; `User.country` is user-supplied rather than geolocated; `pass_service.grant` already takes a non-purchase `source`; BunnyCDN storage is available for attachments; latest migration is `082`; and `points_service` / `PointsLedgerEntry` already implements an append-only reward ledger that redeems into passes, so this domain copies a working pattern rather than inventing one |
| 2026-09-12 | **Reward amounts signed off and the payout flow pinned down.** Matrix set at critical ₦2,000 · high ₦1,500 · medium ₦1,000 · low ₦500 · high-value feedback ₦1,500 · standard feedback ₦500 (§5.2). Three consequences followed rather than being decided separately: the flat ₦500 mobile bonus is **dropped**, because at a ₦500 floor it doubled an award for choosing a platform instead of for finding anything; the minimum cash withdrawal drops ₦2,000 → **₦1,000**, since a ₦2,000 floor equals a critical bug and would have made the cash rail unreachable for most participants; and the season budget and per-participant cap are restated as ₦300,000 / ₦15,000 against the new ladder. Cash payouts are confirmed **manual**: super admin approves, transfers from the business account by hand, then records the bank reference — `mark-paid` is a record of a completed transfer, the console button reads *Record payment* rather than *Pay*, and a `revert-to-approved` route covers a bank-side failure without touching the ledger. The participant dashboard now carries the full Requested → Approved → Paid state with the reference. **Landing page confirmed as part of the new app** (§8.1), with `maigie-public` limited to one section and a footer link and explicitly carrying no second copy of the amounts (§8.2). Domain is `bug-hunt.maigie.com` or `issues.maigie.com`, still open (Decision 1b) |
| 2026-09-12 | **Season 2 confirmed, so the programme is now designed as recurring rather than as a campaign.** Three new locked decisions: multi-season by construction (12) — every season-varying value lives on `BugHuntProgram`, and opening a season is a form in the admin dashboard, not a deploy; participation carries forward (13) — a previous season's approved participants are bulk-seeded as approved, so a proven reporter does not re-audition; and `issues.maigie.com` over `bug-hunt.maigie.com` (1b), because a URL is the one piece of copy we cannot recall from a WhatsApp group. **The significant model change is that the wallet is now keyed on `userId`, not on a participation** (§6.1): one balance, one payout account, one withdrawal queue across all seasons, with only *earning* scoped to a programme. Keying the ledger on `participantId` would have stranded any unwithdrawn Season 1 balance — likely, given a ₦1,000 minimum — behind a closed season, or split it into two wallets with two minimums to clear. Fixing that later would be a migration over rows representing money owed to people, which is the worst thing in the schema to migrate. Consequent additions: a `BugHuntWallet` row to lock, the reward matrix moved onto the programme row so a closed season still shows what it paid, a `known_issue` outcome at ₦0 for a re-report of an unfixed finding from an earlier season (calling that a `duplicate` blames the reporter for our backlog), `GET /seasons` and `GET /known-issues`, a season editor and carry-forward action in the admin app, per-season terms re-acceptance via `rulesVersion`, between-seasons landing states built in Season 1 because they go live the day it closes, and a retention rule that no longer deletes a returning participant's bank details during the gap. Phase 9 is now Season 1 wrap and Phase 10 is Season 2 — which should contain zero engineering, and is the acceptance test for Decision 12 |
| 2026-09-12 | **Phase 1 shipped: the `bug_hunt` domain, migration `083`, and seasons end to end.** New package at `apps/backend/src/domains/bug_hunt/` — eight tables, the reward matrix as a seeded-then-authoritative JSONB column on the season row, named refusals carrying codes the app renders screens from, `program_service` (resolution, create-from-previous, open/close, carry-forward) and `eligibility_service` (one composed gate for the four independent facts). Nine routes mounted: `GET /bug-hunt/program|/seasons` unauthenticated, `GET /bug-hunt/me` as the app's single boot call, and seven season-admin endpoints under `/api/v1/admin/bug-hunt` split staff-read / super-admin-write with `AuditLog` on every mutation. **Verified rather than assumed:** migration `083` applied *and* downgraded against a scratch Postgres, and all 25 schema invariants exercised by hand — the three partial unique indexes (one open season, one award per submission, one open withdrawal per tester) each refused their violation, the ledger sign and attribution constraints refused a positive redemption and a season-attributed spend, and a balance summed correctly across two seasons. 157 new tests, full suite 5 028 passed, Ruff and targeted mypy clean. **Two defects found by the verification, not by reading the code:** deleting an account cascaded away its *paid* withdrawal records, erasing the evidence that real money had left the business account — now `SET NULL`, so a payout is anonymised rather than destroyed while the wallet and ledger still cascade, because a balance with nobody to pay is not a balance; and the repo's `test_orm_attribute_names` guard caught camelCase attribute reads in `/me`, whose fix also removed a query-per-season N+1 from the boot path. Still open in Phase 1: behavioural tests of the service layer against a live database — the constraints beneath it are verified, the Python above them is not |
| 2026-09-12 | **Phase 2 shipped: intake. A tester can now apply, be reviewed, submit and attach evidence.** Five participant routes — `POST /applications` (participation and first finding in one transaction), `POST /terms-acceptance`, `POST /submissions`, `GET /submissions[/{id}]`, `POST /submissions/{id}/attachments` — behind `submission_service` and an `attachments` rules module. Decisions worth recording: the reapply path **reuses the participation row** rather than inserting a second one, so the unique `(programId, userId)` index stays the thing preventing duplicates; the daily limit is **counted from rows, not Redis**, because it is a published property of the season and `check_rate_limit` degrades *open* — a limit that stops applying during a cache blip is not a limit; attachments are authorised by **ownership rather than approval**, since the application's own finding belongs to a `pending` applicant who must still be able to attach a screenshot; and a submitter cannot reach `category`, `severity`, `status` or `publicResponse` through the contract or the field mapper, because a tester who could set their own severity could set their own payment. **Verification changed shape here.** The alembic chain cannot build a database from base — it assumes the Prisma-era `User` baseline — so the harness builds the schema from SQLAlchemy metadata (131 tables), which made local `RUN_DB_TESTS=1` possible for the first time. That produced `tests/test_bug_hunt_lifecycle.py`: 74 tests including the whole Season 1 → Season 2 handover driven over HTTP through the admin API, which is Decision 12's acceptance test, and it also closes the Phase 1 gap that was left open. **The HTTP layer immediately found a defect the service tests could not:** a newly created submission's `attachments` collection was unloaded, so serialising it after the session closed raised `DetachedInstanceError` — a 500 on the first application anybody would ever file, invisible to tests that never serialise. Fixed by refreshing the relationship explicitly. A second, smaller find was in the harness itself: `AuditLog.adminUserId` is `NOT NULL` behind an `ON DELETE SET NULL` foreign key, so deleting a staff user makes Postgres attempt a null it forbids — worth knowing about beyond this domain. Totals: 274 Bug Hunt tests (200 without a database, 74 with), full suite 5 074 passed, Ruff and mypy clean on the domain |
| 2026-09-12 | **Phase 3 shipped: triage, and the admin dashboard to work it from. M2 is done.** Backend `triage_service` plus nine admin endpoints; frontend `bugHuntApi`, `bugHunt.types`, `money.ts`, shared badges and six pages in `maigie-client/apps/admin`, in their own `Bug Hunt` nav group. **The property the whole surface is built around: no amount crosses the triage boundary.** `TriageRequest` has nowhere to put a figure — a triager sets category and severity, the season's matrix decides the kobo, and accepting a grading the season does not price is *refused* rather than silently paid as zero, because "accepted, ₦0" is the one outcome that is both wrong and hard to notice. That is also why triage is **staff** rather than super admin (Decision D): the amount is not theirs to choose, and a two-click money flow across a 14-day season would not get used. Suspension *is* super admin, and explicitly does not touch a balance — confiscation would turn a moderation decision into a fine. Grading reads the submission's own season's matrix, demonstrated by a test where Season 2 prices a critical at ₦9,999 and a late Season 1 finding is still paid ₦2,000: a slow queue must never be an unfair one. `known_issue` is wired end to end with its own lookup over earlier seasons' accepted findings and its own colour in the UI, because without the lookup that decision is a memory test whose reliable outcome under pressure is `duplicate` — which blames the reporter for our backlog. Queues are ordered oldest-first throughout. The triage console shows what a grading pays *before* it is committed. **Verification, and three defects it caught:** `ilike_any` already escapes and wraps its term, so my `%term%` searched for a literal percent sign and every search silently matched nothing — an empty queue that looks like a working filter, and grep confirmed mine was the only such call site in the codebase; a falsy check on the median turnaround would have reported a perfect zero-hour queue as "not yet measured", hiding the one result worth celebrating; and the repo's `test_owner_scoped_reads` guard correctly demanded that two cross-learner staff reads declare themselves in its allowlist, which they now do by name. 101 new tests (54 behavioural against Postgres, 47 contract-level), all ten endpoints the admin UI calls exercised over HTTP against seeded data, `nx build admin` green with zero new TypeScript errors against a pre-existing 30-error baseline, full backend suite 5 154 passed. Bug Hunt totals: 375 tests. **Still open in Phase 3:** nobody is told their outcome — a tester learns it by opening the dashboard. Phase 8 owns the email |
| 2026-09-12 | **Migration `083` applied to staging.** 8 tables, 3 partial unique indexes, 34 CHECK constraints, 22 foreign keys, and 1 212 existing `User` rows untouched — the migration is additive only, and the only pre-existing table it references is `User`, as an FK target. `GET /bug-hunt/program` against the real schema returns `{"state":"between"}`, which is the correct landing state before Season 1 exists. Two `DATABASE_URL` lines are present in `apps/backend/.env`; the active one is labelled `#staging` and prod is commented out, which is what made this safe to run. **Production remains unmigrated** |
| 2026-09-12 | **Phase 4 shipped: the reward ledger. M3 is done, and a graded finding is now money somebody holds.** `ledger_service` (credit, debit, balance, summary, history, reverse) and `reward_service` (award, adjust), plus `GET /wallet`, `GET /wallet/ledger` and a super-admin adjustment endpoint. **Reading `points_service` first was worth it for what not to copy:** it reads a spendable balance in one session and writes the redemption in another, a check-then-act with no lock between the steps, so two concurrent redemptions both succeed. That survives because points are cheap; kobo are not, and this programme has two independent spend rails a tester could plausibly fire at once on purpose. So every debit here takes `SELECT … FOR UPDATE` on the wallet row and sums the ledger inside that transaction. **The races are run rather than asserted** — 4 concurrent ₦1,500 redemptions against ₦2,500 leave exactly one success and ₦1,000; 4 concurrent awards of one finding pay once; 3 concurrent awards against a ₦2,000 budget fit exactly one and `awardedKobo` lands on ₦2,000 with the CHECK intact. A lock you have not raced is a lock you are hoping for, and these are the tests that would fail if `with_for_update` were deleted while nothing else would. **The cap clamps and the budget refuses, deliberately differently:** a tester ₦500 short of their cap who files a critical gets the ₦500, because a cap is a ceiling on what we pay one person — but paying a tester less than the published amount *because we ran out of money* is the one failure that would damage the programme, so an over-budget award is refused rather than reduced, the finding stays accepted, and the console links straight to the season to raise the budget. The award is written in a **second transaction** after the grading commits, so an exhausted budget cannot silently un-accept a real bug. `TriageResponse` now carries `matrixKobo` beside `awardKobo` with an `awardBlocked` reason, because a single number cannot express "accepted, owed ₦2,000, paid nothing" — and that is exactly the state an operator has to see. Verified end to end on a seeded database: clamp, cap, budget refusal, budget raise and settlement, wallet and ledger over HTTP from the tester's own token, and `awardedKobo` equal to the sum of the season's award entries. 49 new tests; Bug Hunt totals 424; full backend suite 5 156 passed; `nx build admin` green. **Known limitation, recorded rather than hidden:** an award is written once, so correcting a mis-grade upward cannot pay the difference — the shortfall is stated in the triage response and settled with an adjustment, which is a manual step somebody has to notice |
| 2026-09-12 | **Phase 5 shipped: pass redemption. M4 is done, and "one good bug = a week of Plus" is now literally true.** `redemption_service` plus `GET /wallet/redemption-options` and `POST /wallet/redeem-pass`. Demonstrated end to end on a seeded database: a high-severity finding pays ₦1,500, the offer screen shows a 7-day pass listed at ₦1,500 costing ₦1,125 of balance at 25% off, and after redeeming it the pass appears in the learner's **own** app at `GET /billing/passes` as `inventoryCount: 1`, `status: inventory`, `source: bug_hunt`, carrying the NGN allowance of 4 500 units rather than the global 10 000 — passes are sized by market, and inheriting the global total would give away something the product does not sell in Nigeria. Prices come from `PRICE_NGN_PLUS_PASS_*`, the same constants Paystack charges against, so this rail cannot drift from the catalogue; the uplift lives on the season row, is clamped at 90% so a pass can never be free, and falls back to the config default between seasons, because the rail stays open when no season does. **The ordering is the design: debit → grant → annotate**, with the debit reversed if the grant raises. Granting first is the version that hands out free passes when the debit is refused, and `points_service` grants first — defensibly for points, not for kobo. Tested by making the grant fail on purpose rather than by reading the `except` block, and the refund is a new row so the history shows the attempt. **A defect the tests found rather than review:** a season-attributed adjustment counted against the tester's cap but never touched `awardedKobo`, so the season budget covered awards only — meaning a super admin, who is also the person who can raise the budget, could spend past it silently through the one endpoint that takes a free-typed amount. Adjustments now take the same lock and check an award does, and a clawback returns the budget. 37 new tests; Bug Hunt totals 465 (218 behavioural against Postgres); full backend suite 5 159 passed. No frontend work: the wallet is a participant surface and belongs to `apps/bughunt` in Phase 7 |
| 2026-09-12 | **Phase 6 shipped: cash payouts. M5 is done and the backend is feature-complete.** `payout_crypto`, `withdrawal_service`, four participant endpoints, five admin ones, and the payout console in `apps/admin`. **The open encryption-key question is closed by pointing at an answer the codebase already had:** `notifications/subscription_crypto.py` derives an AES-GCM key from `SECRET_KEY` with HKDF under a distinct `info` label, and this mirrors it rather than inventing a second key-management story — the earlier claim in §15 that no field-level encryption existed was simply wrong. Rotation is unsupported and recorded as such. Only the last four are ever read back, and `PayoutAccountView` has no field for the full number, which beats remembering not to include it. **The debit lands at request time**, so a pending request cannot be spent twice; a refusal writes a compensating credit; and `mark-paid` writes **no ledger entry at all**, with a test asserting the ledger is identical before and after — writing one there would pay the same request twice, once out of the wallet and once out of the bank. The payout console discloses the full number with a copy control in one view only, is **audited on read** because the disclosure is the sensitive act and the transfer happens where we cannot see it, shows a first-time payee as a first-time payee, and labels its button *Record payment* rather than *Pay*. An unreadable ciphertext degrades to "ask them to re-enter their details" rather than a 500. 62 new tests including the crypto round trip, a wrong key detected rather than returning rubbish, four concurrent requests leaving exactly one open, and the no-orphan-row case when a debit is refused after the request row exists. **One thing was written and then deliberately deleted: the automatic `finance` expense mirror.** `finance.routes._resolve_gbp` never invents an FX rate — a non-GBP line requires an operator-entered GBP figure and `amountGbp` is `NOT NULL` — so an automatic mirror could only work by inventing the number that domain declines to invent, inside a `try/except` that swallows its own failures. A wrong number in the books is worse than no line, so `financeEntryId` stays null, the console tells the operator which expense line to add and with which reference, and a test asserts the mirror does not exist so anyone re-adding it reads why first. Bug Hunt totals 527 behavioural tests against Postgres; full backend suite 5 162 passed; `nx build admin` green. **Remaining before launch is all client work:** Phase 7 (`apps/bughunt`, including the bank picker `GET /banks` this phase deliberately left out) and Phase 8 |
| 2026-09-12 | **Phase 7 shipped: `apps/bughunt`, the participant app. Both client surfaces now exist and the only thing between here and M6 is Phase 8.** A new Vite app on port 4203 — landing page, auth, application, dashboard, submission detail, wallet, rules, seasons and six standing screens — plus `GET /banks` on the backend and the `maigie-public` touchpoint. **Every number on every screen comes from the season row.** There is not one hardcoded amount, date, cap or limit in the app: the landing page, the rules page, the terms-acceptance screen and the seasons list all render from `GET /program` or `GET /seasons`, which is what makes the rules page a document we are actually honouring rather than a promise nobody enforces, and what lets a closed season keep displaying what it really paid. **Three decisions worth the words.** The app has **its own `bughunt-auth-storage` persist key** — a tester signing out here must not lose the study session they have open in the learner app they are being paid to test, and the programme calls are on the client's public-path list so a stale token cannot bounce a curious visitor off a marketing page into a login screen. **The application form and the submit form are one component**, because the application *is* a submission and two forms would drift into asking different questions and produce a first finding a triager cannot grade against the same bar; steps, expected and actual are three required boxes rather than one textarea, since a report we cannot reproduce is a report we cannot pay for. And **`/wallet` is gated on auth and nothing else** — not participation, not an approved status, not an open season. Every other route sends people to the screen describing their actual state; this one deliberately does not, because a suspended, ineligible or between-seasons tester who cannot reach their own balance is the single worst thing this programme could do to its reputation. That is the payoff for keying the wallet on `userId` in Phase 4. **The state machine lives in one function.** `standing()` in `features/participation/useMe.ts` maps one `GET /me` to one of eight screens, and suspension outranks a closed season on purpose — a suspended tester returning between seasons should not see a neutral "nothing is open" page implying they are in good standing. Scattering those conditions across pages is how you get a suspended participant looking at a submission form because one page checked `status === 'approved'` and another checked `status !== 'rejected'`. **Refusals are screens, not toasts.** `COUNTRY_NOT_SET` is a one-tap fix rather than a rejection, since telling a Nigerian tester they are ineligible because we never asked where they live turns our missing data into their dead end. `SUBMISSION_LIMIT_REACHED` gets a calm panel rather than a red error, because hitting the daily limit means somebody has been working. `duplicate` and `known_issue` carry different colours *and* different copy — merging them would blame the reporter for our backlog. The grade on an accepted finding is shown as arithmetic (category, grade, and the published amount that grade carries) so a tester who called it critical and got low is checking a table they agreed to rather than absorbing an anonymous decision. **`GET /banks` degrades to an empty list**, which the client turns into a free-text bank name: an outage at Paystack must not be why somebody cannot enter the details they get paid with. **Two smaller departures from this plan, both recorded above.** No season switcher on the dashboard — the list already spans every season with the season number on each row, so the control would filter away the thing it was added to reveal. And no device prefill from the user agent: about half the time the tester is reporting a mobile bug from a laptop, and a wrong prefilled device model is worse for a triager than an empty field. The `maigie-public` section went on the landing page rather than `/careers` (§8.2 explains why: nobody reaches a careers page looking for an evening's testing), placed at position 11 so a visitor working out what Maigie is is never offered a side job first. **Verification:** `tsc --noEmit` clean on `apps/bughunt` with **zero** errors — the app has no share of `apps/admin`'s pre-existing 28–30 baseline; `nx build bughunt` green at 384 kB / 120 kB gzipped; `nx build admin` still green after the workspace `npm install` that the new package required; `maigie-public` builds and `issues.maigie.com` appears in the emitted `dist/index.html`. Backend: 527 Bug Hunt tests still passing, ruff and mypy clean on `src/domains/bug_hunt`. No new automated tests on the participant app — it is presentational over contracts that already have 527 tests behind them, and the Phase 8 staging walkthrough is the honest verification for a screen. **Phase 8 is what remains:** the §4.3 emails (nobody is currently told their outcome — a tester learns it by opening the dashboard), analytics, the CORS origin and DNS for `issues.maigie.com`, and Season 1 created through the admin season editor rather than by SQL |
