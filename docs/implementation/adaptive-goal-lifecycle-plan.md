# Adaptive goal lifecycle

Goals that reschedule themselves and escalate effort, instead of silently going overdue.

The premise: **a goal should never quietly become overdue.** Either the learner is still pursuing it and
the system keeps adjusting, or the learner has said otherwise — completed, deprioritised, abandoned. Silence
is not one of those answers, and it is the only one the system currently records.

The two kinds of deadline behave differently and the difference is the whole design. A **course** deadline is
the learner's own intention, so it can move and the system should keep moving it rather than let the goal
lapse. An **exam** date belongs to the world, so it cannot move — and when it passes, the honest act is not
to reschedule but to **ask how it went**. That answer is what completes the preparation, and it is also the
first ground-truth label the system has ever collected about its own readiness figure (§6).

Depends on `completion-unification-plan.md`: rescheduling well requires knowing what is actually done. The
two can be built in parallel, but the escalation ladder in §5 is only as good as the completion signal
underneath it.

---

## 1. The finding that makes this cheaper than it looks

**Almost all of the intelligence is already computed. Nothing consumes it.**

`progress/services/goal_metrics.py` already derives, per goal, on every read:

| Function | Line | Answers |
| --- | --- | --- |
| `elapsed_percent` | 127 | how far through its own window the goal is |
| `is_at_risk` | 144 | has it fallen behind the pace its deadline implies (`AT_RISK_LAG_POINTS = 15`) |
| `is_due_soon` | 176 | deadline inside `DUE_SOON_DAYS = 7` and still ahead |
| `is_overdue` | 201 | active, unfinished, deadline passed |
| `status_label` | 220 | `COMPLETED` / `ON_TRACK` / `NEEDS_ATTENTION` |
| `pace_percent` | 241 | progress as a share of where the schedule says it should be |
| `projected_outcome` | 260 | where it lands at the current rate |

All pure, all correct, all **display-only**. The single goal-related periodic task is
`progress.capture_goal_progress` (01:30, snapshot writer). **No scheduled task calls `is_at_risk`,
`is_overdue` or `portfolio_headline`.** The system can already tell you a learner is three weeks behind and
projected to reach 40%; it just never does anything about it.

So this is largely a **consumer** problem, not a measurement one.

Three more things already exist and are directly reusable:

- **Auto-created goals, exactly as described.** `goal_derivation_service.derive_goals_quietly` is called on
  course creation (`knowledge/services/course_service.py:339`) and on preparation creation
  (`personal_learning/services/exam_prep_service.py:66`). A prep goal takes `ExamPrep.examDate` as its
  `targetDate` and `targetReadiness` as its target, left **empty rather than guessed** when unstated.
- **Behaviour is genuinely measured now.** `learning.analyze_behaviour` runs 02:00 daily and populates
  `preferredStudyTimes` (with a `local` vs `utc_assumed` basis), `avgSessionMinutes`, `consistencyScore`,
  `bestDayOfWeek` and `dropoutRisk`. Its own docstring records that these were NULL for every learner until
  a swallowed `TypeError` was fixed.
- **Rescheduling machinery exists in two shapes.** `study_plan_service._redistribute_plan` repacks pending
  items inside a fixed deadline; `planning_impl.regenerate_goal_plan` rewrites schedule blocks **and moves
  `targetDate`**.

## 2. The distinction you drew is already in the schema

Exam prep and course goals behave differently because **one deadline is external and the other is the
learner's own**. That is not a new field:

| | Exam prep goal | Course goal |
| --- | --- | --- |
| `metricKind` | `prep_readiness` | `course_progress` |
| `targetDate` origin | `ExamPrep.examDate` — **NOT NULL**, set by the world | `Course.targetDate` — **nullable**, set by the learner |
| Can the date move? | **No.** The exam is on the 15th. | **Yes.** It was always an intention. |
| On falling behind | escalate effort, compress the plan, notify | move the date, keep the effort steady, keep nudging |
| On the date passing | **decide** — the learner must answer | reschedule and continue |

Call this the goal's **date authority**: `external` or `learner`. Derive it rather than store it — a prep
link means external, otherwise the date is the learner's. Storing it would create a second thing that can
disagree with the link, which is the mistake `Goal.progress` already is.

`focused_minutes`, `topics_mastered` and `cards_reviewed` goals have no link and usually no date; they fall
under `learner` authority and are the least urgent case.

## 3. What is actively wrong today

**A missed exam is recorded as a success, and nothing asks how it went.**
`exam_prep_service.mark_overdue_preparations_completed` (`:729`) runs on beat at 01:00 daily, selects
`examDate < now AND status != 'COMPLETED'` across all users, and sets `status = COMPLETED` — regardless of
readiness. The preparation then drops out of `PREP_STATUSES_WORTH_A_GOAL`, so it is no longer even a
candidate for a goal. A learner who was 30% ready for an exam they missed gets a completed preparation and a
stale goal.

**A clock is not an outcome.** The date passing says the exam happened, not that the learner was ready, not
that they sat it, and not that it went well. The only party who knows is the learner, and nothing has ever
asked them — `ExamPrep` has **no outcome, score, rating or reflection column at all**. §6 is the answer to
this, and it is why the fix is not merely "stop lying".

**Rescheduling only fires when the learner is active.** *(Fixed in phase 3 — §10.2.)*
`_redistribute_plan` had exactly two triggers: `update_study_plan` when the schedule inputs change
(`:650`), and — after a learner marks an item complete — `if len(pending_past_due) > 2` (`:952`). **A
learner who goes silent got no redistribution at all**, which inverts the need: the learners whose plan has
drifted furthest are the ones not completing anything. There is now a nightly pass as well.

**Nothing ever learns whether an intervention worked.** *(Closed for goals in phase 6 — §10.5 — but not
here.)* `retention_service.record_intervention_outcome` (`:213`) sets `outcome` and `outcome_at` and still has
**zero callers** anywhere in `src` or `tests`. The `RetentionIntervention` table, the 7-day cooldown and the
0.7 churn threshold all exist; its feedback loop does not, because the whole subsystem is unreachable. The
goal ladder's loop is closed on `GoalLifecycleAction` instead. And the whole stack is unreachable regardless:
`tasks/retention_check.py` is not imported by `tasks/__init__.py`, has no beat entry, and imports
`src.workers.celery_app` rather than `src.core.celery_app`.

**The one nudge that does run is not goal-aware.** *(A goal-aware one now runs alongside it — phase 4,
§10.3.)* `learning.check_declining_engagement` (every 6h) reads `dropout_risk > 0.5` and sends one static
message pointing at flashcards. It never mentions a deadline, a goal or a plan, and it has not been changed;
`progress.review_goal_lifecycle` is a second, separate pass that does.

**Notification delivery has three traps** worth knowing before hanging anything on it. *(All three
addressed in phase 5 — §10.4 — and a fourth found there that was worse than any of them.)*

- The daily cap **silently drops**: `create_notification` returns `None` once
  `count_today_delivered >= max_daily_notifications` (default 5). It now defers instead.
- Quiet hours are compared against the **naive UTC clock**, not the learner's local time.
- `_compute_optimal_time` is a stub that returns `now`, and `deliver_pending` marks rows delivered with a
  comment reading `# Deliver the notification (push/email would go here)`. **There is no push on the
  learning path** — push infrastructure exists but is only used by credit purchases. A push is now
  attempted, but it still reaches nobody: nothing registers a device token.

## 4. A constraint that shapes the design

