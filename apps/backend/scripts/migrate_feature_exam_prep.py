"""Feature migration: legacy Prisma exam-prep → new Prep*/Quiz* models (in-database).

Run against a database that already holds BOTH the verbatim-carried legacy tables
(ExamPrepTopic, ExamPrepMaterial, ExamQuizSession, ExamQuestion, ExamQuestionAttempt) AND the new
(empty) tables. Source and target are the same DB, so this is INSERT ... SELECT.

    TARGET_URL=postgresql://user@host:5432/db python scripts/migrate_feature_exam_prep.py [--commit]

Without --commit it runs inside a transaction and rolls back (dry run) after printing what it would
do. With --commit it commits.

Legacy shape (from the real columns, not the code):
  * ExamQuestion is TOPIC-owned (topicId, NOT NULL) — it has no quizSessionId and no orderIndex.
    A session's use of a question is recorded only by ExamQuestionAttempt(quizSessionId, questionId).
  * Column renames: examPrepId→prepId, order→orderIndex, score→scorePercentage.
  * Enum columns (mode, category, source, questionType, difficulty) are cast to text.
  * Naive legacy timestamps are read as UTC.

Mapping:
  ExamPrepTopic     → PrepTopic            (id preserved)
  ExamPrepMaterial  → PrepMaterial         (id preserved)
  ExamQuizSession   → QuizSession          (id preserved; userId coalesced from ExamPrep; topic nulled if unresolved)
  ExamQuestion      → PrepQuestion         (id preserved; prepId derived from the topic's examPrepId)
  ExamQuestionAttempt (distinct) → QuizSessionQuestion (which questions a session asked; order by first attempt)
  ExamQuestionAttempt → QuizAnswer         (id preserved)
  + backfill PrepQuestion.timesAnswered/timesCorrect from QuizAnswer.

Idempotent: every insert is guarded by NOT EXISTS on the target key.
FK-safe order: parents before children, so no FK deferral is needed.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys

import asyncpg

_NEW_ID = "substr(md5(random()::text || clock_timestamp()::text), 1, 25)"

STEPS: list[tuple[str, str]] = [
    (
        "PrepTopic ← ExamPrepTopic",
        """
        INSERT INTO "PrepTopic" (id, "prepId", title, description, "orderIndex",
                                 "estimatedMinutes", "masteryScore", status, "createdAt", "updatedAt")
        SELECT t.id, t."examPrepId", t.title, t.description, COALESCE(t."order", 0),
               30, 0.0, 'NOT_STARTED',
               t."createdAt" AT TIME ZONE 'UTC', t."createdAt" AT TIME ZONE 'UTC'
        FROM "ExamPrepTopic" t
        WHERE EXISTS (SELECT 1 FROM "ExamPrep" e WHERE e.id = t."examPrepId")
          AND NOT EXISTS (SELECT 1 FROM "PrepTopic" p WHERE p.id = t.id)
        """,
    ),
    (
        "PrepMaterial ← ExamPrepMaterial",
        """
        INSERT INTO "PrepMaterial" (id, "prepId", filename, url, "extractedText", "fileType", size,
                                    category, label, "createdAt", "updatedAt")
        SELECT m.id, m."examPrepId", m.filename, m.url, m."extractedText", m."fileType", m.size,
               COALESCE(m.category::text, 'OTHER'), m.label,
               m."createdAt" AT TIME ZONE 'UTC', m."createdAt" AT TIME ZONE 'UTC'
        FROM "ExamPrepMaterial" m
        WHERE EXISTS (SELECT 1 FROM "ExamPrep" e WHERE e.id = m."examPrepId")
          AND NOT EXISTS (SELECT 1 FROM "PrepMaterial" p WHERE p.id = m.id)
        """,
    ),
    (
        "QuizSession ← ExamQuizSession",
        """
        INSERT INTO "QuizSession" (id, "userId", "prepId", mode, "topicId", status,
                                   "totalQuestions", "correctCount", "scorePercentage",
                                   "durationSeconds", "completedAt", "createdAt", "updatedAt")
        SELECT s.id,
               COALESCE(s."userId", e."userId"),
               s."examPrepId",
               s.mode::text,
               CASE WHEN s."topicId" IS NOT NULL
                         AND EXISTS (SELECT 1 FROM "PrepTopic" pt WHERE pt.id = s."topicId")
                    THEN s."topicId" ELSE NULL END,
               CASE WHEN s."completedAt" IS NOT NULL THEN 'COMPLETED' ELSE 'IN_PROGRESS' END,
               s."totalQuestions", s."correctCount", s.score, s."durationSeconds",
               s."completedAt" AT TIME ZONE 'UTC',
               s."createdAt" AT TIME ZONE 'UTC', s."createdAt" AT TIME ZONE 'UTC'
        FROM "ExamQuizSession" s
        JOIN "ExamPrep" e ON e.id = s."examPrepId"
        WHERE NOT EXISTS (SELECT 1 FROM "QuizSession" q WHERE q.id = s.id)
        """,
    ),
    (
        "PrepQuestion ← ExamQuestion (prepId via topic)",
        """
        INSERT INTO "PrepQuestion" (id, "prepId", "prepTopicId", "questionText", "questionType",
                                    options, "correctAnswer", explanation, difficulty, source,
                                    "sourceYear", "createdAt", "updatedAt")
        SELECT q.id, t."examPrepId", q."topicId", q."questionText", q."questionType"::text,
               q.options, COALESCE(q."correctAnswer", ''), q.explanation,
               q.difficulty::text, q.source::text,
               CASE WHEN q.year ~ '^[0-9]+$' THEN q.year::int ELSE NULL END,
               q."createdAt" AT TIME ZONE 'UTC', q."createdAt" AT TIME ZONE 'UTC'
        FROM "ExamQuestion" q
        JOIN "ExamPrepTopic" t ON t.id = q."topicId"
        WHERE EXISTS (SELECT 1 FROM "ExamPrep" e WHERE e.id = t."examPrepId")
          AND NOT EXISTS (SELECT 1 FROM "PrepQuestion" p WHERE p.id = q.id)
        """,
    ),
    (
        "QuizSessionQuestion ← distinct(attempt session,question)",
        f"""
        INSERT INTO "QuizSessionQuestion" (id, "quizSessionId", "prepQuestionId", "orderIndex",
                                           "createdAt", "updatedAt")
        SELECT {_NEW_ID}, x.sid, x.qid, x.ord, x.first_at, x.first_at
        FROM (
            SELECT a."quizSessionId" AS sid,
                   a."questionId"    AS qid,
                   MIN(a."createdAt") AT TIME ZONE 'UTC' AS first_at,
                   (ROW_NUMBER() OVER (PARTITION BY a."quizSessionId"
                                       ORDER BY MIN(a."createdAt")) - 1) AS ord
            FROM "ExamQuestionAttempt" a
            JOIN "QuizSession"  qs ON qs.id = a."quizSessionId"
            JOIN "PrepQuestion" pq ON pq.id = a."questionId"
            GROUP BY a."quizSessionId", a."questionId"
        ) x
        WHERE NOT EXISTS (
            SELECT 1 FROM "QuizSessionQuestion" e
            WHERE e."quizSessionId" = x.sid AND e."prepQuestionId" = x.qid
        )
        """,
    ),
    (
        "QuizAnswer ← ExamQuestionAttempt",
        """
        INSERT INTO "QuizAnswer" (id, "quizSessionId", "questionId", "userAnswer", "isCorrect",
                                  "timeTakenSeconds", "createdAt", "updatedAt")
        SELECT a.id, a."quizSessionId", a."questionId", a."userAnswer", a."isCorrect",
               a."timeTakenSeconds", a."createdAt" AT TIME ZONE 'UTC', a."createdAt" AT TIME ZONE 'UTC'
        FROM "ExamQuestionAttempt" a
        JOIN "QuizSession"  qs ON qs.id = a."quizSessionId"
        JOIN "PrepQuestion" pq ON pq.id = a."questionId"
        WHERE NOT EXISTS (SELECT 1 FROM "QuizAnswer" e WHERE e.id = a.id)
        """,
    ),
    (
        "backfill PrepQuestion lifetime stats",
        """
        UPDATE "PrepQuestion" pq
        SET "timesAnswered" = stats.answered,
            "timesCorrect"  = stats.correct
        FROM (
            SELECT "questionId" AS qid, COUNT(*) AS answered,
                   COUNT(*) FILTER (WHERE "isCorrect") AS correct
            FROM "QuizAnswer" GROUP BY "questionId"
        ) stats
        WHERE pq.id = stats.qid
        """,
    ),
]

# (label, legacy_count_sql, new_count_sql) — expectations after migration.
CHECKS: list[tuple[str, str, str]] = [
    ("topics", 'SELECT count(*) FROM "ExamPrepTopic"', 'SELECT count(*) FROM "PrepTopic"'),
    ("materials", 'SELECT count(*) FROM "ExamPrepMaterial"', 'SELECT count(*) FROM "PrepMaterial"'),
    ("sessions", 'SELECT count(*) FROM "ExamQuizSession"', 'SELECT count(*) FROM "QuizSession"'),
    ("questions", 'SELECT count(*) FROM "ExamQuestion"', 'SELECT count(*) FROM "PrepQuestion"'),
    ("answers", 'SELECT count(*) FROM "ExamQuestionAttempt"', 'SELECT count(*) FROM "QuizAnswer"'),
]

# Dangling-FK guards: these MUST return 0 after the migration.
DANGLING: list[tuple[str, str]] = [
    ("PrepTopic.prepId", 'SELECT count(*) FROM "PrepTopic" t LEFT JOIN "ExamPrep" e ON e.id=t."prepId" WHERE e.id IS NULL'),
    ("QuizSession.prepId", 'SELECT count(*) FROM "QuizSession" s LEFT JOIN "ExamPrep" e ON e.id=s."prepId" WHERE e.id IS NULL'),
    ("PrepQuestion.prepId", 'SELECT count(*) FROM "PrepQuestion" q LEFT JOIN "ExamPrep" e ON e.id=q."prepId" WHERE e.id IS NULL'),
    ("QuizAnswer.questionId", 'SELECT count(*) FROM "QuizAnswer" a LEFT JOIN "PrepQuestion" q ON q.id=a."questionId" WHERE q.id IS NULL'),
    ("QuizAnswer.quizSessionId", 'SELECT count(*) FROM "QuizAnswer" a LEFT JOIN "QuizSession" s ON s.id=a."quizSessionId" WHERE s.id IS NULL'),
    ("QuizSessionQuestion refs", 'SELECT count(*) FROM "QuizSessionQuestion" l LEFT JOIN "QuizSession" s ON s.id=l."quizSessionId" LEFT JOIN "PrepQuestion" q ON q.id=l."prepQuestionId" WHERE s.id IS NULL OR q.id IS NULL'),
]


async def main() -> None:
    commit = "--commit" in sys.argv
    url = os.environ["TARGET_URL"]
    url = re.sub(r"^postgresql\+asyncpg://", "postgresql://", url)
    con = await asyncpg.connect(url, statement_cache_size=0, server_settings={"search_path": "public"})
    tx = con.transaction()
    await tx.start()
    try:
        print(f"=== exam-prep feature migration ({'COMMIT' if commit else 'DRY RUN'}) ===")
        for label, sql in STEPS:
            status = await con.execute(sql)
            print(f"  {label:52s} {status}")
        print("\n--- row-count check (legacy → new) ---")
        for label, oldq, newq in CHECKS:
            o = await con.fetchval(oldq)
            n = await con.fetchval(newq)
            flag = "" if n >= o else "  <-- FEWER THAN LEGACY"
            print(f"  {label:12s} legacy={o:<6d} new={n}{flag}")
        print("\n--- dangling-FK guards (must be 0) ---")
        bad = 0
        for label, q in DANGLING:
            c = await con.fetchval(q)
            if c:
                bad += 1
            print(f"  {label:28s} {c}")
        if bad:
            raise RuntimeError(f"{bad} dangling-FK checks failed — rolling back")
        # QuizSessionQuestion coverage (informational)
        sq = await con.fetchval('SELECT count(*) FROM "QuizSessionQuestion"')
        print(f"\n  QuizSessionQuestion rows: {sq}")
    except Exception:
        await tx.rollback()
        print("!! rolled back due to error")
        raise
    if commit:
        await tx.commit()
        print("\nCOMMITTED ✅")
    else:
        await tx.rollback()
        print("\nrolled back (dry run) — re-run with --commit to apply")
    await con.close()


if __name__ == "__main__":
    asyncio.run(main())
