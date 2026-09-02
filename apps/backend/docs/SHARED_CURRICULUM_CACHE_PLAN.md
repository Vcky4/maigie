# Shared Curriculum Cache — Design Plan

> Status: **Design only. Nothing is built and nothing should be built until §3's blocker is resolved.**
>
> **The claim.** Generated study content — quiz questions, lesson bodies, course outlines, topic breakdowns, flashcards — is produced **per learner, per request** today. For standardised national exams it is near-identical across thousands of learners. JAMB, WAEC and NECO are single national syllabi: every JAMB Physics candidate studies the same topics for the same paper. Generating "10 multiple-choice questions on Newton's Laws of Motion" separately for 4 000 learners is paying 4 000 times for one artefact.
>
> **What it is worth, stated honestly and lower than the first estimate.** Cache hits move generation COGS toward zero, but generation is already *bounded* by the usage window (`MAIGIE_PLUS_COMMERCIAL_PLAN.md` §6.3), so this does not reduce a cost that is currently unbounded — it reduces the cost of serving a bounded allowance. At a 60% hit rate the saving is roughly **+30% contribution in Nigeria**, not the 2.5× an earlier estimate suggested; that figure had payment-coverage work folded into it. §7 has the arithmetic.
>
> **The larger prize is not the margin.** A cache hit returns in milliseconds where generation takes 10–30 seconds and can fan out to nine provider calls on retry. For a learner on a slow Nigerian connection, instant lesson content is a different product, not a cheaper one. Treat the cost saving as the justification and the latency as the reason anyone will notice.
>
> **The strategic point this document exists to record.** `MAIGIE_PLUS_COMMERCIAL_PLAN.md` §6.8 frames Nigeria as the difficult market that only works if the cost work lands. On this axis the opposite is true: **Nigeria is the cheapest market in the world to serve**, because curriculum standardisation is highest there. The US and UK, with curricula fragmented across states, boards and schools, are where per-learner generation is unavoidable. The launch market was chosen for reach and happens to also be structurally cheapest, and nothing in the plan had noticed.
>
> Owner: Backend
> Companion: [`MAIGIE_PLUS_COMMERCIAL_PLAN.md`](./MAIGIE_PLUS_COMMERCIAL_PLAN.md) — §6.5 (the cost surface), §6.7 (free-tier COGS), Decision L (the metering chokepoint this rides on)
> Depends on: **Phase 3b of the commercial plan** (Decision L). There is no single point where generation passes through today, so there is nowhere to put a cache. Do not start before it.
> Last reviewed: 2026-09-02 (revision 1)

## 1. What is actually shareable

This is the whole design. Everything else is mechanics.

A generated artefact is shareable if **its prompt contains nothing about the learner**. Checked against the code rather than assumed:

| Operation | Prompt inputs | Shareable? |
| --- | --- | --- |
| Quiz / question generation (`quiz_engine.py:452`) | topic titles + subject | **Yes** — the prompt is built from topic labels and asks for MCQs, hints and exam tips. Nothing learner-specific reaches it |
| Lesson body (`knowledge/routes.py:1145`) | topic title + course subject | **Yes** |
| Course outline (`knowledge/routes.py:82`, `ai_course_generation.py:41`) | title + subject + difficulty | **Yes** |
| Flashcards from topic (`flashcard_service.py:300`) | topic label | **Yes** |
| Flashcards from plan item (`:353`) | plan item title | **Yes**, probably — verify the item title is not learner-authored |
| Deck starter cards (`:404`) | deck title | **Yes** |
| **Prep topic extraction** (`exam_prep_service.py:582`) | `prep.subject` **+ `material_text`** | **No** — `material_text` is the learner's uploaded material |
| **Flashcards from note** (`flashcard_service.py:207`) | `note.title` + `note.content[:3000]` | **No** — the note is private, and the deck is one-per-note by design |
| Document generation (`document_impl.py:1242`) | learner's own content | **No** |
| Chat turn (`ask_service.py`) | memory, profile, history, knowledge base | **No**, and never |
| Growth narrative, drivers, goal insight, reflections | the learner's own measured behaviour | **No**, and never — these are the product's "it knows me" surface |

**Two lines are load-bearing here.** `exam_prep_service.py:582` interpolates `material_text` directly into the prompt, so **topic extraction is permanently uncacheable across learners** — and it is upstream of the quiz generation that *is* cacheable, which means the pipeline is contaminated at the top and clean lower down. And `flashcard_service.generate_from_note` reads `note.content`, so the single most obviously "syllabus-shaped" feature is the one that cannot be shared at all.