`goal_derivation_service`'s docstring records an explicit decision:

> **Derivation fires when intent is recorded, not on a schedule.** `progress_repo.delete_goal` is a hard
> `DELETE`, so a nightly sweep over "courses without goals" would resurrect a goal the learner deliberately
> threw away, every night, with no way for them to make it stop.

So the nightly job proposed below **must never create a goal.** It adjusts, escalates and notifies about
goals that already exist. Goal creation stays on the intent-recording paths. If per-topic goals are wanted
(§8), they are created when the plan is created, not swept into existence.

## 5. The design

### 5.1 One nightly pass, acting on what is already derived

A `goal_lifecycle_service.review_goals(user_id, now)` — and a beat task around it — that loads the
learner's active goals, measures them with the existing `derive_current_values` / `derived_progress`, and
takes exactly one action per goal from a **ladder**, never more:

| Condition | `external` date authority | `learner` date authority |
| --- | --- | --- |
| On track | nothing | nothing |
| At risk, deadline far | compress the plan: `_redistribute_plan` | compress the plan |
| At risk, `due_soon` | notify with the real numbers, offer *deprioritise* / *keep going* | extend the date, notify that it moved |
| Deadline passed, unfinished | **ask how it went** — the post-exam review in §6. Never extend, never infer. | extend the date, notify, keep the effort |
| Learner answered *deprioritise* | set the goal `ARCHIVED`; stop nudging | same |
| Learner answered *completed* | `COMPLETED` | same |
| No answer after N days | leave `ACTIVE`, stop escalating, record the silence | same |

Two rules make this honest rather than nagging:

- **One action per goal per pass, and a cooldown.** Reuse the `RetentionIntervention` shape: a row per
  action, with the 7-day `INTERVENTION_COOLDOWN_DAYS` precedent. A goal that was extended yesterday is not
  extended again tonight.
- **An external deadline is never moved, and never silently accepted.** For a prep goal past its exam date
  the system has no truthful action except asking — see §6. Anything else asserts an outcome nobody
  observed, which is the same argument migration 046 makes about `ScheduleBlock.completedAt`:
  *"Marking historical blocks complete because their date has passed would assert attendance nobody
  recorded."*

### 5.2 Extension is bounded and recorded

For `learner`-authority goals, extending the date needs limits or it becomes a goal that can never fail:

- Extend by a **measured** amount, not a fixed guess: the work remaining divided by the learner's observed
  throughput (`avgSessionMinutes`, `consistencyScore`, `preferredDays`). `_daily_minute_budget` already
  computes the daily figure and already prefers the learner's stated session length over the observed one.
- Cap the number of extensions. After the cap, the honest move is to ask whether the goal is still wanted
  rather than extend a ninth time.
- **Record every extension.** A goal quietly rewritten three times looks on-track and is not. This wants a
  small `GoalScheduleChange` table — or `RetentionIntervention` generalised — holding the old date, the new
  date, the reason token, and the learner's response. Without it, `elapsed_percent` silently resets its own
  denominator on every extension and `is_at_risk` can never fire again.

That last point is the subtle one. `elapsed_percent` measures from `createdAt` to `targetDate`. Moving
`targetDate` forward *reduces* elapsed percent, which reduces the at-risk lag, which means **an
auto-extending goal would mark itself healthy by moving its own goalposts.** The change log is what keeps
"extended four times" visible, and the surfaces should show it.

### 5.3 Learning why, honestly

"Learn why" splits into three things with very different confidence, and they should not be presented as
one:

**Measured, available today.** `consistencyScore`, `avgSessionMinutes`, `bestDayOfWeek`,
`preferredStudyTimes` (with its `basis`), `dropoutRisk`, and — from the completion work — which *kinds* of
item stall. These support statements like "you have missed four Tuesday sessions in a row" and
"your sessions have shortened from 40 to 15 minutes". They are facts.

**Inferable with care.** Correlations across those signals: overdue clusters on particular weekdays, on
particular item types, or after a run of `no_room` agenda days. Worth computing, worth acting on, and worth
wording as an observation rather than a diagnosis.

**Not knowable.** *Why* the learner stopped. The system can observe that they did and offer options; it
cannot know the exam was cancelled or they got ill. **Ask, do not conclude.** The one place a real reason
can be captured is the learner's answer to a nudge, which is exactly why the answer must be stored — and
this is where `record_intervention_outcome` finally gets its callers.

That feedback loop is what turns this from a rules engine into something that improves: intervention →
learner response → outcome recorded → which interventions actually work for this learner. Without the
outcome write, every future version is guessing at the same rate as the first.

**The LLM's role is wording, not deciding.** This repo already holds that line — "the service decides where
and why; the client writes the words" (`agenda_service`), and the model "narrates and never supplies a
number". Rescheduling arithmetic, thresholds and the ladder are code. The model may phrase the nudge.

### 5.4 Fix the notification path first

Any escalation is only as good as its delivery, and all three traps in §3 need closing before a deadline
nudge ships: quiet hours compared in the learner's own zone, the daily cap not silently dropping a
high-priority deadline message, and — for this to be worth anything — push wired on the learning path. A
"your exam is in three days and you are 30% ready" notification that lands in an in-app list the learner
does not open has not been delivered.

## 6. The post-exam review — how a preparation actually completes

**A preparation is completed by the learner telling us how it went, not by a date passing.** This replaces
the 01:00 sweep as the completion path and, in doing so, gives the system the one thing it has never had: a
ground-truth label.

### 6.1 The ask

The day after `examDate`, the prep enters a state that is **awaiting the learner's answer** rather than
completed. The learner is asked, once, with a cooldown and a bounded number of reminders:

1. **Did you sit it?** — sat / missed / postponed / cancelled. Four real outcomes, and three of them are not
   failure. A postponed exam is the one case where a new date is legitimate, and it is legitimate *because
   the learner said so*, not because the system inferred it.
2. **How did it go?** — a small ordinal scale, not free text alone. Ordinal so it can be correlated;
   free text alongside it because the interesting reasons are never in the scale.
3. **How well did the preparation serve you?** — the rating you described, and it is a **different question**
   from how the exam went. A learner can be well prepared and still have a bad day, or scrape through badly
   prepared. Collapsing the two would make every signal derived from them useless.
4. **Optionally, the result** when they have it — often weeks later, which is why it must be a separate,
   later write and not a required field on the first answer.

Only after an answer does the prep become `COMPLETED`, and the prep goal resolves with it. Until then it is
neither complete nor overdue — it is waiting, and the surfaces should say so.

**If the learner never answers**, the prep stops nudging after the reminder budget and settles into a
terminal state that asserts nothing about the outcome. That is a truthful record: the exam date passed and
we do not know what happened. It is not `COMPLETED`.

### 6.2 What this makes possible: readiness becomes falsifiable

This is the part worth building the feature for.

`prep_readiness.progress_percent` is `topicsStrong / topicsTotal`, where "strong" is
`MASTERY_STRONG_THRESHOLD = 80.0` (`prep_readiness.py:45`). It is a **prediction** — "you are 72% ready" —
and `PrepReadinessSnapshot` already records it, with `averageMasteryPercent`, `topicsTotal`, `topicsStrong`
and `topicsFocus`, **once per prep per day**. So the full predicted trajectory up to and including the exam
day is already in the database.

**Nothing has ever compared it to what happened.** There is no outcome to compare against. The number is
asserted, acted upon, shown to learners, used to gate goal progress — and unfalsifiable.

An outcome label changes that at three levels:

