"""Course material is read on the way in, and a generated outline is applied once.

`add_course_material` used to hand the upload straight to storage and write a `Resource` holding
a filename and a URL. Nothing read the bytes, nothing capped the size, and nothing sanitised the
name — so a course material was a download link, and `../../` in a client-supplied filename was
addressing the storage prefix of whatever it pointed at.

`apply_generated_outline` is new, and its retry guard is the part worth pinning: onboarding's
steps are individually best-effort, so a learner whose outline call failed comes back through it.
Without the guard the retry appends a second curriculum to the same course.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

import io
from types import SimpleNamespace

import pytest

from src.domains.knowledge.services import course_service
from src.shared import files as shared_files


class FakeUpload:
    """The parts of `UploadFile` this service touches."""

    def __init__(self, content: bytes, filename: str, content_type: str | None = None):
        self._buffer = io.BytesIO(content)
        self.filename = filename
        self.content_type = content_type

    async def read(self) -> bytes:
        return self._buffer.read()


@pytest.fixture
def captured(monkeypatch):
    """Capture the storage write and the resource row instead of performing them."""
    record: dict = {}

    async def fake_upload_bytes(content, remote_path, *, content_type="application/octet-stream"):
        record["content"] = content
        record["remotePath"] = remote_path
        record["contentType"] = content_type
        return {"filename": remote_path.rsplit("/", 1)[-1], "url": f"https://cdn/{remote_path}"}

    async def fake_create_resource(data):
        record["resource"] = data
        return SimpleNamespace(id="resource-1", **{})

    async def fake_ownership(course_id, user_id):
        record["ownershipChecked"] = (course_id, user_id)
        return SimpleNamespace(id=course_id)

    monkeypatch.setattr(
        "src.shared.infrastructure.storage.storage_service.upload_bytes", fake_upload_bytes
    )
    monkeypatch.setattr(course_service.knowledge_repo, "create_resource", fake_create_resource)
    monkeypatch.setattr(course_service, "check_course_ownership", fake_ownership)
    return record


class TestUploadedMaterialIsRead:
    @pytest.mark.asyncio
    async def test_text_is_extracted_and_stored(self, captured):
        await course_service.add_course_material(
            user_id="user-1",
            course_id="course-1",
            file=FakeUpload(b"Week 1: limits\nWeek 2: derivatives", "MTH101.txt", "text/plain"),
        )

        assert "derivatives" in captured["resource"]["extractedText"]
        assert captured["resource"]["courseId"] == "course-1"
        assert captured["resource"]["type"] == "DOCUMENT"

    @pytest.mark.asyncio
    async def test_a_file_we_cannot_read_is_still_stored(self, captured):
        """An image belongs in the library even though it grounds nothing. `None` says so."""
        await course_service.add_course_material(
            user_id="user-1",
            course_id="course-1",
            file=FakeUpload(b"\x89PNG\r\n\x1a\n", "diagram.png", "image/png"),
        )

        assert captured["resource"]["extractedText"] is None
        assert captured["resource"]["url"].endswith("diagram.png")

    @pytest.mark.asyncio
    async def test_a_traversing_filename_cannot_escape_the_course_prefix(self, captured):
        await course_service.add_course_material(
            user_id="user-1",
            course_id="course-1",
            file=FakeUpload(b"notes", "../../other-user/secrets.txt", "text/plain"),
        )

        assert captured["remotePath"].startswith("courses/user-1/course-1/")
        assert ".." not in captured["remotePath"]

    @pytest.mark.asyncio
    async def test_an_empty_file_is_refused(self, captured):
        with pytest.raises(Exception) as excinfo:
            await course_service.add_course_material(
                user_id="user-1",
                course_id="course-1",
                file=FakeUpload(b"", "empty.txt", "text/plain"),
            )

        assert "empty" in str(excinfo.value).lower()
        assert "resource" not in captured

    @pytest.mark.asyncio
    async def test_an_oversized_file_is_refused_before_it_is_stored(self, captured):
        oversized = b"x" * (shared_files.MAX_MATERIAL_UPLOAD_BYTES + 1)

        with pytest.raises(Exception) as excinfo:
            await course_service.add_course_material(
                user_id="user-1",
                course_id="course-1",
                file=FakeUpload(oversized, "textbook.txt", "text/plain"),
            )

        assert "limit" in str(excinfo.value).lower()
        # Nothing reached storage, so no resource points at a URL holding nothing.
        assert "content" not in captured
        assert "resource" not in captured


class TestOutlineIsAppliedOnce:
    @pytest.fixture
    def writes(self, monkeypatch):
        record: dict = {"modules": [], "topics": {}}

        async def fake_create_module(data):
            module_id = f"module-{len(record['modules']) + 1}"
            record["modules"].append(data)
            return SimpleNamespace(id=module_id)

        async def fake_create_topics(module_id, items):
            record["topics"][module_id] = items
            return [SimpleNamespace(id=f"{module_id}-t{i}") for i, _ in enumerate(items)]

        monkeypatch.setattr(course_service.knowledge_repo, "create_module", fake_create_module)
        monkeypatch.setattr(course_service.knowledge_repo, "create_topics", fake_create_topics)
        return record

    def _course(self, monkeypatch, *, modules):
        async def fake_find(course_id, user_id):
            return SimpleNamespace(id=course_id, title="Python", modules=modules)

        monkeypatch.setattr(course_service.knowledge_repo, "find_course_with_modules", fake_find)

    @pytest.mark.asyncio
    async def test_modules_and_topics_are_written_in_order(self, monkeypatch, writes):
        self._course(monkeypatch, modules=[])

        written = await course_service.apply_generated_outline(
            user_id="user-1",
            course_id="course-1",
            modules=[
                {
                    "title": "Foundations",
                    "description": "The basics",
                    "topics": [
                        {"title": "Values", "kind": "Lesson", "durationMinutes": 30},
                        {"title": "Practice", "kind": "Practice", "durationMinutes": 45},
                    ],
                },
                {"title": "Collections", "topics": [{"title": "Lists"}]},
            ],
        )

        assert written == 2
        assert [m["title"] for m in writes["modules"]] == ["Foundations", "Collections"]
        assert [m["order"] for m in writes["modules"]] == [0.0, 1.0]
        first = writes["topics"]["module-1"]
        assert [t["title"] for t in first] == ["Values", "Practice"]
        # `kind` survives, unlike in `create_course_with_outline`, which normalises topics to
        # bare titles — a Practice topic arriving as a plain Lesson is a worse first course.
        assert [t["kind"] for t in first] == ["Lesson", "Practice"]
        # Minutes from the generator, hours in the column.
        assert first[0]["estimatedHours"] == 0.5

    @pytest.mark.asyncio
    async def test_a_course_that_already_has_an_outline_is_left_alone(self, monkeypatch, writes):
        """The retry guard. Generation is best-effort and re-runs; appending a second
        curriculum to the same course is the failure this prevents."""
        self._course(monkeypatch, modules=[SimpleNamespace(id="module-existing")])

        written = await course_service.apply_generated_outline(
            user_id="user-1",
            course_id="course-1",
            modules=[{"title": "Foundations", "topics": [{"title": "Values"}]}],
        )

        assert written == 0
        assert writes["modules"] == []

    @pytest.mark.asyncio
    async def test_an_empty_outline_writes_nothing(self, monkeypatch, writes):
        self._course(monkeypatch, modules=[])

        assert (
            await course_service.apply_generated_outline(
                user_id="user-1", course_id="course-1", modules=[]
            )
            == 0
        )
        assert writes["modules"] == []

    @pytest.mark.asyncio
    async def test_a_module_with_no_usable_topics_is_skipped(self, monkeypatch, writes):
        self._course(monkeypatch, modules=[])

        written = await course_service.apply_generated_outline(
            user_id="user-1",
            course_id="course-1",
            modules=[
                {"title": "Empty", "topics": []},
                {"title": "Real", "topics": [{"title": "Something"}]},
            ],
        )

        assert written == 1
        assert [m["title"] for m in writes["modules"]] == ["Real"]