**The rule, and it must be enforced by types rather than by care:** a cacheable generator takes a **curriculum key** and nothing else. If a function's signature can see `user_id`, a note, a resource or a profile, it is not a cacheable generator. Do not pass `user_id` into a shared generator "for logging" — that is how the boundary erodes.

## 2. Why `narrative_cache` is not the answer, though it is the right shape

`personal_learning/services/narrative_cache.py` already implements the pattern: `fingerprint(inputs)` returns a SHA-256 over the inputs, `resolve(user_id, kind, entity_id, scope, inputs)` returns the cached row when `inputs_hash` matches, and `_store` writes it back. Reuse the mechanism and the discipline.

**But its cache key starts with `user_id`, and that is exactly what has to change.** `narrative_cache` exists to avoid regenerating *the same learner's* narrative twice. This design exists to avoid regenerating *the same syllabus content* for different learners. Same machinery, opposite key, and a shared cache has three problems a per-user cache does not:

1. **A poisoned entry harms everyone.** A wrong answer in a cached MCQ is served to every learner on that topic until someone notices. Per-user caching has a blast radius of one.
2. **There is no natural invalidation.** A narrative goes stale when the learner's data changes, which `inputs_hash` detects. Syllabus content goes stale when a syllabus changes, which nothing in the system observes.
3. **The key has to be canonical.** Two learners must produce byte-identical keys for the same topic, or the cache never hits. §3 is why this is currently impossible.

## 3. The blocker: there is no curriculum identity in the schema

**Checked in the models, and this is why the document stops at design.**

| Field | Type | Problem |
| --- | --- | --- |
| `ExamPrep.subject` | `String`, not null, **free text** | "Physics", "physics", "JAMB Physics", "Phy", "PHYSICS 101" are five cache keys for one subject |
| `ExamPrep.prep_type` | `String`, nullable | `EXAM` \| `CERTIFICATION` \| `INTERVIEW` \| … — a category, not a syllabus |
| `LearningProfile.exam_name` | `String`, nullable, **free text** | Same problem, second location, and nothing reconciles the two |
| `Course.subject` | subject label | Free text again |
| `Topic.title` | `String` | Learner- or model-authored topic names |
| **exam board** | — | **Does not exist** |
| **country / region** | — | **Does not exist** |
| **syllabus version** | — | **Does not exist** |

**A cache keyed on free text does not hit.** Worse, it hits *wrongly*: "Physics" for a JAMB candidate and "Physics" for an A-Level candidate are different syllabi at different depths, and serving one to the other is a quality failure that looks like a caching bug.

So **the first phase is not caching. It is a curriculum taxonomy**, and the cache is what the taxonomy is for. Anyone who starts with the cache will build something with a 3% hit rate and conclude the idea does not work.

## 4. The country dimension, deliberately deferred

**Per-country identification does not exist today and will be introduced when this is built.** That is a known and accepted sequencing decision, not an oversight, and the design's job is to make the later addition cheap rather than to pretend the dimension is absent.

**Therefore: `country` is in the cache key from the first migration, with a single value.** Not omitted, not added later. A key of `(country, board, subject, topic, depth, prompt_version)` where `country` is initially always `NG` costs nothing now and means introducing a second country is a data change rather than a migration plus a full cache invalidation.

The failure mode this avoids is specific and common: ship the key as `(subject, topic)`, reach a useful hit rate, then add a second country and discover **every cached entry is now ambiguous** — you cannot tell which country's syllabus produced it, so the only safe move is to discard the entire cache and lose the hit rate you spent months building. A column that is constant for a year is much cheaper than that.

**What "introducing per-country identification" will need to supply**, so it can be designed once:

- **Where country comes from.** A profile field the learner sets, not IP geolocation — a Nigerian student on a VPN must not be served the wrong syllabus, and billing already needs a territory for §5.7's regional pricing. **These should be the same field.** The commercial plan needs a territory for the NGN-only Term Pass and for `PlanItem.availability`; this needs one for the cache key. Two independent notions of "which country is this learner in" would be a fifth entitlement-resolver problem in a new domain.
- **What happens to learners who set nothing.** They must be *uncacheable*, not defaulted. A default country is a wrong syllabus served confidently.
- **Whether country or board is the real key.** Country is a proxy. The thing that determines a syllabus is the **board** — WAEC serves several countries, and a UK learner's board is AQA or Edexcel rather than "UK". Country is the coarse dimension worth keying on now; board is the correct one long-term. Both belong in the key.

## 5. The cache key