- **Per learner.** If this learner's "80% ready" has twice corresponded to feeling under-prepared, the
  threshold is wrong *for them* and their readiness can be reported more conservatively.
- **In aggregate.** Is `MASTERY_STRONG_THRESHOLD = 80` the right number? Is `topicsStrong / topicsTotal` the
  right formula, or does `averageMasteryPercent` predict outcomes better? Both are recorded; neither has
  ever been scored. This is answerable with a query once outcomes exist.
- **Per preparation type.** `ExamPrep.prep_type` distinguishes exam, certification, interview and test.
  Readiness may calibrate very differently across them, and one threshold currently serves all four.

There is also a **before-and-after pairing already half-present**: `ExamPrep.confidence` (`:203`) is a
stated self-assessment at setup. Stated confidence → measured readiness trajectory → actual outcome is a
three-point sequence, and today only the middle point exists.

### 6.3 Rules for the feedback, so it stays honest

- **Store the answer, derive the conclusions.** The learner's rating is a fact and goes in a row. Anything
  inferred from it — a recalibrated threshold, a changed nudge — is derived on read from those rows, the same
  discipline `derived_progress` follows. A stored conclusion is wrong the moment the next answer arrives.
- **Never overwrite readiness history with hindsight.** `PrepReadinessSnapshot` says what was believed on
  that day. If calibration later says 72% should have read 55%, that is a *new* interpretation, not a
  correction of the record. Migration 046's principle again: do not rewrite the past to look consistent.
- **Two questions, two columns.** How the exam went and how the preparation served them, kept separate for
  the reason in §6.1.
- **Ordinal plus optional prose.** The scale is what correlates; the prose is where the actual reason lives
  and cannot be inferred. Both, and neither pretending to be the other.
- **Do not compute a per-learner calibration from one exam.** Most learners will have one prep, ever. The
  aggregate signal is the useful one first; per-learner adjustment needs several outcomes and should stay
  absent rather than confident, which is the same rule `preferredStudyTimes` follows with its
  `MIN_SESSIONS_FOR_TIME_PATTERN` floor and its `basis` field.
- **Asking is not free.** This is a message after a possibly-bad exam. One ask, a small reminder budget,
  respectful wording, and an easy way to decline permanently. A learner who dismisses it is answering.

### 6.4 What to store

New, because none of it exists. Modelled on `CourseOutlineSatisfaction`
(`knowledge/db_models.py:498`, the KPI-feedback precedent) and `RetentionIntervention.outcome` /
`outcome_at` (`personal_learning/db_models.py:1873`, the outcome-recording precedent):

- On `ExamPrep`, or in a `PrepOutcome` row beside it: `attended` (sat/missed/postponed/cancelled),
  `experienceRating`, `preparationRating`, `reflection` (nullable prose), `resultValue` +
  `resultScale` (both nullable, written later), `answeredAt`, and `askedAt` / `reminderCount` for the
  nudge budget.
- **A separate row rather than columns is probably right**, because a postponed exam produces a *second*
  sitting of the same preparation, and a second outcome. Columns would overwrite the first.
- The **readiness at the time** should be snapshotted onto the outcome — `progress_percent`,
  `averageMasteryPercent`, `topicsStrong`, `topicsTotal` as of the exam day. They are recoverable from
  `PrepReadinessSnapshot` today, but copying them makes the calibration query one table and immune to the
  snapshot being pruned.

## 7. Phases

Assumes the §8 decisions are made first. Engineer-days of focused work, not calendar.

**Phases 0 through 6 are implemented on the backend.** See §10 for what shipped, what changed against this
plan, and what is still open. **No client renders any of it** — every phase from 1 onwards has an outstanding
UI half, and that is now the binding constraint rather than backend effort.

| | Work | Days | Risk |
| --- | --- | --- | --- |
| **0** | ~~**Stop declaring missed exams complete.**~~ **Shipped.** | 1 | **Low, high value** |
| **1** | **The post-exam review** (§6). **Backend shipped**; both clients' forms outstanding. | 5–7 | Medium — new schema and new copy on a sensitive moment |
| **2** | ~~**Derive date authority** + a `GoalScheduleChange` log + surface "extended N times".~~ **Shipped**, no behaviour change. See §10.1. | 2 | Low |
| **3** | ~~**Time-triggered redistribution.** Make `_redistribute_plan` reachable without learner action.~~ **Shipped.** See §10.2. | 1–2 | Low |
| **4** | ~~**The nightly pass**, ladder implemented, one action per goal, cooldown enforced.~~ **Shipped.** See §10.3. | 4–5 | Medium |
| **5** | ~~**Notification path fixes** — learner-local quiet hours, priority bypass of the daily cap, push on the learning path.~~ **Shipped**, with push still blocked on device registration. See §10.4. | 3–4 | Medium — touches every notification |
| **6** | ~~**The learner's answer to a nudge, stored.**~~ **Backend shipped**; both clients' affordances outstanding. Recorded on `GoalLifecycleAction`, not `record_intervention_outcome` — see §10.5. | 3 | Medium |
| **7** | **Readiness calibration** (§6.2): score `progress_percent` and `averageMasteryPercent` against recorded outcomes, in aggregate first. | 3–4 | Low technically; **blocked on outcome volume** |
| **8** | **Behaviour correlation** — which weekday, which item kind, which pattern precedes a stall. | 3–5 | Medium; needs data from 1–6 first |

Backend ≈ **22–32 engineer-days**. Clients need ~6–9 days each — the review form is real UI on a moment that
deserves care, plus the response affordances and extension history. Realistically **8–11 weeks elapsed for
one person**.

**Phases 7 and 8 are gated on data, not effort.** Calibration needs recorded outcomes and most learners have
one preparation, so the aggregate signal will take months to become meaningful. Build phase 1 early precisely
because it starts the clock; do not schedule phase 7 against it.

**Cheapest meaningful slice: phases 0 + 3.** Two to three days, no new schema. It stops the system asserting
that a missed exam went fine, and makes rescheduling reach the learners who need it — the inactive ones.
Phase 1 is the next-most valuable and the one that makes everything after it possible.

## 8. Decisions needed first

**Answered by §6:** what a prep goal becomes when its date passes. It waits for the learner's answer, and
completes on that answer. The date is never pushed forward automatically; a postponed exam gets a new date
because the learner said it was postponed.

**Answered by phase 4 (§10.3):** decision 1, the extension cap — **three**, counting only the system's own
extensions. Decision 6 in part: the cooldown is seven days per goal, so a goal produces at most one message
a week; whether the post-exam ask bypasses the daily cap is still phase 5's to settle.

Still open:

1. ~~**How many times may a `learner`-authority goal extend before the system asks instead?**~~ Answered:
   three. See §10.3.
2. **What state is "awaiting your answer"?** `Goal.status` is `ACTIVE | COMPLETED | ARCHIVED | CANCELLED`
   (`progress/models.py:37`) and `ExamPrep.status` is `SETUP | IN_PROGRESS | COMPLETED`
   (`personal_learning/models.py:957`). Neither has a value
   for it, and adding one is a contract change for both clients. Alternative: keep `ACTIVE` and let the
   *absence of an outcome row* be the state, which adds no enum value but makes every reader do a join.
3. **What terminal state does an unanswered preparation reach?** It must not be `COMPLETED`. It also should
   not stay `ACTIVE` forever, or the learner is nagged by their own history.
4. **What scale for the two ratings?** Recommend the same shape for both so they can be compared, and an
   odd number of points so "about as expected" is expressible.
