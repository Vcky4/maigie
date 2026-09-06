# Unifying completion and progress

One definition of "done" across study plan items, lessons, topics, sections, preparations, quizzes,
reviews, schedule blocks and goals.

Written after a learner opened a study plan task, saw a dialog offering only **Mark complete**, and asked
the obvious question: where is the lesson this task refers to, and does finishing it tick this off?

Both answers are currently no. This document says why, what it would take to change, and which parts are
engineering decisions versus product ones.

---

## 1. The finding that reframes the problem

**This is not a synchronisation problem. The link does not exist yet.**

`StudyPlanItem` has a `topicId` column — `personal_learning/db_models.py:1335`, a plain `String` with
**no foreign key and no index** — and the generator never gives it a value:

- **Preparation-scoped plans** hard-code it. `study_plan_service.generate_plan` lines 126–140 append
  `{"topicId": None, "prepTopicId": t.id, …}`, and the adaptive scheduler does the same
  (`prep_plan_adaptive.py:185–212`, `topic_id=None`). So a prep plan item *does* carry a real
  `PrepTopic` id. That one is usable today.
- **Goal-scoped plans** have no id of any kind. `_generate_topics_from_goal`
  (`study_plan_service.py:1334–1440`) returns `{"title", "estimatedMinutes", "phase", "type"}` straight
  from an LLM. The model is not told the learner's existing topics exist. `courseIds` from the wizard are
  validated and linked to the **plan** (`repo.link_plan_courses`, line 236) but their modules and topics
  are never walked.

So for a goal plan there is no lesson row to link to, and no id was available-but-discarded — it was
never known. Any "go to this lesson" affordance requires changing **generation**, not the reader.

And the second half of the learner's question has a worse answer than "no". There are **three independent
completion ledgers with no rollup between them**:

| Ledger | Written by | Rolls up to |
| --- | --- | --- |
| `TopicSection.completed` | `knowledge/routes.py` section-complete route | **nothing** — the route's own docstring says finishing the last section does not complete the topic |
| `Topic.completed` / `completedAt` | `course_service.toggle_topic_completion`, via `update_topic` at line 449 | `Course.progress` only, via `recount_course_progress` (`knowledge/repository.py:217`) |
| `StudyPlanItem.status` / `completedAt` | `study_plan_service.set_item_status` — **the only writer anywhere in `src`** | `StudyPlan.completedItems`, via `recount_plan_progress` |

Nothing outside the study-plan service ever writes `StudyPlanItem.status`. The backend states the
consequence at its own write site: the activity-feed entry for a completed item records the *plan*, with
the comment *"an item has no page of its own, so routing to it would be a link to nowhere."*

## 2. What already exists, and should be copied rather than rebuilt

Two parts of this problem are already solved well, and the plan below follows them.

**Goals are already unified on read.** `goal_metrics.derived_progress`
(`progress/services/goal_metrics.py:451`) is
documented as "the one definition", and `derive_current_values` measures every goal in one query **per
metric kind**, not per goal. `Goal.progress` is a stored column that nothing meaningfully writes and every
reader now bypasses — the docstring records that before this, "the first four derived goals in the
database measure `currentValue=4.0` against `progress=0.0`". This is the shape to reproduce for
completion.

**`agenda_service` is the composition precedent.** Its module docstring is the design argument, and it
applies here almost word for word:

> **Composed on read, never materialised.** Every source keeps its own store and this reads across them,
> tagging each entry with `source`. The alternative — have every planner also write a `ScheduleBlock` —
> needs every writer to remember, which is exactly how `ActivityFeedEntry` ended up with `entityType`
> columns no writer populates.

Structurally reusable: per-source `_read_*` functions returning one uniform tagged record, namespaced ids
(`"plan_item:…"`, `"review:…"`), reads run **sequentially rather than gathered** (fan-out exhausted the
session-mode pooler and caused intermittent 500s), and each source wrapped in its own `try/except → []` so
one failure costs one source. Not reusable: `AgendaEntry`'s scheduling semantics (`timed`, `placement`,
`window`, `_Pending.build`) — a completion view wants a sibling dataclass, not that one.

**One link is already done.** Migration 048 added `StudyPlanItem.scheduleBlockId`, so plan ↔ schedule is
connected, and `agenda_service._plan_links_by_block` already recovers a plan from a block.

**The recount pattern is established.** `recount_course_progress` and `recount_plan_progress` both
recompute from the rows rather than incrementing, and both docstrings explain why: an increment cannot
express uncompleting or skipping, and it drifts the moment the underlying set changes. Anything added here
follows that.

## 3. What is broken or absent

- **Three stored-derived caches that can drift:** `Course.progress`, `StudyPlan.completedItems`/
  `totalItems`, and `Goal.progress` — the last barely written at all. `course_progress` goals derive from
  the *cached* `Course.progress`, so a drift there propagates into goal progress and into every nightly
  `GoalProgressSnapshot`.
