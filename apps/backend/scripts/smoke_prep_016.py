"""Read-only smoke check that the app works against the 016 schema.

Exercises the two paths the migration touches — the preparation detail read and
the topic listing — through the real ORM models, the real service functions, and
the real response models. A column-name mismatch between `db_models.py` and the
migration would surface here as a database error, and a payload the response model
rejects would surface as a validation error.

**Writes nothing.** The HTTP integration tests would prove more, but they sign up
users and create preparations, which is not something to do to a shared database
for the sake of a verification.

    poetry run python scripts/db_direct.py python scripts/smoke_prep_016.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()

# Runnable from anywhere, like the other scripts in this directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main() -> None:
    from src.domains.personal_learning import models
    from src.domains.personal_learning.repository import personal_learning_repo as repo
    from src.domains.personal_learning.services import exam_prep_service, prep_readiness
    from src.shared.database.session import connect_db, disconnect_db

    await connect_db()
    try:
        # Any existing preparation will do; this only reads.
        preparations = await repo.list_exam_preps_by_ids(
            [row.id for row in await repo.list_snapshot_candidate_preps(skip=0, take=1)]
        )
        if not preparations:
            print("no preparations to read — schema verified, behaviour not exercised")
            return

        prep = preparations[0]
        print(f"preparation      : {prep.id}")
        print(f"  targetReadiness: {prep.target_readiness!r} (new column readable)")

        detail = await exam_prep_service.get_preparation_detail(
            user_id=prep.user_id, prep_id=prep.id
        )
        validated = models.PrepDetailResponse.model_validate(detail)
        print("\nGET /preparations/{id} -> PrepDetailResponse: validated")
        print(f"  progressPercent      : {validated.progress.progress_percent}")
        print(f"  averageMasteryPercent: {validated.progress.average_mastery_percent}")
        print(f"  accuracyPercent      : {validated.progress.accuracy_percent}")
        print(
            f"  topics strong/review/focus: "
            f"{validated.progress.topics_strong}/"
            f"{validated.progress.topics_review}/"
            f"{validated.progress.topics_focus}"
            f" of {validated.progress.topics_total}"
        )
        print(f"  practiceStreak       : {validated.progress.practice_streak!r}")
        print(f"  practiceReady        : {validated.progress.practice_ready}")
        print(f"  daysUntilExam        : {validated.days_until_exam!r}")
        if validated.focus:
            print(
                f"  focus                : {validated.focus.reason_code} "
                f"-> {validated.focus.recommended_mode} "
                f"({validated.focus.topic_title!r})"
            )
            print(f"  focus reason         : {validated.focus.reason}")

        # The number the Learn dashboard shows must be the same one. This is the
        # invariant the shared helper exists to protect.
        shared = await prep_readiness.load_for_preparation(prep.id)
        assert validated.progress.progress_percent == shared.progress_percent
        assert validated.progress.average_mastery_percent == shared.average_mastery_percent
        print("\nagrees with prep_readiness: yes")

        topics = await exam_prep_service.list_topics(user_id=prep.user_id, prep_id=prep.id)
        validated_topics = [models.PrepTopicDetail.model_validate(row) for row in topics]
        print(
            f"\nGET /preparations/{{id}}/topics -> {len(validated_topics)} PrepTopicDetail: validated"
        )
        for topic in validated_topics[:3]:
            print(
                f"  {topic.title[:34]:<34} band={topic.band:<6} "
                f"category={topic.category!r} "
                f"questions={topic.answered_question_count}/{topic.question_count}"
            )

        trend = await repo.list_readiness_snapshots(
            prep.id, since=__import__("datetime").date(2000, 1, 1)
        )
        print(f"\nreadiness snapshots readable: {len(trend)} (targetPercent column present)")
    finally:
        await disconnect_db()


if __name__ == "__main__":
    asyncio.run(main())