5. ~~**Is "deprioritise" `ARCHIVED`, or a new paused state?**~~ Answered: `ARCHIVED`. See §10.5.
6. **How many deadline nudges per goal per week is acceptable**, given the cap of five notifications per day
   across everything? And does the post-exam ask count against that cap or bypass it?
7. **Should extending a goal's date also move `Course.targetDate`?** They are two records of one intention
   and they will disagree otherwise.
8. **Per-topic goals** (§9) — wanted, or does one course goal plus plan items already cover it?

## 9. On per-topic goals

The idea of a goal per topic as the learner proceeds is appealing and probably wrong as stated.
`MAX_DERIVED_COURSE_GOALS = 3` exists for a reason recorded in the source: one live learner has sixteen
unarchived courses, and "deriving a goal for each would put sixteen commitments on a surface whose job is to
show what the learner is working towards, which is the course list they already have, sorted worse." A goal
per topic is that failure multiplied — a twelve-topic course becomes twelve goals.

What is actually wanted is *per-topic dated targets inside one goal*, and that structure already exists
twice over: `StudyPlanItem.scheduledDate` is a dated per-topic commitment, and `GoalMilestone` is the
learner's own breakdown with `targetValue` and `achievedAt`. The right move is to make the course goal's
milestones line up with the plan's phases — `StudyPlanItem.phase` is already the grouping label — and let
the ladder in §5.1 act per milestone. That gives the behaviour without multiplying the goals.

Also worth closing: **no path creates a goal from a StudyPlan.** `study_plan_service` never imports
`goal_derivation_service`. A plan is stated intent with a deadline, which is exactly what the derivation
rules accept — but note that a plan generated *from* a preparation would double up with the prep goal, so
the link check needs to cover that.

## 10. Implementation record — phases 0 and 1, backend

Shipped in `maigie/apps/backend`. Verified: **3,364 tests passing** (baseline 3,325), `ruff check src tests`
clean, `export_openapi.py --check` in sync. **24 mutations applied one at a time; 24 caught, none survived.**

**The lie is gone.** `mark_overdue_preparations_completed` is now
`exam_prep_service.mark_preparations_awaiting_review`. It moves a passed preparation to `AWAITING_REVIEW`
and asks the learner; **only their answer sets `COMPLETED`**. The Celery task *name* is unchanged
(`learning.mark_completed_preparations`) on purpose — the beat schedule references it, and renaming a
registered task means a deploy where beat points at a name no worker answers to.

**What was built**

| Piece | Where |
| --- | --- |
| `AWAITING_REVIEW` status | `PreparationStatus`, now four values |
| `PrepOutcome` table | `db_models.py`, migration `050_prep_outcome` |
| Ask budget on the prep | `ExamPrep.reviewAskedAt` / `reviewRemindersSent` / `reviewDeclinedAt` |
| The decisions | `services/prep_outcome_service.py` |
| Endpoints | `GET`/`POST` `/preparations/{id}/review`, `/review/result`, `/review/decline`, `/review/history` |
| Repo reads/writes | `upsert_prep_outcome`, `find_prep_outcome`, `list_prep_outcomes`, `list_preps_awaiting_review`, and `progress_repo.list_goals_for_prep` (which did not exist) |

**Five decisions made while building, that this plan had left open**

1. **Rating scale: 1–5, the same scale for both ratings** (§8 decision 4), so they can be compared and
   "about as expected" is expressible.
2. **`experienceRating` is refused when the exam was not sat; `preparationRating` is not.** Someone who
   missed the exam can still say whether the preparation was any good — and that is the rating that says
   anything about us, so refusing it would discard the signal the feature exists for.
3. **`COMPLETED` means "this preparation is finished", not "you passed."** It is a lifecycle value; the
   outcome row carries what happened. That is what lets `missed` and `cancelled` conclude a preparation
   without asserting success.
4. **A declined review does *not* complete the preparation** (§8 decision 3, partly). Nothing has been said
   about how it went, so completing it would restore the exact lie being removed. It leaves the ask list and
   stays out of the way.
5. **The linked goal resolves as measured, not guessed** — `COMPLETED` when `derived_progress >= 100`,
   `ARCHIVED` otherwise. Marking every goal complete would claim the learner was ready; archiving every one
   would discard the achievement of those who were. Contained in a `try/except`: the answer is stored and a
   goal that could not be resolved is a stale label, not a reason to reject it.

**Four things found while implementing that the plan had not anticipated**

- **A preparation in `AWAITING_REVIEW` would have vanished from the dashboard.**
  `prepare_dashboard_service` queried only `SETUP` and `IN_PROGRESS`, so the preparation would have
  disappeared the morning after the exam and the review would have been reachable *only* from a
  notification — which quiet hours and the daily cap can both suppress. `AWAITING_REVIEW` is now in
  `ACTIVE_STATUSES`.
- **`ACTIVE_STATUSES` existed and the queries did not use it.** `_load_active_preparations` hard-coded the
  same two strings, so changing the constant changed nothing. Its own test caught this, because that test
  asserts the queries *issued* against the constant rather than restating the constant. It now reads it.
- **Migration numbered 050, not 049.** `049_chat_msg_grounding` already claimed 048 as its parent while
  this work was in progress, and two revisions with one parent is a branch alembic refuses to upgrade past.
- **`ExamPrep.examDate` is stored without an offset** while the ORM declares otherwise, so it arrives naive
  and comparing it against an aware `now` raises. Handled by a local `_as_utc`, the same shape as
  `goal_metrics._utc` — which exists because that exact mismatch made `GET /progress/goals` a 500.

**One test was passing for the wrong reason, and mutation is what found it.** The
"nothing to measure is null rather than zero" case monkeypatched `prep_outcome_service.prep_readiness`, but
`_readiness_at_answer` does a function-local `from . import prep_readiness`, which rebinds and ignores the
patch. The real call ran, failed inside its own `try/except`, and returned `{}` — indistinguishable from the
behaviour being asserted. Mutating the `topics_total <= 0` guard away left the test green. It now patches the
`prep_readiness` module itself and asserts the stand-in was actually reached.

**Still open on phase 1**

- **Both clients.** No UI exists: the review is reachable only by calling the API. Web and mobile each need
  the form, and mobile has an existing `preparation_review` notification `action_data.route` to honour.
- **The `preparation_review` notification inherits the known traps** — quiet hours compared against the
  naive UTC clock, and the daily cap of five silently dropping a message. Phase 5 fixes those; until then a
  learner at their cap may not see the ask, which is why the ask is also on the dashboard.
- **Nothing consumes the outcomes yet.** Calibration is phase 7 and is gated on volume, not effort.

### 10.1 Implementation record — phase 2, backend

Shipped in `maigie/apps/backend`. Verified: **3,464 tests passing** (baseline 3,432, +32 new), `ruff check
src tests` clean, `export_openapi.py --check` in sync, single alembic head. **8 mutations applied one at a
time; 8 caught.**

**No behaviour change.** Nothing new moves a deadline, no predicate changed, and no existing response field
changed meaning. What changed is that a deadline moving is now recorded and published.

| Piece | Where |
| --- | --- |
| `date_authority(goal)` — derived, `external` \| `learner` | `goal_metrics.py`, beside the other derived labels |
| `GoalScheduleChange` table | `progress/db_models.py`, migration `051_goal_sched_change` |
| The one rule for what counts as a change | `services/goal_schedule_log.py`, `record_date_change` |
| Batched read of the count | `goal_metrics.derive_schedule_history`, one query for a whole page |
| Wire fields | `GoalResponse.dateAuthority`, `.extendedCount`, `.originalTargetDate` |
| Writers wired | `goal_service.update_goal` (`learner_edited`), `planning_impl.regenerate_goal_plan` (`plan_regenerated`) |

