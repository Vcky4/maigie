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

**Rescheduling only fires when the learner is active.** `_redistribute_plan` has exactly two triggers:
`update_study_plan` when the schedule inputs change (`:650`), and — after a learner marks an item complete
— `if len(pending_past_due) > 2` (`:952`). **A learner who goes silent gets no redistribution at all**,
which inverts the need: the learners whose plan has drifted furthest are the ones not completing anything.

**Nothing ever learns whether an intervention worked.**
`retention_service.record_intervention_outcome` (`:213`) sets `outcome` and `outcome_at` and has **zero
callers** anywhere in `src` or `tests`. The `RetentionIntervention` table, the 7-day cooldown and the 0.7
churn threshold all exist; the feedback loop does not. And the whole stack is unreachable regardless:
`tasks/retention_check.py` is not imported by `tasks/__init__.py`, has no beat entry, and imports
`src.workers.celery_app` rather than `src.core.celery_app`.

**The one nudge that does run is not goal-aware.** `learning.check_declining_engagement` (every 6h) reads
`dropout_risk > 0.5` and sends one static message pointing at flashcards. It never mentions a deadline, a
goal or a plan.

**Notification delivery has three traps** worth knowing before hanging anything on it:

- The daily cap **silently drops**: `create_notification` returns `None` once
  `count_today_delivered >= max_daily_notifications` (default 5).
- Quiet hours are compared against the **naive UTC clock**, not the learner's local time.
- `_compute_optimal_time` is a stub that returns `now`, and `deliver_pending` marks rows delivered with a
  comment reading `# Deliver the notification (push/email would go here)`. **There is no push on the
  learning path** — push infrastructure exists but is only used by credit purchases.

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

**Phases 0 and 1 are implemented on the backend.** See §10 for what shipped, what changed against this
plan, and what is still open.

| | Work | Days | Risk |
| --- | --- | --- | --- |
| **0** | ~~**Stop declaring missed exams complete.**~~ **Shipped.** | 1 | **Low, high value** |
| **1** | **The post-exam review** (§6). **Backend shipped**; both clients' forms outstanding. | 5–7 | Medium — new schema and new copy on a sensitive moment |
| **2** | **Derive date authority** + a `GoalScheduleChange` log + surface "extended N times". No behaviour change yet. | 2 | Low |
| **3** | **Time-triggered redistribution.** Make `_redistribute_plan` reachable without learner action, which is the actual gap. | 1–2 | Low |
| **4** | **The nightly pass**, ladder implemented, one action per goal, cooldown enforced. Reuses existing metrics and `_redistribute_plan`. | 4–5 | Medium |
| **5** | **Notification path fixes** — learner-local quiet hours, priority bypass of the daily cap, push on the learning path. | 3–4 | Medium — touches every notification |
| **6** | **The learner's answer to a nudge, stored.** Deprioritise / completed / keep-going, wired to `record_intervention_outcome`. Needs client work. | 3 | Medium |
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

Still open:

1. **How many times may a `learner`-authority goal extend before the system asks instead?**
2. **What state is "awaiting your answer"?** `Goal.status` is `ACTIVE | COMPLETED | ARCHIVED | CANCELLED`
   (`progress/models.py:37`) and `ExamPrep.status` is `SETUP | IN_PROGRESS | COMPLETED`
   (`personal_learning/models.py:957`). Neither has a value
   for it, and adding one is a contract change for both clients. Alternative: keep `ACTIVE` and let the
   *absence of an outcome row* be the state, which adds no enum value but makes every reader do a join.
3. **What terminal state does an unanswered preparation reach?** It must not be `COMPLETED`. It also should
   not stay `ACTIVE` forever, or the learner is nagged by their own history.
4. **What scale for the two ratings?** Recommend the same shape for both so they can be compared, and an
   odd number of points so "about as expected" is expressible.
5. **Is "deprioritise" `ARCHIVED`, or a new paused state?** `StudyPlan.PAUSED` is the precedent, and its
   docstring argues a pause "is not a statement about the deadline".
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

## 11. What is not known

- **No runtime measurement.** Everything here comes from reading the source. In particular the *number* of
  learners currently sitting on overdue goals is unmeasured, and it determines whether phase 2 is urgent or
  merely correct. `is_overdue` is pure and already written — it can be run over the goal table as a script
  before committing to any of this.
- **Whether the observed-throughput extension is stable enough to schedule against.** `consistencyScore`
  and `avgSessionMinutes` only became non-NULL recently, so there may not yet be enough history to size an
  extension from. Phase 1's change log is what would reveal it.
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
