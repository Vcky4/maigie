"""What a learner may attach to a turn, and what happens to it.

Plan §6.1 and §5.2.2. These cover the validation rules as decisions rather than as plumbing, because what
is accepted here is learner-supplied input that reaches a model and gets served back under our domain.

The route tests hold the parts a caller can observe and depend on: a rejection is a 400 and not a 500, a
storage outage is a 503 and writes no row, and another learner's upload is a 404 and not a 403.
"""

from __future__ import annotations

import io
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.domains.intelligence import routes  # noqa: E402
from src.domains.intelligence.conversation import attachments  # noqa: E402
from src.shared.auth import get_current_user  # noqa: E402

USER = SimpleNamespace(id="user_1", email="learner@example.com")


# ---------------------------------------------------------------------------
# Validation, without storage
# ---------------------------------------------------------------------------


class TestWhatIsWorthStoring:
    """`validate_attachment` is pure, so the policy can be read off the tests."""

    def test_an_image_within_the_limit_is_accepted(self):
        assert (
            attachments.validate_attachment(
                content_type="image/png", size=2048, allowed=attachments.ALLOWED_IMAGE_TYPES
            )
            is None
        )

    def test_an_empty_file_is_refused(self):
        rejection = attachments.validate_attachment(
            content_type="image/png", size=0, allowed=attachments.ALLOWED_IMAGE_TYPES
        )
        assert rejection is not None
        assert rejection.code == attachments.ATTACHMENT_REJECTED_EMPTY

    def test_a_file_over_the_limit_is_refused_and_says_the_limit(self):
        """The message carries both numbers.

        "Too large" without a limit leaves the learner guessing how much to shrink it by, which turns one
        refusal into several.
        """
        rejection = attachments.validate_attachment(
            content_type="image/png",
            size=attachments.MAX_ATTACHMENT_BYTES + 1,
            allowed=attachments.ALLOWED_IMAGE_TYPES,
        )
        assert rejection is not None
        assert rejection.code == attachments.ATTACHMENT_REJECTED_TOO_LARGE
        assert "10 MB" in rejection.message

    def test_the_limit_itself_is_accepted(self):
        """The boundary is inclusive. A file of exactly the advertised limit must not be refused by the
        rule that advertises it."""
        assert (
            attachments.validate_attachment(
                content_type="image/png",
                size=attachments.MAX_ATTACHMENT_BYTES,
                allowed=attachments.ALLOWED_IMAGE_TYPES,
            )
            is None
        )

    @pytest.mark.parametrize("declared", ["application/pdf", "text/html", "", None])
    def test_a_type_the_model_cannot_read_is_refused(self, declared):
        rejection = attachments.validate_attachment(
            content_type=declared, size=1024, allowed=attachments.ALLOWED_IMAGE_TYPES
        )
        assert rejection is not None
        assert rejection.code == attachments.ATTACHMENT_REJECTED_TYPE

    def test_svg_is_refused(self):
        """SVG is a scriptable document, not a bitmap.

        Called out on its own rather than folded into the parametrised case because it is the one type
        where accepting a mislabelled file would store an XSS payload under a URL this product serves. A
        future widening of the allowlist should have to delete this test on purpose.
        """
        rejection = attachments.validate_attachment(
            content_type="image/svg+xml", size=1024, allowed=attachments.ALLOWED_IMAGE_TYPES
        )
        assert rejection is not None
        assert rejection.code == attachments.ATTACHMENT_REJECTED_TYPE

    def test_a_charset_parameter_does_not_defeat_the_allowlist(self):
        """Browsers append `; charset=...`. Matching the raw header would refuse a valid upload."""
        assert (
            attachments.validate_attachment(
                content_type="image/png; charset=binary",
                size=1024,
                allowed=attachments.ALLOWED_IMAGE_TYPES,
            )
            is None
        )

    def test_the_allowlist_is_a_parameter_not_baked_in(self):
        """The rule is decoupled from the list it enforces.

        Only the image list is live today — an audio/transcription path was drafted and pulled for want of
        a provider — but the validator still takes the allowlist as an argument. This pins that seam: a
        type on one list is refused against a different, empty-of-it list, which is what makes re-adding a
        second file kind a caller change rather than a fork of the rules.
        """
        assert (
            attachments.validate_attachment(
                content_type="image/png", size=1024, allowed=attachments.ALLOWED_IMAGE_TYPES
            )
            is None
        )
        assert attachments.validate_attachment(
            content_type="image/png", size=1024, allowed=frozenset({"audio/webm"})
        )


class TestWhereAttachmentsLand:
    def test_the_path_is_namespaced_by_learner(self):
        """So a listing is per-account and a delete cannot walk into another folder by filename."""
        path = attachments.upload_path(user_id="user_1", kind="images")
        assert "user_1" in path
        assert attachments.upload_path(user_id="user_2", kind="images") != path

    def test_images_and_audio_are_kept_apart(self):
        assert attachments.upload_path(user_id="u", kind="images") != attachments.upload_path(
            user_id="u", kind="audio"
        )


class TestTheStoredRow:
    def test_the_row_carries_the_owner_and_the_file(self):
        row = attachments.build_upload_row(
            user_id="user_1",
            url="https://cdn/x.png",
            filename="x.png",
            mime_type="image/png",
            size=2048,
        )
        assert row["userId"] == "user_1"
        assert row["url"] == "https://cdn/x.png"
        assert row["mimeType"] == "image/png"
        assert row["size"] == 2048

    def test_the_row_is_not_linked_to_a_message(self):
        """The attachment is uploaded before the turn that carries it exists.

        A `chatMessageId: None` written here would be indistinguishable from an orphan that was never
        used, so linking is the turn's job and the key must be absent, not null.
        """
        row = attachments.build_upload_row(
            user_id="user_1", url="u", filename="f", mime_type=None, size=1
        )
        assert "chatMessageId" not in row