**Decisions made while building**

1. **`extendedCount` counts only deadlines pushed *later*.** Pulling a deadline forward, and setting a first
   deadline on a goal that had none, are both recorded as changes but excluded from the count. Neither buys
   the learner room, and a count that includes ordinary edits is a warning light that is always on.
2. **`dateAuthority` is snapshotted onto each log row**, though it stays derived everywhere else. `Goal.prepId`
   is `ON DELETE SET NULL`, so deleting a preparation would retroactively reclassify every past change on its
   goal as the learner's own — and what an entry records is what was true when the date moved. Same argument
   `050` makes for copying readiness onto the outcome.
3. **`originalTargetDate` is published, not applied.** It is the denominator `elapsed_percent` arguably should
   use, but re-basing that window changes every pace figure on every surface. That is a behaviour change with
   its own decision, and this phase was scoped not to make it.
4. **The reason token set holds only what has a writer.** No `system_extended`, because the ladder that would
   extend a deadline unprompted does not exist; no `learnerResponse`/`respondedAt` columns, because nothing
   asks yet. Both arrive with their writers, on the same grounds migration 032 removed a column nothing wrote.
5. **Derived in memory, not aggregated in SQL.** The interesting figure is *the previous date on the earliest
   row*, not `min(previousDate)` — a deadline pulled forward and later pushed out has a minimum that was never
   the goal's original window. One query for the page, attributed in memory, as `derive_current_values` does.
6. **A failure to log never fails the edit.** The edit is what the learner asked for; the log is bookkeeping
   about it.

**Found while implementing**

- **`regenerate_goal_plan` rewrites externally-owned deadlines today.** It recomputes `targetDate` from a
  requested duration in weeks and writes it without reading date authority, so regenerating a plan can move a
  date that came from an exam. Left as-is — blocking it is a behaviour change and belongs with the ladder —
  but it is now recorded, which is what makes it findable.
- **`POST /progress/goals/{id}/regenerate-plan` is broken for an unrelated reason.**
  `intelligence/action/action_service.py` is a stub holding only `execute`, so the `action_service.create_schedule`
  call in that route's block-creation loop raises `AttributeError`. The deadline write happens *before* the
  loop, so in production the date moves and then the request 500s. Not touched here; it needs its own fix.
- **`index=True` on the model would have named the index differently from the migration**
  (`ix_GoalScheduleChange_userId` versus `GoalScheduleChange_userId_idx`), which is how autogenerate ends up
  proposing to add an index that already exists. Declared in `__table_args__` with the migration's name.

**Still open on phase 2**

- **Neither client shows any of it.** `extendedCount` and `originalTargetDate` are on the wire and nothing
  renders them, so a goal extended three times still *looks* comfortable to a learner. §5.2 asks for the
  surfaces to show it; that is client work.
- **`ReflectGoal` does not carry the new fields.** It is the second goal read model
  (`personal_learning/models.py`) with its own naming convention, so Reflect goal cards cannot show extension
  history yet.
- **The log starts empty and cannot be backfilled.** Past date moves left no trace anywhere, so `extendedCount`
  reads `0` for every goal that was extended before this shipped. That is truthful but it means the number is
  only useful going forward.

### 10.2 Implementation record — phase 3, backend

Shipped in `maigie/apps/backend`. Verified: **3,486 tests passing** (baseline 3,467, +19 new), `ruff check
src tests` clean, `export_openapi.py --check` in sync (no wire change), single alembic head, beat entry and
task registration confirmed loaded. **12 mutations applied one at a time; 12 caught.**

| Piece | Where |
| --- | --- |
| `StudyPlan.lastRedistributedAt` | `personal_learning/db_models.py`, migration `052_plan_redistributed` |
| The cross-user drift query | `repository.list_plans_with_drift` |
| The sweep | `study_plan_service.redistribute_drifted_plans` |
| Beat task, 05:00 daily | `tasks/plan_redistribution.py`, `learning.redistribute_drifted_plans` |
| Named thresholds | `MAX_TOLERATED_PAST_DUE = 2`, `REDISTRIBUTION_COOLDOWN_DAYS = 7` |

**The sweep decides nothing new.** The drift test and the placement arithmetic are the ones the completion
path already used, so a plan repacked overnight lands exactly where it would have landed had the learner
opened the app and ticked something off. What was missing was only the trigger.

**Decisions made while building**

1. **The `> 2` magic number is now a named constant shared by both paths.** It had no comment and no
   explanation anywhere. Two definitions of "behind" would mean a plan that is drifted when the learner
   completes something and not drifted overnight.
2. **A cooldown was mandatory, not a nicety.** Redistribution re-anchors *every* pending item to tomorrow,
   so an ungated nightly pass walks a silent learner's whole schedule forward one day every night — dates
   that never settle and a diff on every client poll. Seven days, matching
   `INTERVENTION_COOLDOWN_DAYS` and the weekly check-in.
3. **The stamp is written even when nothing moved.** A plan whose every pending item is pinned to an
   accepted calendar block moves nothing, and stamping only on success would leave it reconsidered every
   night forever. Same trap `run_weekly_check_ins` documents for suppressed notifications.
4. **The learner-triggered path is deliberately *not* throttled.** Completing an item still redistributes at
   once. A cooldown there would look like the app ignoring them.
5. **Expired plans are excluded.** `days_remaining = max(1, (deadline - now).days)` means a past deadline
   yields a one-day window and every pending item piles onto tomorrow. That is a wall, not a schedule, and
   what to do with an expired plan is a question for the learner — which is the same argument §6 makes about
   preparations.
6. **`PAUSED` and `SUPERSEDED` are excluded**, per `StudyPlan.status`'s own docstring: pausing is not a
   statement about the deadline.
7. **The learner is told.** They did not ask for this, and a schedule rewritten overnight in silence is the
   system changing their commitments behind their back — the phase boundaries they accepted in the wizard
   move with it, since a phase's week range is just the span of its items' dates. Delivery can still be
   dropped by quiet hours or the daily cap; that is phase 5's defect, and the stamp does not depend on it.
8. **Per-plan error containment**, following `check_declining_engagement`. `run_weekly_check_ins` has no such
   guard and one bad row aborts the run.

**One behaviour change to the existing paths, made deliberately**

`_redistribute_plan` now **leaves items that have an accepted calendar block where they are.**
`StudyPlanItem.scheduleBlockId` is set when the learner accepted a suggested hour, so a real `ScheduleBlock`
sits on that day; moving `scheduledDate` and leaving the block behind gives them a calendar entry on Tuesday
and a plan item on Friday, and the day they turn up is the one in their calendar. This affects the two
learner-triggered paths as well as the sweep, on purpose — two different redistribution semantics would be
worse than one changed one, and the same argument that stops redistribution rewriting the rhythm the learner
chose stops it moving a time they explicitly accepted. `_redistribute_plan` also now returns the number of
items it moved, so a caller that did not act on the learner's behalf can say what it did.

**Still open on phase 3**

- **The pile-up case is unchanged.** A plan whose deadline is two days away still packs all its remaining
  items onto those two days. That is arguably honest and it is what the interactive path has always done, so
  it was left alone rather than redesigned here.