- **`TopicSection.completed` rolls up nowhere.**
- **Two independent spaced-repetition schedules.** `ReviewItem` schedules a return to a whole topic;
  `Flashcard.nextReviewAt` schedules a card. Both are correct and they schedule different things.
- **Two dead columns:** `Module.completed` and the whole `ScheduleBehaviourLog` table have no writer
  anywhere.
- **No `Goal` ↔ `StudyPlan` link.** `Goal` has four optional FKs (`courseId`, `topicId`, `spaceId`,
  `prepId`) and no plan link. There is no `linkedTo` column; the web mock's field maps onto those four. A
  "goal plan" is `ScheduleBlock` rows written destructively by
  `planning_impl.regenerate_goal_plan` — it calls `delete_blocks_for_goal` and rewrites.

### The event bus cannot carry this

Worth stating plainly, because it is the obvious first idea and it is wrong.

`shared/events/bus.py` is an in-process, at-most-once notifier. `_safe_dispatch` (bus.py:79) catches
**every** exception and logs it; the emitter never learns. There is no outbox, no retry, no persistence, no
ordering, no dedupe. Emission happens *after* the writes commit — `toggle_topic_completion` commits the
topic and the recount, then awaits `emit_topic_completed` at line 461 — so a handler failure loses the
effect permanently with the source data already changed.

Two further facts settle it:

- **`topic.uncompleted` has zero listeners.** An event-driven rollup would be one-directional by
  construction, and one-directional is how ledgers diverge.
- **`emit_study_plan_item_completed` exists and has no callers.** `PersonalLearningEvents`' own docstring
  records that apart from `MILESTONE_REACHED`, none of its nine emitters is reached from anywhere.

The bus is fine for what it does now — seeding a first review, raising a notification. Authoritative
completion rollup goes **inline, in the request, in the recount style** the repo already uses.

## 4. Decisions needed before implementation

These are product calls. Each one changes the design, and getting them in writing first is most of what
makes the estimate below hold.

1. **Does completing a lesson complete the plan item?** Recommend **yes** — it is the question the learner
   asked.
2. **Does un-completing the lesson reverse it?** Recommend **yes**. Asymmetry here is precisely how three
   ledgers became three ledgers, and `uncomplete_item` already exists for the manual path.
3. **Does finishing every section complete the topic?** The code deliberately says no today, in two
   places. Changing it is a real behaviour change, not a bug fix, and it should be decided rather than
   drifted into.
4. **Does completing a plan item complete the lesson?** Recommend **no**. The plan is a schedule over the
   work, not the work. Making this bidirectional means ticking a task claims you read the lesson.
5. **Retro, or new plans only?** Existing goal-plan items are model-invented titles with no counterpart
   row. Title matching is the only retro option and **should be refused**: it is fuzzy, silent when wrong,
   and wrong in the direction of crediting work nobody did. Migration 046 set the precedent —
   *"Marking historical blocks complete because their date has passed would assert attendance nobody
   recorded."*

## 5. Phases

| | Work | Days | Risk |
| --- | --- | --- | --- |
| **A** | **Make the link exist** | 2–3 | Medium |
| **B** | **One read-side definition of done** | 3–4 | **Low** |
| **C** | **Rollup on write** | 3–5 | Medium |
| **D** | **Retire the caches** | 2–3 | Medium |
| **E** | The two review systems | — | **Out of scope** |

Backend total ≈ **10–15 engineer-days**. Web and mobile each need ~3–5 days on top. Call it **4–6 weeks
elapsed for one person**, most of it review and the decisions in §4 rather than typing. Sizing assumes one
engineer familiar with these domains; it is engineer-days of focused work, not calendar estimates.

### Phase A — make the link exist

The prerequisite for everything a learner would notice.

- When `courseIds` are supplied to `generate_plan`, walk those courses' modules and topics and either use
  them as items directly or give them to the model to **select and order** rather than invent. Persist
  `topicId` on each item.
- `StudyPlanItemCreate` and `StudyPlanItemUpdate` currently expose no topic id (`models.py:1851–1868`), so
  a client cannot back-fill the link either. Decide whether create should accept one.
- Add the missing FK and index on `StudyPlanItem.topicId` — it is a bare `String` today, so a deleted topic
  leaves an id that resolves to nothing.
- Prep plans need no change: `prepTopicId` is already persisted.

**Only helps plans created afterwards** (per decision 5). State that in the release note rather than
letting it be discovered.

### Phase B — one read-side definition of done

Do this first even though it fixes nothing visible.

A `completion_service.get_completions(user_id, window)` on the `agenda_service` pattern: per-source
`_read_*` over `StudyPlanItem`, `Topic`, `TopicSection`, `ScheduleBlock.completedAt`, `QuizSession`,
`FlashcardReview`, `GoalMilestone.achievedAt`, each returning one uniform record tagged with its source and
carrying a namespaced id.

**No writes, no migration, fully reversible.** Its value is diagnostic: it makes the divergence between the
three ledgers *measurable per learner* before anything changes. Nobody currently knows the size of the
disagreement, and Phases C and D are far easier to review with that number in hand.