# ---------------------------------------------------------------------------
# The routes
# ---------------------------------------------------------------------------


class Harness:
    """The attachment routes with storage and the upload table faked."""

    def __init__(self, *, find_upload=None, stored=None, storage_raises=None):
        self.created: list[dict] = []
        self.deleted: list[tuple] = []
        self.storage_deletes: list[str] = []
        self.stored = stored or {"url": "https://cdn/x.png", "filename": "x.png", "size": 2048}
        self.storage_raises = storage_raises
        self.find_upload = find_upload or AsyncMock(return_value=None)

    def __enter__(self):
        app = FastAPI()
        app.include_router(routes.router, prefix="/api/v1/intelligence")
        app.dependency_overrides[get_current_user] = lambda: USER

        async def create_upload(data):
            self.created.append(data)
            return SimpleNamespace(
                id="up_1",
                url=data["url"],
                filename=data["filename"],
                mime_type=data.get("mimeType"),
                size=data.get("size"),
            )

        async def delete_upload(upload_id, user_id):
            self.deleted.append((upload_id, user_id))
            return True

        async def upload_upload_file(_file, path_prefix=""):
            if self.storage_raises:
                raise self.storage_raises
            return self.stored

        async def storage_delete(url):
            self.storage_deletes.append(url)
            return True

        self._patches = [
            patch.object(routes.intelligence_repo, "create_upload", create_upload),
            patch.object(routes.intelligence_repo, "find_upload", self.find_upload),
            patch.object(routes.intelligence_repo, "delete_upload", delete_upload),
            patch(
                "src.shared.infrastructure.storage.storage_service.upload_upload_file",
                upload_upload_file,
            ),
            patch("src.shared.infrastructure.storage.storage_service.delete", storage_delete),
        ]
        for p in self._patches:
            p.start()
        self.client = TestClient(app, raise_server_exceptions=False)
        return self

    def __exit__(self, *_exc):
        for p in reversed(self._patches):
            p.stop()

    def upload(self, *, content=b"\x89PNG fake bytes", content_type="image/png"):
        return self.client.post(
            "/api/v1/intelligence/ask/attachments",
            files={"file": ("x.png", io.BytesIO(content), content_type)},
        )


class TestUploadingAnImage:
    def test_an_accepted_image_is_stored_and_returned_with_its_id(self):
        """The id is what a later delete addresses.

        Returning only the url would leave an attachment the learner removes from the composer with no way
        to be cleaned up — which is the orphan §6.1 asked for the delete route to prevent.
        """
        with Harness() as h:
            response = h.upload()
        assert response.status_code == 201
        body = response.json()
        assert body["id"] == "up_1"
        assert body["url"] == "https://cdn/x.png"
        assert len(h.created) == 1

    def test_a_refused_file_is_a_400_and_is_never_stored(self):
        """Rejected before storage, so a bad upload costs nothing."""
        with Harness() as h:
            response = h.upload(content_type="application/pdf")
        assert response.status_code == 400
        assert h.created == []

    def test_an_empty_file_is_a_400(self):
        with Harness() as h:
            response = h.upload(content=b"")
        assert response.status_code == 400
        assert h.created == []

    def test_a_storage_outage_is_a_503_and_writes_no_row(self):
        """A row pointing at a file that was never stored renders as a broken image forever."""
        from src.shared.infrastructure.storage import StorageError

        with Harness(storage_raises=StorageError("bunny down")) as h:
            response = h.upload()
        assert response.status_code == 503
        assert h.created == []


class TestRemovingAnAttachment:
    def test_the_owner_can_delete_and_gets_no_content(self):
        owned = SimpleNamespace(id="up_1", url="https://cdn/x.png", user_id="user_1")
        with Harness(find_upload=AsyncMock(return_value=owned)) as h:
            response = h.client.delete("/api/v1/intelligence/ask/attachments/up_1")
        assert response.status_code == 204
        assert h.deleted == [("up_1", "user_1")]
        assert h.storage_deletes == ["https://cdn/x.png"]

    def test_another_learners_upload_is_a_404_not_a_403(self):
        """`403` would confirm the id exists, which makes it probeable.

        Matches `/ask`'s choice for another learner's conversation: the answer to "is this yours" and "does
        this exist" must be the same answer.
        """
        with Harness(find_upload=AsyncMock(return_value=None)) as h:
            response = h.client.delete("/api/v1/intelligence/ask/attachments/someone_elses")
        assert response.status_code == 404
        assert h.deleted == []

    def test_the_row_goes_even_if_storage_will_not(self):
        """A file left in storage is invisible and merely costs money.

        A row pointing at a deleted file is a broken image in the learner's thread. Prefer the invisible
        failure, and log it — so a storage error must not abort the row delete.
        """
        owned = SimpleNamespace(id="up_1", url="https://cdn/x.png", user_id="user_1")
        with Harness(find_upload=AsyncMock(return_value=owned)) as h:
            with patch(
                "src.shared.infrastructure.storage.storage_service.delete",
                AsyncMock(side_effect=RuntimeError("bunny down")),
            ):
                response = h.client.delete("/api/v1/intelligence/ask/attachments/up_1")
        assert response.status_code == 204
        assert h.deleted == [("up_1", "user_1")]