- **Per-item commits.** `_redistribute_plan` issues one `update_plan_item` per item, each in its own session,
  so a forty-item plan is forty commits and a crash mid-plan leaves it half repacked. Survivable because the
  next sweep converges, but a plan-scoped transaction would be better.
- **Placement is by UTC day.** Unlike `list_items_due_today`, redistribution does not resolve the learner's
  timezone, so an item can land on a day boundary that is not theirs.
- **Nothing tells the learner *why*.** The notification says the plan was rescheduled, not that they have
  missed four Tuesdays. That is §5.3, and it needs phase 8's correlation work to say anything true.

### 10.3 Implementation record — phase 4, backend

Shipped in `maigie/apps/backend`. Verified: **3,523 tests passing** (baseline 3,486, +37 new), `ruff check
src tests` clean, `export_openapi.py --check` in sync (no wire change), single alembic head, task and beat
entry confirmed loaded. **19 mutations applied one at a time; 18 caught, 1 equivalent mutant** (`len(rows) <
2` versus `< 1` in the extension sizer — a single row still fails the span check, so both forms behave
identically).

| Piece | Where |
| --- | --- |
| The ladder | `progress/services/goal_lifecycle_service.py`, `review_goals` |
| Its memory, and the cooldown | `GoalLifecycleAction`, migration `053_goal_lifecycle` |
| `system_extended` reason token | widened `GoalScheduleChange_reason_check` in the same migration |
| A separate count for the budget | `GoalScheduleHistory.system_extended_count` |
| Bounded candidate query | `repository.list_goals_for_lifecycle_review` |
| Beat task, 02:30 daily | `workers/progress_tasks.py`, `progress.review_goal_lifecycle` |
| Named limits | `GOAL_ACTION_COOLDOWN_DAYS = 7`, `MAX_SYSTEM_EXTENSIONS = 3`, `RATE_WINDOW_DAYS = 14` |

**The rungs, as built**

| Condition | `external` | `learner` |
| --- | --- | --- |
| On track, or finished | nothing | nothing |
| At risk, deadline far | nothing here — `redistribute_drifted_plans` owns it | same |
| At risk, due soon | `warned`, with the real numbers | `extended`, or `asked_to_confirm` |
| Deadline passed | nothing here — the post-exam review owns it | `extended`, or `asked_to_confirm` |

**Decisions made while building**

1. **The extension cap is three, counting only the system's own extensions** (§8 decision 1). An extension
   here is sized from the learner's measured rate, so it is a date they were on pace to meet when it was
   set. Missing three consecutive achievable dates is evidence about the goal, not the arithmetic. Counting
   a learner's own edits against the budget would mean refusing to help someone for having re-planned —
   which is why `GoalScheduleHistory` now carries two counts. The wire keeps publishing the wide one,
   because a learner's question is "has this deadline moved" and it does not matter who moved it.
2. **"Deprioritise" is deliberately still undecided** (§8 decision 5). It is a *state the learner's answer
   puts a goal into*, and nothing asks for that answer until phase 6. Inventing the state now would add a
   status with no writer, which is the rule this work has followed throughout.
3. **Extensions are sized from `GoalProgressSnapshot`, and refused when they cannot be.** Fewer than two
   recorded days, a window that collapses to one day, no progress gained, or nothing left to do all return
   `None`, and the ladder asks the learner instead. A goal at 0% for a fortnight has no rate, and 0 is not
   a number you can divide by to get a deadline. A fixed "add two weeks" would have been the system
   inventing a commitment.
4. **Each extension may at most double the goal's *original* window**, read from
   `GoalScheduleHistory.original_target_date` rather than from `targetDate`. Using the current column would
   let three extensions compound, each doubling a window the last had already doubled.
5. **An overdue prep goal gets no action at all.** `mark_preparations_awaiting_review` has already asked how
   the exam went. A second ask, in different words, from a different surface, about the same exam, reads as
   the system not knowing what it had already said.
6. **Rung 4 was deliberately not implemented here.** "At risk, deadline far → compress the plan" shipped as
   phase 3, triggered by actual item-level drift rather than by derived progress lagging. Calling
   `_redistribute_plan` from the ladder as well would bypass that sweep's cooldown and bring back the
   nightly churn it exists to prevent. The more direct signal already has an owner.
7. **The cooldown is a `NOT EXISTS` against the action log, not a stamp on the goal.** A stamp would say
   when the ladder last acted but not what it did, and "extended or merely warned" is what the next decision
   turns on.
8. **The action row is written before anything is sent, and failing to write it fails the action.** The
   opposite of `goal_schedule_log`, which swallows its own failures so a missing audit row cannot reject a
   learner's edit. Here the row *is* the cooldown, so an action taken without one repeats every night after.
   Better to lose one night's escalation than to start a loop. And it cannot be read from the notification
   table instead: `create_notification` returns `None` under quiet hours or the daily cap, so a suppressed
   warning would be indistinguishable from one never sent. That is the third time this programme has closed
   that same trap.
9. **The candidate query is bounded to the due-soon horizon.** Whether a goal is at risk needs derived
   progress and cannot be asked in SQL, but both acting rungs need the deadline near or past, so loading
   anything further out would be work spent to decide to do nothing.

**Still open on phase 4**

- **Nobody can answer.** The ladder asks "do you want to keep going, or set it aside?" and there is no
  endpoint, no affordance and no column to receive the reply. That is phase 6, and until it lands
  `asked_to_confirm` is a question into the void — the notification is the only thing the learner sees, and
  the only thing they can do about it is open the goal and edit it by hand.
- **`record_intervention_outcome` still has zero callers.** Phase 4 records what the system *did*, never
  whether it worked. Every future version of this ladder is guessing at the same rate as the first until
  phase 6 closes the loop.
- **Delivery is still unreliable.** All three messages go through the path with learner-local quiet hours
  unimplemented and a daily cap that silently drops. Phase 5.
- **Extending a goal does not move `Course.targetDate`** (§8 decision 7, still open). The two records of one
  intention will disagree after the first extension.
- **The wording is server-composed.** Consistent with `run_weekly_check_ins`, and the numbers in it are the
  server's, per §5.3's rule that the model may phrase but never supply a number. It has had no review for
  tone on what may be a discouraging moment.
- **Unmeasured.** How many goals this fires on, on the first night, is unknown — §11's first item. The pass
  is bounded by `limit=500` and a seven-day cooldown, so the blast radius is capped, but the first run could
  still put a message in front of a lot of people at once.

### 10.4 Implementation record — phase 5, backend

Shipped in `maigie/apps/backend`. Verified: **3,564 tests passing** (baseline 3,523, +41 net), `ruff check
src tests` clean, `export_openapi.py --check` in sync (no wire change), single alembic head. **26 mutations
applied one at a time; 25 caught, 1 equivalent mutant** (raising instead of logging inside `_push` — the
per-row handler catches it after the delivery is already recorded, which is the ordering property that
mutation 12 pins directly).

| Piece | Where |
| --- | --- |
| One definition of quiet hours | `src/shared/time/quiet_hours.py`, read by both the notification path and the agenda |
| The learner's own day | `learner_timezone.local_day_bounds` |
| `Notification.pushedAt` | migration `054_notification_push` |
| Deferral instead of destruction | `notification_service.create_notification` |
| Delivering held-back rows | `repository.list_due_for_delivery`, `notification_service.deliver_pending` |
| Push, honestly | `notification_service._push`, `_push_allowed` |
| Named limits | `PRIORITY_TIME_CRITICAL = 1`, `DEFAULT_MAX_DAILY = 5`, `MAX_DEFERRAL_DAYS = 3`, `DELIVERY_BATCH = 200` |