Reuse the parts of `agenda_service` that transfer — sequential reads, per-source isolation, learner-timezone
normalisation via `shared/time` — and write a sibling dataclass rather than borrowing `AgendaEntry`.

### Phase C — rollup on write

- `sync_plan_items_for_topic(user_id, topic_id, completed)`, called **inline** from
  `toggle_topic_completion`, both directions, followed by `recount_plan_progress`. Not via the bus, for
  the reasons in §3.
- Scope the update by plan **and** learner, matching the ownership fix already recorded in
  `set_item_status`: that function used to verify the plan belonged to the learner and then update the
  item by id alone.
- If decision 3 is yes, add the `TopicSection` → `Topic` rollup, and make it recount-style so reopening a
  section reopens the topic.
- The uncomplete path is where the real work is. A plan whose items were completed by rollup and then
  reopened must return to `ACTIVE` — `set_item_status` already expresses this correctly for the manual
  path and is the model to follow.

### Phase D — retire the caches

- **`Goal.progress` should be deleted, not synced.** `derived_progress` already ignores it; it exists only
  to disagree. That means dropping the column, removing the write in `goal_service.record_progress`, and
  retiring or repurposing `POST /progress/goals/{id}/progress`. Manual goals keep `currentValue`, which is
  the field that actually holds a learner's own figure.
- Keep `Course.progress` and `StudyPlan.completedItems` — they are read cheaply from outside their domains
  and the recount discipline is sound. Add a reconciliation script (precedent:
  `scripts/backfill_daily_snapshots.py`) so drift is detectable rather than theoretical.
- Delete `Module.completed` and `ScheduleBehaviourLog`, or document them as intentionally unwritten.
  Migration 046 already argues that a column nothing writes is not unused, it is wrong.

### Phase E — the two review systems, out of scope

`ReviewItem` and `Flashcard` schedule different things, both are correct, and merging them is a larger
piece with no payoff for this problem. Named here so it is a decision rather than an oversight.

## 6. Migrations

Alembic, revisions in `apps/backend/alembic/versions/`, linear chain, **latest is
`048_plan_item_block_link`**. Revision ids are slugs, not hashes.

**Keep new revision ids to 32 characters or fewer.** `alembic_version.version_num` is `varchar(32)`; a
33-character id applies the DDL, fails the version bump, rolls the whole transaction back, and reports a
`StringDataRightTruncationError` about a value nobody wrote. Documented in the docstring of
`046_schedule_block_completion.py`, which hit it — the longest id in the tree is exactly 32.

Backfill template: **`037_add_deck_origin.py`** — `add_column`, then a CTE with `ROW_NUMBER()` deriving
values from legacy columns with documented precedence and deliberate null-on-ambiguity, then the unique
index created *after* the backfill so a bad backfill fails loudly.

Counter-precedent to respect: **`046` ships no backfill on purpose.** Deriving from `Topic.completedAt` or
`StudyPlanItem.completedAt` is acceptable — dated evidence exists. Synthesising `ScheduleBlock` or
`TopicSection` history is not.

## 7. Recommended first slice

If only one week is available, this is the highest-value cut and it delivers exactly what the learner
asked for:

1. **Phase A**, so goal-plan items carry a real `topicId`.
2. **Phase C limited to topic → plan item**, both directions.
3. **Prep-plan routing, which works today** — `prepTopicId` is persisted and the plan carries `prepId`, so
   the dialog can already open the preparation's topic with no backend change at all.

Then Phase B when there is room, because it is what makes Phases C and D reviewable.

## 8. Client work

Both clients need the same two changes, and neither should ship before Phase A:

- A destination on the plan-item dialog, from `topicId` (new plans) or `prepTopicId` + `prepId` (prep
  plans), and **no destination at all** when neither resolves. `null` is a real answer: mobile has just
  paid twice for the alternative — a feed row pointed at a deck screen with a card id and rendered
  "Deck unavailable (404)".
- Copy that states the relationship honestly. Until Phase C ships, the dialog must not imply that
  finishing the lesson ticks the item off, because it does not.

Mobile's route resolvers (`features/schedule/agendaRoutes.ts`, `features/growth/mapGrowth.ts`) already
follow the rule this needs — a route is offered only where the stored id is the id the target screen
resolves — so the change there is additive.

## 9. What is not known

- **No runtime measurement was taken.** Every claim above comes from reading the source; nothing was run
  against a database. The *size* of the divergence between the three ledgers is unmeasured, which is the
  main argument for doing Phase B first.
- Whether the LLM will reliably select from a supplied topic list rather than inventing titles is a prompt
  question, and the 2–3 day estimate for Phase A includes iteration but not a guarantee.
- Section → topic rollup (decision 3) may have consequences for `Course.progress` and therefore for
  `course_progress` goals and every `GoalProgressSnapshot` written afterwards. Worth checking before
  committing to it.
