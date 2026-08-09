# Learning Intelligence for Practice — Design

> Status: Phase A implemented (migration `012`, applied). Phases B–E proposed.
> Scope: what the system learns from a learner's practice, and what it does with it.
> Companion: [`PREPARE_API_INTEGRATION_PLAN.md`](../../../../maigie-client/docs/PREPARE_API_INTEGRATION_PLAN.md) (Phases 1–4b, now applied)

## 1. Why this document exists

Phases 1–4b made the Prepare surface **honest**: every number traces to a persisted row, answer keys cannot be read early, scores cannot be inflated. That was necessary and it is not sufficient. An honest dashboard is still a dashboard.

The book is unusually direct about this being the wrong destination:

> Not a dashboard. / Not a report. / A living capacity to grow.
> — `content/intelligence/ch26-institutional-intelligence.mdx`

> A dashboard shows information. / A Home welcomes you back.
> — `content/platform/ch07-personal-learning.mdx`

> If a feature simply stores information without increasing intelligence, it should be questioned. / Storage is necessary. / Intelligence creates value.
> — `content/philosophy/ch04-product-principles.mdx`

So this document covers the part that is currently missing: the system observing a learner, forming an understanding that improves, and changing what it does next.

## 2. What the book already decided for us

Read before designing. These are constraints, not inspiration.

**The architecture is given.** `ch11-learning-intelligence.mdx` names four stages — Observe, Reason, Recommend, Orchestrate. This design follows that spine rather than inventing one.

**The target behaviour is given, precisely.** This is the single most useful passage in the book for our purposes:

> Most systems remember: *Victor struggles with Design Patterns.* / That's descriptive. / Maigie should go further: *Based on everything we've learned about Victor, learners like Victor, this Classroom, and this Course, the best next step is a 20-minute collaborative revision session followed by three active recall questions tomorrow morning.* / That's not remembering. / That's using memory to shape the future.
> — `content/platform/ch13-memory.mdx`, "Prescriptive, Not Just Descriptive"

"Your weakest topic is Hypothesis Testing (62%)" is the descriptive version. It is what we ship today.

**Adaptive difficulty is explicitly mandated**, and the trigger is named:

> That adjusts difficulty **before frustration builds**.
> — `content/intelligence/ch27-towards-autonomous-learning.mdx`

**A single metric is explicitly rejected:**

> It must resist the temptation to optimise toward a single metric. / It must embrace nuance.
> — `content/intelligence/ch24-reasoning.mdx`

**Memory must be selective, decaying, and revisable:**

> A bad day is not a pattern. / A single mistake is not a weakness. … It should remember patterns. / It should forget noise. / It should weight recent experience appropriately. / It should never trap someone in their past.
> — `content/intelligence/ch23-memory.mdx`

> Earlier observations should be revisited in light of new evidence. … Memory that does not evolve becomes prejudice. / Memory that evolves becomes wisdom.
> — same chapter

**Readiness specifically is treated with suspicion.** The only two mentions in the entire book are warnings:

> It will misjudge readiness.
> The learner knows their own readiness.
> — `ch24-reasoning.mdx`

**And hints are a pedagogical instrument, not a penalty:**

> An answer given too quickly prevents learning. / A hint at the right moment creates breakthrough. / Intelligence must know when to speak and when to wait.
> — `content/intelligence/ch22-the-nature-of-intelligence.mdx`

## 3. Three things in the current design that the book rules out

Worth stating plainly, because they are not cosmetic.

**3.1 `ADAPTIVE` mode does not adapt. It is a name with no implementation.** `quiz_engine.start_quiz` branches on `FULL_PRACTICE`, `WEAK_AREAS`, and `TOPIC_FOCUS`; everything else falls through to `all_topics[:5]` — the free quick-review path. `ADAPTIVE` and `PAST_PAPER_SIM` are entitlement-gated and billed as Plus features, and behave identically to the free tier. Meanwhile `conversion_engine`, `value_summary_service`, `trial_service`, and `retention_service` all advertise behaviour that does not exist ("adjust difficulty based on your performance", "focuses questions on your weak areas until you master them"). `study_plan_service` computes `is_adaptive` and uses it only to record feature usage.

This is the most urgent item in this document, and it is a trust issue before it is an engineering one.