**A fourth defect, found while implementing, and worse than the three in §3**

**Every notification quiet hours ever deferred was never delivered by anything.**
`create_notification` wrote the row with `status="QUEUED"` and a later `scheduledAt`;
`list_pending_for_delivery` selected `status == "PENDING"` only. So the row existed, had a delivery time,
and no code path ever looked at it again. It still appeared in the learner's in-app list, because that read
filters on `READ`/`DISMISSED` rather than on delivery — which is exactly why this survived unnoticed. Fixed
by selecting both statuses.

**Decisions made while building**

1. **The cap defers; it no longer destroys.** Over the allowance, the notification is written `QUEUED` and
   released at the start of the learner's next day. This is the substantive change: the plan asked for a
   priority bypass, and a bypass alone would still have thrown away everything below the threshold. Nothing
   is discarded now, so the allowance protects attention without costing information.
2. **`create_notification` always returns the row.** There is no suppression path left that destroys a
   notification, so callers no longer have to read `None` as "may or may not have happened". The four
   places that documented that behaviour, and three tests that asserted it, were corrected — the belief was
   also *half wrong* before, since quiet hours never returned `None`, only the cap did.
3. **A priority threshold over the existing numbers would have been wrong.** Live priorities are 2–4, and
   the deadline messages the plan wants protected sat at 3 alongside the daily plan — while engagement
   nudges and celebrations sat at 2. Exempting "priority ≤ 3" would have exempted the recommendation
   traffic the cap exists for. Instead `PRIORITY_TIME_CRITICAL = 1` is a new band, and exactly one message
   was moved into it: `goal_at_risk`, the only one whose value expires with the deadline it describes.
4. **Quiet hours hold even a time-critical message.** A deadline hours away does not justify waking
   someone, and nothing on this path is urgent on the scale that would. The cap is about attention;
   quiet hours are about sleep.
5. **Held-back notifications expire after three days** rather than arriving stale. "Your exam is in two
   days" landing after the exam is worse than silence, because the learner acts on it. This also bounds the
   first run after deployment, when the orphaned `QUEUED` backlog is finally selected.
6. **The delivery sweep marks a row delivered *before* pushing.** The status write is what stops the row
   being selected again, so a crash between the two loses a push rather than repeating one — the right way
   round for something that buzzes a phone in a pocket.
7. **`deliveredAt` and `pushedAt` are separate events.** `deliveredAt` means released into the in-app list,
   which always works. `pushedAt` is written only when a device actually received something, so
   `no_tokens` and an unconfigured Firebase are never recorded as deliveries.
8. **The dormant push preferences are now honoured.** `UserPreferences.notifications`,
   `pushScheduleReminder` and `pushStudyTips` have existed unread for the whole life of the schema;
   sending push without consulting them would have turned a dormant column into a broken promise. A type no
   toggle plainly describes is allowed rather than mapped onto the nearest-sounding one, which would be
   reading consent into an answer the learner never gave.
9. **`parse_hhmm` fails open, `_push_allowed` fails closed.** Opposite directions, deliberately. A corrupt
   quiet-hours string means a message at a bad hour — visible and complainable; the alternative silences
   every notification for that learner with nothing reporting it. An unreadable preferences row means no
   push — which costs the learner nothing, because the notification is already in their list.
10. **`enforce_daily_limit` was deleted.** A second, uncalled copy of the cap with its own `or 5`, and a
    limit that lives in two places is a limit that can disagree with itself.

**Still open**

- **Push reaches nobody.** Nothing writes `DeviceToken` rows — there is no registration endpoint, and
  building one is client work. Every send returns `no_tokens`, `pushedAt` stays null, and the data says so
  rather than claiming a delivery. The in-app list is the only channel that works today, which means the
  original §5.4 worry stands: a notification that lands in a list the learner does not open has not been
  delivered.
- **`_compute_optimal_time` is gone rather than fixed.** It returned `now` from both branches, so it was a
  stub pretending to be a decision. `behaviour_service._compute_optimal_times` already derives local
  study-time slots and is the honest source if delivery timing is ever made real.
- **The allowance now bites for the first time.** It counts *delivered* rows, and quiet-hours rows were
  never delivered, so on any learner with quiet hours set the cap has effectively never fired. It will now.
- **Nothing records *why* a notification was held back.** The row's status says `QUEUED`, not whether that
  was quiet hours or a spent allowance. Enough for behaviour, not enough to answer "how often does the cap
  defer something" without inference.
- **No route sets quiet hours or the allowance.** `update_quiet_hours` exists in `onboarding_service` and
  has no HTTP caller, so in practice every learner still runs with no quiet hours and a cap of five. The
  local-time fix is correct and currently unexercised in production.

### 10.5 Implementation record — phase 6, backend

Shipped in `maigie/apps/backend`. Verified: **3,579 tests passing** (baseline 3,564, +15 new), `ruff check src
tests` clean, `export_openapi.py --check` in sync, single alembic head, model and migration cross-checked.
**13 mutations applied one at a time; 13 caught.**

| Piece | Where |
| --- | --- |
| `learnerResponse`, `respondedAt` | `GoalLifecycleAction`, migration `055_goal_action_answer` |
| What each answer means | `goal_lifecycle_service._STATUS_FOR_RESPONSE`, `record_answer` |
| The endpoint | `POST /progress/goals/{goal_id}/nudge-answer` |
| Reaching the question | `repository.latest_unanswered_actions`, `GoalResponse.pendingNudge` |
| Attaching the reply | `repository.find_latest_lifecycle_action`, `record_lifecycle_response` |

**Three answers, each one something the system cannot work out for itself**

- `keep_going` — they still want it. The goal is left exactly as it is.
- `set_aside` — stop chasing them. The goal is `ARCHIVED`.
- `already_done` — the work happened and the measurement missed it. The goal is `COMPLETED`. **This is the
  answer worth the most**, because it says the measurement is wrong rather than the learner, and it is the
  only signal in the system that can say so.

**Decisions made while building**

1. **`set_aside` archives; there is no new paused state** (§8 decision 5). `ARCHIVED` is the only existing
   value meaning "concluded without being achieved", which is exactly what the learner just said; it is what
   `prep_outcome_service` already does for an unmet preparation goal, so this is consistent rather than novel;
   it is reversible; and it removes the goal from the at-risk counts, which is what "stop chasing me" means in
   practice. A paused state would have been a contract change both clients must handle to express a
   distinction neither can currently render. What it would have carried — *why* the goal stopped — is on the
   action row instead, so "archived because they chose to set it aside" is distinguishable from any other
   archiving.
2. **The answer is recorded on `GoalLifecycleAction`, not through `record_intervention_outcome`** — a
   deliberate deviation from this plan's wording. That function writes to `RetentionIntervention`, whose whole
   subsystem is unreachable: `tasks/retention_check.py` is not imported by `tasks/__init__.py`, has no beat
   entry, and imports `src.workers.celery_app` rather than `src.core.celery_app`. Routing goal answers into a
   table nothing reads, to satisfy the letter of the plan, would have put the feedback loop somewhere it
   cannot be used. Reviving retention is separate work and the plan's §3 entry is updated to say so.
3. **Null is the most informative value.** It is how "we asked and heard nothing" is told apart from "we never
   asked", and the two justify completely different next moves — silence after three asks is an answer, while
   never having asked is a bug. Hence nullable rather than defaulted, and a CHECK pairing the response with
   its timestamp so a reply time without a reply cannot exist.