```
curriculum_key = sha256(
    canonical_json({
        "v":        1,             # prompt_version — bump to invalidate wholesale
        "country":  "NG",          # constant until per-country identification lands
        "board":    "JAMB",        # nullable; null means uncacheable, not "any"
        "subject":  "physics",     # canonical taxonomy id, NOT free text
        "topic":    "newtons-laws-of-motion",
        "artefact": "quiz_mcq_10", # what was asked for
        "depth":    "secondary",   # syllabus level
        "model":    "gemini-3.5-flash",
    })
)
```

Six properties this key needs, each for a reason:

- **`prompt_version` is first and is bumped by hand.** When a prompt changes, every entry generated by the old prompt is wrong-shaped rather than merely stale. This is the only invalidation mechanism that reliably works, and `narrative_cache`'s `inputs_hash` does not cover it because the prompt is not an input.
- **`model` is in the key.** Content generated by Flash-Lite and content generated by 3.5 Flash are not interchangeable — Decision P confines the expensive model to operations above 500 units, and quiz generation at 780 units is one of them. A cache that serves Flash-Lite output to a Plus learner silently undoes the model-quality paywall.
- **`board` nullable means uncacheable.** Never treat null as a wildcard.
- **`subject` and `topic` are taxonomy ids, never free text.** This is the §3 blocker restated as a constraint.
- **No `user_id`, no `prep_id`, no `course_id`.** If one appears, the entry is not shared and belongs in `narrative_cache` instead.
- **Canonical JSON.** Sorted keys, no whitespace, explicit encoding — `narrative_cache.fingerprint` already does this and should be reused rather than reimplemented.

## 6. Data model

Two tables. Deliberately not one.

**`CurriculumTopic`** — the taxonomy that §3 says does not exist.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | String PK | slug, e.g. `ng-jamb-physics-newtons-laws` |
| `country` | String | `NG` for now; see §4 |
| `board` | String, nullable | `JAMB` \| `WAEC` \| `NECO` \| null |
| `subject` | String | canonical subject id, indexed |
| `topic` | String | canonical topic id |
| `depth` | String | syllabus level |
| `displayName` | String | what a learner sees |
| `aliases` | JSON | the free-text strings that map here — this is how `ExamPrep.subject` is resolved |

**`GeneratedArtefact`** — the cache itself.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | String PK | |
| `curriculumKey` | String | the §5 hash, **unique** |
| `curriculumTopicId` | String FK | so a topic's whole cache can be dropped |
| `artefactKind` | String | `quiz_mcq_10` \| `lesson_body` \| `course_outline` \| `flashcards` |
| `promptVersion` | Integer | denormalised from the key for bulk invalidation |
| `model` | String | which model produced it |
| `payload` | JSON | the generated content |
| `costUnits` | Integer | what it cost to produce once — this is how the saving gets measured rather than asserted |
| `hitCount` | Integer | served count. **The single most important number in this design** |
| `qualityFlags` | Integer | count of learner reports against it |
| `reviewedAt` | DateTime, nullable | null means never human-checked |
| `createdAt` | DateTime | |

Indexes: unique on `curriculumKey`; `(curriculumTopicId, artefactKind)`; `(promptVersion)` for bulk invalidation; `(qualityFlags DESC)` for the review queue.

**`hitCount` and `costUnits` together are the business case.** `SUM(costUnits × (hitCount − 1))` is the money this saved, measured rather than modelled. If that number is small after a month, the design is wrong and should be deleted — which is the point of instrumenting it from the first commit.

## 7. What it is worth

Free COGS at 10 000 MAU is **$376** (commercial plan §6.7), of which roughly **$0.05 of the $0.08 per active free learner is metered generation** — the cacheable part. Background tasks and home guidance are already cached or gated.

| Cache hit rate | Free COGS | Nigeria contribution | Global contribution |
| --- | --- | --- | --- |
| 0% (today) | $376 | +$463 | +$2 367 |
| 40% | $282 | +$557 (+20%) | +$2 461 |
| **60%** | **$235** | **+$604 (+30%)** | **+$2 508** |
| 80% | $188 | +$651 (+41%) | +$2 555 |

**This is a correction to an earlier estimate that claimed 2.5×.** That figure combined this work with fixing Nigerian payment-method coverage, which is a separate and larger lever. Caching alone is worth roughly **+30%** at a plausible hit rate. Worth doing; not transformative on its own.

**The second-order effect is larger than the first and harder to model.** Because a hit costs nothing, allowances can be raised without raising COGS. Nigeria's 6 000-unit monthly (§6.8) is sized by what ₦2 400 can fund; if 60% of a learner's generation is free to serve, the same money funds a materially more generous product in the market where generosity is hardest to afford. **That is a product lever disguised as a cost lever**, and it is the reason to build this rather than the $141.