**3.2 Mastery is a lifetime average, which the book forbids twice over.** `_update_topic_mastery` computes `correct / total` across *every answer ever recorded* for a topic. So:

- A learner who was wrong ten times last month and right ten times today reads 50% — identical to someone answering at random. That is precisely "trap[ping] someone in their past" and the opposite of "weight recent experience appropriately".
- A topic with one answered question reads 0% or 100%. "A single mistake is not a weakness" — but here it is the entire model.

**3.3 Evidence is destroyed as it arrives.** Mastery is a mutable float. When it updates, the reasoning behind it is gone. That makes "earlier observations should be revisited in light of new evidence" impossible: there are no earlier observations, only a number. Phase 4b.5 added daily snapshots, which preserves the *output* — this design needs the *input* preserved too.

## 4. Design

### 4.1 Observe — keep the evidence, not just the verdict

One append-only row per answered question. This is the substrate everything else derives from, and it is what lets a conclusion change its mind later.

```text
PracticeObservation                    (append-only; never updated)
  id
  userId        -> User
  prepId        -> ExamPrep
  prepTopicId   -> PrepTopic (nullable, as attribution can fail)
  prepQuestionId-> PrepQuestion
  quizSessionId -> QuizSession
  isCorrect      bool
  responseMs     int | null     -- time on this question
  hintUsed       bool
  hintCount      int            -- hints are levelled; see 4.4
  difficulty     str | null     -- copied at answer time, not joined later
  observedAt     timestamptz
```

Two deliberate choices. `difficulty` is **copied, not referenced**, because a question's difficulty may be recalibrated later and an observation must record what was true when it happened. And nothing here is nullable-by-accident: `responseMs` is null when the client did not report it, which must stay distinguishable from "answered instantly".

`QuizAnswer` already holds correctness and `timeTakenSeconds`. Rather than duplicate, this may be an extension of `QuizAnswer` plus the new columns — decided at implementation, on whether observations need to outlive session deletion. **They probably do**: deleting a practice session should not erase what the system learned about the learner.

### 4.2 Reason — a competence estimate with several dimensions, and an honest confidence

Replace the single `mastery_score` float with a derived estimate per `(learner, topic)`. Not stored as a truth; recomputed from observations, cached.

Four dimensions, because the book forbids collapsing to one:

| Dimension | Question it answers | Derived from |
| --- | --- | --- |
| `retention` | Do they still know it? | recency-weighted correctness |
| `fluency` | How effortfully? | response time relative to their own baseline |
| `independence` | Can they do it unaided? | hint usage rate |
| `reliability` | Is it consistent, or luck? | variance across observations |

And a fifth number that is *not* a dimension of skill but governs whether we say anything at all:

**`evidence` — how much we actually know.** Below a threshold, the system does not report a percentage. It says *"not enough practice yet to tell"*. This is the direct implementation of "a single mistake is not a weakness", and it replaces today's behaviour of asserting 0% from one wrong answer.

**Recency weighting.** Each observation's weight decays with age (exponential, half-life on the order of two to three weeks, calibrated later). A learner who has improved should read as improved. This is not a refinement; it is the difference between memory and a grudge.

**Difficulty weighting.** A correct answer on a `HARD` question is stronger evidence than on an `EASY` one. This is why 4b.2's `difficulty` column matters beyond a UI badge.

**Hint discount.** A hinted-correct answer is genuine evidence — of *assisted* competence. It raises `retention` less than an unaided correct answer and lowers `independence`. It is never treated as a wrong answer, because it isn't one.

**Latency, carefully.** Response time separates "knows it" from "worked it out", which are different states needing different next steps. Two cautions, both mine rather than the book's: it must be normalised against *the learner's own* baseline (people read at different speeds, and a slow reader is not a weak learner), and it must never be surfaced as a judgement. "You were slow" is not something this product should ever say.

### 4.3 Recommend — a next step, in words, with its reason available

The output is not a number. It is the smallest useful action, plus the evidence that produced it, because the learner is entitled to ask why:

> Learners should understand what information is remembered and why.
> — `content/platform/ch13-memory.mdx`

Shape:

```text
{
  headline:  "Hypothesis testing has slipped since last week."
  action:    "Ten minutes of recall on it now would help more than anything else."
  because:   ["3 of your last 4 answers needed a hint",
              "you last practised it 9 days ago"]
  confidence: "tentative" | "reasonable" | "confident"
  dismissible: true
}
```

`because` is mandatory, not decorative. A recommendation that cannot explain itself does not ship. `confidence` is expressed in words, not a percentage — the book uses confidence as a learner emotion throughout and never as a system-side probability, and "97% confident" would be pretending to a precision we do not have.

### 4.4 Hints — the learner asks, the system answers, and it costs them nothing punitive

Per the user's direction and `ch22`, a hint is available **on request**, never pushed.

A hint is a **distinct artifact, deliberately weaker than the explanation.** This matters more than it looks: given that Phase 4 withholds answer keys until a learner answers, a "hint" that paraphrases the explanation is an answer key with a different label, and every score becomes meaningless again. Same hole, third door.

Two levels:

1. **Nudge** — points at the approach or the relevant concept. Never eliminates options, never restates the answer.
2. **Narrow** — for multiple choice, removes one clearly wrong option. Still a real choice.

Both generated at question creation, stored on `PrepQuestion`, and validated the way 4b.2 validates the rest: a hint containing the correct answer text is rejected rather than stored.

**Scoring:** a hinted-correct answer counts as correct for the session score — they did answer — and is discounted in the competence model. Anything else invites the failure mode already fixed once: farm hints, inflate readiness.

**Framing, which the book is firm about:**

> Behaviour is not judged. / It is understood.
> — `content/platform/ch14-behaviour.mdx`

So hint usage is never shown as a demerit. It is genuinely the most useful signal we have, because a question that needed a hint sits exactly at the edge of what the learner can do — which is where the next question should be aimed.

### 4.5 Orchestrate — aim at the frontier, not at "easier" or "harder"

The book leaves the hardest question open on purpose:

> How long should I let them struggle? / When does productive struggle become unproductive frustration? / These are questions of judgment. / **They have no formula.**
> — `ch24-reasoning.mdx`

It also says learning should be "not without struggle" (`ch27`) *and* that difficulty should adjust "before frustration builds" (`ch27`). Those pull in opposite directions and the book does not resolve them.

**Proposed resolution:** the system targets neither easy nor hard but the learner's **frontier** — selecting questions where the model expects roughly a 70–80% chance of success. That is high enough to sustain momentum and low enough to be real practice. It satisfies both instructions because it is not a fixed line: as competence rises the frontier rises with it, so difficulty increases without anyone deciding to make things harder.

Frustration gets its own, separate treatment, because the book keys adaptation to frustration specifically rather than to error count. Signals, within a session: rising response times, consecutive incorrect answers, hint rate climbing, and abandonment. When they trend together the session eases toward consolidation — a topic already strong — rather than continuing to push. That is "before frustration builds", acted on within the session rather than reported afterwards.

This is what makes `ADAPTIVE` mean something. And per `ch25`, agency comes with obligations: the learner can always override the selection, and the mode must be able to say what it is doing and why.

### 4.6 Correctable, and forgettable

> That it can be corrected. / That it can be forgotten when asked. … Without it, memory becomes surveillance.
> — `ch23-memory.mdx`

The book gives the principle and no surface for it, so this is a design decision: a learner can **reset a topic's model**, which archives the observations rather than deleting the history, and returns the topic to "not enough evidence yet". And they can disagree with a readiness figure without arguing with it — "the learner knows their own readiness" (`ch24`) means a stated self-assessment should be recorded alongside the derived one, not overwritten by it, and shown when the two diverge. A system that tells someone they are ready when they know they are not has failed them, and the reverse is worse.

## 5. What is mine, not the book's

Flagged so it can be challenged rather than mistaken for doctrine.

| Decision | Book's position |
| --- | --- |
| Using response time at all | **Silent.** And "Learning is not measured by time" (`ch12`) cuts against it. Justified as a fluency signal, never a score, never surfaced as judgement |
| Hint usage as a measured signal | **Silent.** Hints appear once, as a teaching act. Recording them is my inference from the user's direction |
| Four dimensions, and which four | **Silent** on mechanics. Only "resist… a single metric" constrains it |
| 70–80% target success rate | **Silent.** "No formula", explicitly |
| Decay half-life | **Silent.** Only "weight recent experience appropriately" |
| Evidence threshold before reporting | Inferred from "a single mistake is not a weakness" |
| Frontier-targeting as the resolution of struggle-vs-frustration | The book supplies the tension and declines to resolve it. This is my resolution |