4. **`pendingNudge` is on the goal response.** Otherwise the only route to answering is the notification, and
   a notification can be held until morning, deferred to the next day by the allowance, or expire. The same
   argument put `AWAITING_REVIEW` on the prepare dashboard. Only the *most recent* action counts, so a
   superseded nudge is never presented as a live question.
5. **The answer is written before the goal is touched.** If archiving fails the reply is still on record —
   losing it would mean losing the only evidence about whether the ask worked, which is the entire point.
6. **Answering an unasked question is a `404`.** This route answers a nudge; changing a goal nobody asked
   about is what `PATCH /goals/{goal_id}` is for. Accepting it here would let a client record a reply to a
   nudge that never happened, which would poison the only data this table exists to collect.
7. **Re-answering replaces the answer.** A learner who says "keep going" on Monday and "set it aside" on
   Thursday has changed their mind, and the last word counts.

**Still open**

- **No client can answer.** The endpoint and the `pendingNudge` flag exist; nothing renders either. Until a
  client ships the affordance, `asked_to_confirm` remains a question into the void and the only thing a
  learner can do about it is open the goal and edit it by hand.
- **Nothing consumes the answers yet.** They accumulate for phase 7's calibration and phase 8's correlation,
  both of which are gated on volume rather than effort. Which intervention works for which learner is not yet
  computed anywhere.
- **`keep_going` does not shorten or lengthen the cooldown.** The next nudge comes seven days later either
  way. A learner who says "keep going" is arguably asking to be chased *more*, and a learner ignoring three
  asks arguably less; both would be reasonable and neither is measurable until answers exist.
- **`record_intervention_outcome` still has zero callers**, and the retention subsystem it belongs to is
  still unreachable.

### 10.6 Migrations applied — and the reason they had not been

**Six migrations shipped across five phases and none of them was applied.** `050` through `055` were
written, reviewed, committed and left on disk while every phase's code went in depending on them. The
symptom arrived as a `500` on `GET /api/v1/learning/home`:

```
column ExamPrep.reviewAskedAt does not exist
```

The ORM declares the column, so **every read of `ExamPrep` failed** — the home surface, the guidance
engine, the prepare dashboard. Applied on 2026-08-27 with
`python scripts/db_direct.py alembic upgrade head`, `049_chat_msg_grounding` → `055_goal_action_answer`.

**Why 3,579 passing tests said nothing about it.** Every test either stubs the repository or builds its
schema from the models, so the models and the tests agree with each other by construction and neither
one ever consults the database. A schema drift of exactly this kind is invisible to the entire suite,
and will be again. The only thing that catches it is running the application against the database.

**Verified**, with `scripts/check_adaptive_050_055.py` (new, read-only, run before and after on the
`check_prep_018.py` pattern):

- `alembic_version` `049_chat_msg_grounding` → `055_goal_action_answer`.
- All three tables and all seven columns present, with the declared nullability.
- **Row counts identical on both sides** — `ExamPrep` 46, `StudyPlan` 105, `Notification` 160, `Goal`
  46, `StudyPlanItem` 467. These migrations move no data, so this is the assertion that matters.
- The three new tables are empty, as a no-backfill migration requires.
- **No preparation was re-flagged**: 18 `COMPLETED`, 2 `IN_PROGRESS`, 26 `SETUP`, unchanged. `050`
  refuses to backfill precisely so that learners are not asked about exams they sat months ago.
- `reviewRemindersSent` is `NOT NULL DEFAULT 0` with **zero null rows** — correct, since nobody has
  been asked. Metadata-only on Postgres 11+, no table rewrite.
- `GoalScheduleChange_reason_check` now admits `system_extended`.
- The exact ORM call from the traceback, `list_exam_preps`, re-run against the real database with the
  real user id: succeeds. Every new repository method exercised against the live schema.

**The first-run blast radius, now measurable** — §11's first unknown, answered for this database:

| Sweep | Would act on |
| --- | --- |
| `mark_preparations_awaiting_review` (01:00) | **3** preparations |
| `redistribute_drifted_plans` (05:00) | **69** study plans, of 105 |
| `review_goal_lifecycle` (02:30) | **6** goals with a deadline already passed |

Three asks is nothing. **69 plans is two thirds of every plan in the database**, each one repacking its
pending items and sending a `study_plan_redistributed` notification. On this data that lands on very few
learners, so most of those notifications will hit the daily allowance, queue, and then **expire** under
`MAX_DEFERRAL_DAYS` rather than arriving — which is the expiry rule doing exactly the job it was added
for, and the first evidence that phase 5's deferral was worth building before phase 3's sweep ever ran
in anger. Worth knowing before this reaches a database where those 69 belong to 69 different people.

**Also observed, not fixed.** `GET /learning/prepare/dashboard` returned **200** while logging the same
`UndefinedColumnError`, because `prepare_dashboard_service._load_active_preparations` catches per-source
failures and degrades to an empty list. `home_service` has no such guard and returned `500`. The `200`
is the worse of the two: a learner was shown a dashboard with no preparations and nothing said anything
was wrong. Recorded as its own defect rather than folded into this.

**Standing correction to the way this work was sequenced.** A migration is not shipped when it is
committed. Every phase record above that says "shipped" meant "the code is merged and the suite is
green", which was true and insufficient. Applying is its own step, needs its own verification, and the
repo already knew this — the Prepare plan carries `Migration 016 is not yet applied` as a standing note
for exactly this reason, and I did not follow it.

## 11. What is not known

- ~~**No runtime measurement.**~~ **Partly answered — §10.6.** Measured against the development database once
  the migrations were applied: **6** goals with a deadline already passed, **3** preparations awaiting a
  review, and **69 of 105** study plans drifted. So the nightly pass is correct rather than urgent for goals,
  and the *plan* sweep is the one with real reach. Still unmeasured on any database with a realistic number of
  learners, where those 69 plans would belong to 69 different people rather than a handful.
- **Whether the observed-throughput extension is stable enough to schedule against.** `consistencyScore`
  and `avgSessionMinutes` only became non-NULL recently, so there may not yet be enough history to size an
  extension from. Phase 2's change log is what would reveal it, now that it exists — but it starts empty, so
  the answer is some months away.
- **Whether learners answer nudges at all.** The entire ladder past "notify" assumes a response rate that
  has never been observed, because no deadline nudge has ever been sent. Phase 6 is where that assumption
  gets tested, and phase 8 depends on it.
- **Whether learners will answer the post-exam review**, which is the assumption phases 1 and 7 rest on. It
  arrives after a possibly-bad experience, which is the worst moment to ask anyone anything. The design
  mitigates by asking once with a small reminder budget and treating a dismissal as an answer, but the
  response rate is unknown and the calibration in §6.2 is worthless below some threshold nobody can predict.
  Instrument the ask itself — asked, answered, dismissed — from day one, so the next decision has a number.
- **How long calibration takes to mean anything.** 46 preparations exist across the whole database, and most
  learners have one. Aggregate calibration may take many months of outcomes; per-learner calibration may
  never be reachable for the majority. Phase 7 should be scheduled against outcome *volume*, not a date, and
  until then readiness stays exactly as it is rather than being adjusted on thin evidence.
- `intelligence/observation/tracker.py` has five registered `@listen` handlers whose bodies are all
  `logger.debug` plus a `# Future:` comment. It is the natural home for some of phase 6, but it has never
  done anything, so treat it as an empty room rather than a foundation.