**And the hit rate is entirely unknown.** It depends on how many learners share a syllabus, which depends on the taxonomy in §6 existing, which is why no number here should be quoted until Phase 1 has run.

## 8. Phases

### Phase 1 — Taxonomy, and measure the overlap before building anything

**This phase exists to kill the idea cheaply if the overlap is not there.**

- [ ] `CurriculumTopic` table and a seeded taxonomy for **one subject on one board** — JAMB Physics is the obvious candidate.
- [ ] A resolver: free-text `ExamPrep.subject` / `LearningProfile.exam_name` → `CurriculumTopic`, via `aliases`, returning **null rather than a guess** when it cannot match.
- [ ] **Measure, read-only, against production: what fraction of existing `ExamPrep` rows resolve to a canonical topic, and what is the distribution?** If 4 000 learners share 20 topics, this works. If they resolve to 3 000 distinct free-text strings, the taxonomy needs to come from a published syllabus rather than from learner input, and that is a content-sourcing project rather than an engineering one.
- [ ] Write the result here with a date, the way the commercial plan records its subscriber count. **Do not proceed on an unmeasured hit rate.**

### Phase 2 — Cache one artefact kind, behind a flag

- [ ] `GeneratedArtefact` table, `curriculum_cache.py` reusing `narrative_cache.fingerprint`.
- [ ] Wrap **quiz generation only** (`quiz_engine.py:452`). It is the highest-cost cacheable operation at 780 units and the one with the cleanest prompt inputs.
- [ ] **Shadow mode first**: compute the key, record hit-or-miss, serve the freshly generated content regardless. This measures the real hit rate with zero risk of serving a wrong artefact.
- [ ] Only after the shadow hit rate is known: serve from cache behind a flag, default off.
- [ ] Tests: identical topic across two learners produces one entry and two hits; different `model` produces two entries; a null `board` never caches; **a learner-specific input in the key fails the test suite** rather than being reviewed for.

### Phase 3 — Quality, because a shared wrong answer is worse than a private one

- [ ] Learner-facing "report this question", writing `qualityFlags`. `PrepQuestionFlag` already exists for prep questions and is the precedent to follow.
- [ ] **Auto-quarantine on N flags** — pull the entry, fall back to live generation, queue for review. The threshold matters less than the mechanism existing before the cache is on by default.
- [ ] A review queue ordered by `qualityFlags DESC × hitCount`, so the worst-and-most-served is first.
- [ ] Decide and record: **is unreviewed cached content acceptable to serve?** My recommendation is yes for lesson bodies and outlines, and **no for quiz answers**, because a wrong `correctAnswer` marks a right answer wrong and that is the one failure a study tool cannot recover trust from.

### Phase 4 — Extend, only on measured hit rates

- [ ] Lesson bodies, course outlines, topic flashcards — each behind the same shadow-then-serve gate.
- [ ] **Never**: topic extraction, flashcards-from-note, documents, chat, or any Reflect narrative (§1).
- [ ] Per-country identification lands here if it has not already, and `country` stops being a constant (§4).

## 9. Risks and open questions

1. **Is the overlap real?** Phase 1 answers it. Everything else is contingent. My expectation is that overlap is high for JAMB/WAEC subjects and near-zero for self-directed courses, which would mean this is an exam-prep feature rather than a platform-wide one.
2. **Does cached content break "it knows me"?** Principle Two says AI should be invisible and personal. A cached MCQ on Newton's Laws is not less personal than a textbook's, and personalisation properly lives in *which* questions a learner is served and when — `prep_focus.py` and `prep_readiness.py` already do that deterministically and for free. **The selection is the personalisation; the content was never the personalisation.** Worth stating because the objection will be raised.
3. **Is there a copyright exposure?** Cached, reviewed, syllabus-aligned question banks start to resemble a publishable product, and the questions are model-generated from a syllabus rather than copied. This is a legal question, not an engineering one, and it should be asked before the bank gets large.
4. **Does this make the question bank a moat or a liability?** `PrepQuestion` today is per-preparation, and migration `008_promote_questions_to_bank.py` records that de-duplication was deliberately deferred as "a separate change made against a schema that can express it". This is that schema. Whether the two models converge or coexist is undecided and should not be decided by accident.
5. **What invalidates a syllabus change?** Nothing observes JAMB changing its syllabus. `promptVersion` covers our changes and not theirs. Probably an annual manual review keyed to the exam cycle, which is a process rather than a feature.
6. **Does a shared cache leak anything?** It must not, by §1's construction — but the test that proves it is that **no cached payload can be traced to a learner**, and that is worth asserting rather than assuming, because the first person to add `user_id` to a key "for debugging" will not think of it as a privacy change.