There is **no exam-preparation chapter** in the book, and readiness is mentioned only twice, both as cautions. Anything we assert about exam readiness goes beyond the book and should be held loosely, offered as a suggestion the learner can contradict.

## 6. Phasing

Ordered so each phase is useful alone and nothing is built on an unvalidated guess.

**Phase A — Observe. ✅ Done, migration `012` applied and verified.**

- `PracticeObservation` — append-only, one row per answered question, holding correctness, `responseMs`, `hintUsed`/`hintCount`, and `difficulty` copied at answer time. **`quizSessionId` and `prepQuestionId` are `SET NULL`, not `CASCADE`**, which is the entire reason it is not just extra columns on `QuizAnswer`: deleting a practice session must not erase what the system learned. It does cascade from `User` and `ExamPrep`, because deleting an account or a preparation is a request to forget.
- Existing answers were **backfilled** as observations — 5 of 5 — because they are real evidence and the model is better off knowing about them.
- `PrepQuestion.hintNudge` plus a **leak check**: a generated hint containing the correct answer is discarded rather than stored. Asking a model not to reveal the answer is not the same as it obeying, and a hint that paraphrases the explanation is the answer key with a different label — the same hole closed twice already, through a third door.
- `POST /quizzes/{id}/questions/{qid}/hint?level=1|2`. Level 1 nudges; level 2 also eliminates one wrong option, chosen **deterministically** so repeated taps cannot work through every option and reveal the answer. Refused after the question is answered — the key is already out, and allowing it would let hint counts be run up after the fact, corrupting the signal rather than recording it.
- `hintsUsed` is exposed per question, so a resumed session does not offer a hint the learner effectively already had.
- Observation writing is **best-effort**: if it fails the answer still stands. Losing a row of evidence is a much smaller harm than rejecting a learner's answer.

36 tests added (78 in `test_quiz_engine.py`, 53 in `test_quiz_engine_scoring.py`; suite at 386). Verified against the database: table created, columns added, backfill matches, `SET NULL` and `CASCADE` rules confirmed as designed.

**Nothing consumes these signals yet, and that is the point of stopping here.** Phase B can now be built against real observations instead of guesses.

**Phase B — Reason.** The competence estimate, replacing lifetime-average mastery. Includes the evidence threshold and "not enough practice yet" as a first-class state. `prep_readiness` becomes a consumer of this rather than the source of truth. Needs care: readiness numbers will move, and the Learn card invariant from Decision B2 must continue to hold.

**Phase C — Recommend.** The prescriptive next step with its `because`. This is where the surface stops being a dashboard.

**Phase D — Orchestrate.** `ADAPTIVE` selects by the model; frontier targeting; in-session frustration response. Fixes the false advertising in §3.1.

**Phase E — Correct and forget.** Learner self-assessment, topic reset, and the divergence surface.

**Throughout:** every phase adds tests, per Phase 4's precedent. And the LLM is a participant here but not the mechanism — the competence model is arithmetic over observations, which is inspectable and testable. Generation and explanation are LLM work; judgement about a learner should not be a black box we cannot explain when asked why.

## 7. Open questions for product

1. **Should a learner see their own competence model?** `ch12` says progress should be visible "through meaningful insight" rather than "complicated analytics", and `ch13` says learners should understand what is remembered and why. That argues yes, but in words rather than four numbers.
2. **How many hints before a question is retired from a learner's rotation?** Repeated hint-dependence on the same item may mean the item is bad, not the learner.
3. **Does `PAST_PAPER_SIM` get a real implementation, or is the mode withdrawn?** It is billed today and does nothing. Same problem as `ADAPTIVE`, but the fix is content, not algorithms — it needs actual past papers, which is an import pipeline rather than intelligence.
4. **Cross-learner priors.** `ch23` explicitly permits them ("It knows which explanations worked for similar learners"). Out of scope here, but the observation table is the thing that would make it possible later.
