"""Every Intelligence response model must read a real row correctly.

This guards a defect class whose failure mode is a `200`, which is why it needs a test rather than a
code review. Every response model in `src/domains/intelligence/models.py` used to declare its fields in
camelCase on a plain `BaseModel` with `from_attributes=True`. The ORM maps camelCase *columns* onto
snake_case *attributes*, so validation looked for attributes that were not there. Measured before the
rewrite, against real instances:

    ConversationResponse.model_validate(<ChatSession id=sess_1 title=T>)
    3 validation errors: userId, createdAt, updatedAt — Field required

    MessageResponse.model_validate(<ChatMessage id=msg_1 role=ASSISTANT>)
    3 validation errors: sessionId, userId, createdAt — Field required

**The three that raised were the lesser half.** They raised because they had no defaults. The eight
that did have defaults — `isActive`, `sessionType`, `courseId`, `topicId`, `examPrepId`, `noteId`,
`spaceId`, `isSpaceRoom` — were served as their declared default regardless of what the row said, with
a `200` and no log line. A conversation about a course would have been published as attached to
nothing. `TestTheGuardBites` reproduces exactly that, so this file fails if anyone reverts the base
class.

These are pure unit tests. The ORM instances are constructed in memory and never persisted, because
the defect is in the schema layer's reading of an object's attributes and a database adds nothing to
the question.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import BaseModel, ConfigDict

from src.domains.intelligence import models
from src.domains.intelligence.db_models import ChatMessage, ChatSession
from src.shared.schemas import CamelModel, CursorPage, PaginatedResponse

NOW = datetime(2026, 8, 26, 9, 0, tzinfo=UTC)


def make_session(**overrides) -> ChatSession:
    """A `ChatSession` with every column populated with a *distinguishable* value.

    Distinguishable matters more than realistic. A row whose `courseId` is `None` cannot tell you
    whether the schema read the column or fell back to its default, which is the whole failure mode
    under test.
    """
    row = ChatSession(
        id="sess_1",
        user_id="user_1",
        title="Thermodynamics questions",
        is_active=True,
        session_type="onboarding",
        space_id="space_1",
        course_id="course_1",
        topic_id="topic_1",
        exam_prep_id="prep_1",
        note_id="note_1",
        is_space_room=True,
    )
    row.created_at = NOW
    row.updated_at = NOW + timedelta(minutes=5)
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def make_message(**overrides) -> ChatMessage:
    """A `ChatMessage` with every published column populated distinguishably."""
    row = ChatMessage(
        id="msg_1",
        session_id="sess_1",
        user_id="user_1",
        role="ASSISTANT",
        content="The first law is conservation of energy.",
        suggestion_text="Review your thermodynamics deck",
        audio_url="https://cdn.example/audio.mp3",
        image_urls=["https://cdn.example/diagram.png"],
        component_data={"type": "mermaid", "source": "graph TD;"},
        token_count=137,
        model_name="gemini-2.0-flash",
        reply_to_message_id="msg_0",
    )
    row.created_at = NOW
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def assert_reads_every_field(model: type[CamelModel], row: object) -> None:
    """Validate `model` from `row` and assert every field equals the attribute of the same name.

    Generic on purpose. A test that names the fields it checks stops covering the field somebody adds
    next week, and the field somebody adds next week is the one that will be declared in camelCase.
    """
    validated = model.model_validate(row)
    for field_name in model.model_fields:
        assert hasattr(row, field_name), (
            f"{model.__name__}.{field_name} has no matching attribute on "
            f"{type(row).__name__}. Response fields are declared snake_case to match the ORM; if this "
            f"field is genuinely derived rather than read, validate it in the service and exclude it "
            f"from this guard explicitly."
        )
        expected = getattr(row, field_name)
        actual = getattr(validated, field_name)
        assert actual == expected, (
            f"{model.__name__}.{field_name} read {actual!r} from a row holding {expected!r}. "
            f"This is the silent-default failure: the endpoint would answer 200 with the wrong value."
        )


class TestConversationResponse:
    def test_reads_every_field_off_the_row(self):
        assert_reads_every_field(models.ConversationResponse, make_session())

    def test_the_context_links_survive(self):
        """The eight fields that used to be silently defaulted, named individually.

        `assert_reads_every_field` already covers them, but it covers them by iteration, and an
        iteration that walks an empty set also passes. These are the ones the outage was about.
        """
        published = models.ConversationResponse.model_validate(make_session()).model_dump(
            by_alias=True
        )
        assert published["courseId"] == "course_1"
        assert published["topicId"] == "topic_1"
        assert published["examPrepId"] == "prep_1"
        assert published["noteId"] == "note_1"
        assert published["spaceId"] == "space_1"
        assert published["isActive"] is True
        assert published["isSpaceRoom"] is True
        assert published["sessionType"] == "onboarding"

    def test_publishes_camel_case_on_the_wire(self):
        """The contract the clients already implement. Fields are snake_case in Python only."""
        published = models.ConversationResponse.model_validate(make_session()).model_dump(
            by_alias=True
        )
        assert "userId" in published and "user_id" not in published
        assert "createdAt" in published and "created_at" not in published
        assert "isSpaceRoom" in published and "is_space_room" not in published

    def test_an_untitled_conversation_keeps_its_null(self):
        """`title` is nullable and a fresh conversation has none. It must not become `""`."""
        validated = models.ConversationResponse.model_validate(make_session(title=None))
        assert validated.title is None

    def test_a_conversation_attached_to_nothing_is_published_as_attached_to_nothing(self):
        row = make_session(
            course_id=None, topic_id=None, exam_prep_id=None, note_id=None, space_id=None
        )
        published = models.ConversationResponse.model_validate(row).model_dump(by_alias=True)
        assert published["courseId"] is None
        assert published["spaceId"] is None


class TestMessageResponse:
    def test_reads_every_field_off_the_row(self):
        assert_reads_every_field(models.ChatMessageResponse, make_message())

    def test_model_name_survives_pydantics_protected_prefix(self):
        """`model_name` is a real column and `model_` is reserved by pydantic.

        The fix is `protected_namespaces=()`, not renaming the field — renaming it would put the schema
        and the ORM back out of step, which is the defect this whole file exists to prevent. Attribution
        is also the point of the column: an answer has to be traceable to the model that produced it.
        """
        validated = models.ChatMessageResponse.model_validate(make_message())
        assert validated.model_name == "gemini-2.0-flash"
        assert validated.model_dump(by_alias=True)["modelName"] == "gemini-2.0-flash"

    def test_a_message_with_no_images_validates(self):
        """`imageUrls` is a nullable array column, so a row with no images reads `None`.

        Declared as `list[str] = []` this raised outright — the plain absence of an image was a `500`.
        """
        validated = models.ChatMessageResponse.model_validate(make_message(image_urls=None))
        assert validated.image_urls is None

    def test_a_message_with_images_keeps_them(self):
        validated = models.ChatMessageResponse.model_validate(
            make_message(image_urls=["https://cdn.example/a.png", "https://cdn.example/b.png"])
        )
        assert validated.image_urls == [
            "https://cdn.example/a.png",
            "https://cdn.example/b.png",
        ]

    def test_component_data_passes_through_unflattened(self):
        validated = models.ChatMessageResponse.model_validate(make_message())
        assert validated.component_data == {"type": "mermaid", "source": "graph TD;"}


class TestTheGuardBites:
    """Proof that the tests above would fail if the base class were reverted.

    Required by the plan's Decision D, and not optional: a guard against a silent failure is worthless
    unless somebody has watched it fail. The models below are deliberate reconstructions of the shape
    `intelligence/models.py` had before the rewrite.
    """

    def test_camel_case_fields_on_a_plain_base_model_raise_on_the_required_ones(self):
        class RevertedConversationResponse(BaseModel):
            model_config = ConfigDict(from_attributes=True)

            id: str
            userId: str
            createdAt: datetime
            updatedAt: datetime

        with pytest.raises(Exception) as excinfo:
            RevertedConversationResponse.model_validate(make_session())

        message = str(excinfo.value)
        assert "userId" in message
        assert "createdAt" in message

    def test_camel_case_fields_with_defaults_are_served_as_the_default_with_no_error(self):
        """The dangerous half, reproduced. No exception, `200`, and the course link is gone."""

        class RevertedConversationResponse(BaseModel):
            model_config = ConfigDict(from_attributes=True)

            id: str
            courseId: str | None = None
            isSpaceRoom: bool = False
            sessionType: str = "general"

        row = make_session()
        assert row.course_id == "course_1"
        assert row.is_space_room is True
        assert row.session_type == "onboarding"

        reverted = RevertedConversationResponse.model_validate(row)
        assert reverted.courseId is None, "reverted shape unexpectedly read the column"
        assert reverted.isSpaceRoom is False
        assert reverted.sessionType == "general"

        # And the model this file actually guards does not do that.
        assert_reads_every_field(models.ConversationResponse, row)

    def test_the_generic_guard_rejects_a_field_with_no_matching_attribute(self):
        """`assert_reads_every_field` must fail, not pass vacuously, on a camelCase field."""

        class Sneaky(CamelModel):
            id: str
            courseId: str | None = None  # already camelCase, so aliased to `courseid`

        with pytest.raises(AssertionError, match="courseId"):
            assert_reads_every_field(Sneaky, make_session())


class TestEveryModelInTheModuleIsSnakeCase:
    """A structural guard, so the next field added is covered before anyone writes a test for it.

    The per-model tests above cover the models that exist today. This covers the ones that do not.
    """

    @staticmethod
    def response_models() -> list[type[BaseModel]]:
        """Every schema *defined in* the module, collected as `BaseModel` rather than as `CamelModel`.

        Collecting `CamelModel` subclasses was the first version of this and it had a hole wide enough
        to drive the original defect back through: reverting a model to a plain `BaseModel` — precisely
        the change these tests exist to catch — removed it from the collection, and the structural tests
        below then passed by not looking at it. Verified by doing it. So the filter is the loosest thing
        that still means "a schema in this module", and `test_every_model_inherits_the_shared_base` is
        what turns membership into a requirement.
        """
        found = []
        for _, obj in inspect.getmembers(models, inspect.isclass):
            if not issubclass(obj, BaseModel):
                continue
            if obj in (BaseModel, CamelModel, PaginatedResponse, CursorPage):
                continue
            if obj.__module__ != models.__name__:
                continue
            found.append(obj)
        return found

    def test_there_are_models_to_check(self):
        """Guards the guard: an empty list would make the test below pass silently."""
        assert len(self.response_models()) >= 8

    @pytest.mark.parametrize("model", response_models.__func__())
    def test_no_field_is_declared_in_camel_case(self, model):
        for field_name in model.model_fields:
            assert field_name.islower() or "_" in field_name, (
                f"{model.__name__}.{field_name} looks like camelCase. Declare fields snake_case to "
                f"match the ORM attributes; `CamelModel` publishes them camelCase on the wire."
            )
            assert not any(char.isupper() for char in field_name), (
                f"{model.__name__}.{field_name} contains an uppercase character. See the module "
                f"docstring in src/domains/intelligence/models.py."
            )

    @pytest.mark.parametrize("model", response_models.__func__())
    def test_every_model_inherits_the_shared_base(self, model):
        """The requirement that makes the collection above meaningful."""
        assert issubclass(model, CamelModel), (
            f"{model.__name__} is a plain BaseModel. Every schema in this module reads or writes rows "
            f"whose columns are camelCase and whose attributes are snake_case; see "
            f"src/shared/schemas.py for the two ways of getting that wrong."
        )
        assert model.model_config.get("from_attributes") is True
        assert model.model_config.get("populate_by_name") is True
        assert model.model_config.get("alias_generator") is not None


class TestMemoryModels:
    """These read camelCase dicts built by `memory_service`, not ORM rows.

    Worth its own class because the rewrite had to keep both input shapes working: `CamelModel`'s
    `populate_by_name` accepts the field name, and its alias generator accepts the camelCase key the
    service already produces. Breaking this would have turned two working endpoints into `500`s while
    fixing three broken ones.
    """

    def test_a_user_fact_validates_from_the_services_dict(self):
        validated = models.UserFactResponse.model_validate(
            {
                "id": "fact_1",
                "fact": "Studies best in the morning",
                "category": "schedule",
                "importance": 0.8,
                "createdAt": NOW,
            }
        )
        assert validated.fact == "Studies best in the morning"
        assert validated.importance == 0.8
        assert validated.created_at == NOW

    def test_a_summary_validates_from_the_services_dict(self):
        validated = models.ConversationSummaryResponse.model_validate(
            {
                "id": "sum_1",
                "sessionId": "sess_1",
                "summary": "Worked through the first law.",
                "keyTopics": ["thermodynamics", "energy"],
                "createdAt": NOW,
            }
        )
        assert validated.session_id == "sess_1"
        assert validated.key_topics == ["thermodynamics", "energy"]

    def test_the_snapshot_shape_validates(self):
        validated = models.MemoryContextResponse.model_validate(
            {
                "userFacts": [
                    {
                        "id": "fact_1",
                        "fact": "Studies best in the morning",
                        "category": "schedule",
                        "importance": 0.8,
                        "createdAt": NOW,
                    }
                ],
                "recentSummaries": [],
            }
        )
        assert len(validated.user_facts) == 1
        assert validated.recent_summaries == []
        assert validated.model_dump(by_alias=True)["userFacts"][0]["fact"] == (
            "Studies best in the morning"
        )

    def test_the_unmeasured_fields_are_gone_rather_than_empty(self):
        """`learningGoals`, `strengths` and `weaknesses` were declared and never computed.

        Publishing them as `[]` says "this learner has no weaknesses" where the truth is "nothing has
        measured them". They return when something computes them.
        """
        assert "learning_goals" not in models.MemoryContextResponse.model_fields
        assert "strengths" not in models.MemoryContextResponse.model_fields
        assert "weaknesses" not in models.MemoryContextResponse.model_fields


class TestModelPreference:
    def test_constructing_with_the_camel_case_kwarg_still_works(self):
        """`routes.py` builds this with `modelId=`, which `populate_by_name` keeps valid."""
        validated = models.ModelPreferenceResponse(
            capability="chat", provider="gemini", modelId="gemini-2.0-flash"
        )
        assert validated.model_id == "gemini-2.0-flash"
        assert validated.model_dump(by_alias=True)["modelId"] == "gemini-2.0-flash"

    def test_constructing_with_the_snake_case_name_also_works(self):
        validated = models.ModelPreferenceResponse(
            capability="chat", provider="gemini", model_id="gemini-2.0-flash"
        )
        assert validated.model_id == "gemini-2.0-flash"


class TestRequestModels:
    def test_conversation_create_dumps_the_keys_the_service_reads(self):
        """`create_conversation` dumps `by_alias=True` because the service reads camelCase keys.

        Without the alias every optional context link would be dropped and the request would still
        `201` — the same silent-discard class, moved from the response to the request.
        """
        body = models.ConversationCreate.model_validate(
            {"courseId": "course_9", "sessionType": "general", "isSpaceRoom": True}
        )
        dumped = body.model_dump(exclude_unset=True, by_alias=True)
        assert dumped == {
            "courseId": "course_9",
            "sessionType": "general",
            "isSpaceRoom": True,
        }

    def test_an_omitted_field_stays_omitted(self):
        body = models.ConversationCreate.model_validate({"courseId": "course_9"})
        assert body.model_dump(exclude_unset=True, by_alias=True) == {"courseId": "course_9"}

    def test_message_send_rejects_empty_content(self):
        with pytest.raises(Exception):
            models.MessageSend.model_validate({"content": ""})


class TestListEnvelopes:
    def test_the_conversation_list_uses_the_shared_paginated_envelope(self):
        page = models.PaginatedResponse[models.ConversationResponse](
            items=[models.ConversationResponse.model_validate(make_session())],
            total=1,
            page=1,
            page_size=20,
            pages=1,
        )
        published = page.model_dump(by_alias=True)
        assert set(published) == {"items", "total", "page", "pageSize", "pages"}
        assert published["items"][0]["courseId"] == "course_1"

    def test_the_message_thread_uses_the_cursor_envelope(self):
        """A thread pages backwards by id, so `page` and `pages` would have to be invented.

        The retired `MessageListResponse` published `messages` and `total` and nothing else, so a
        client could not tell whether more history existed: with a cursor, `total` counts the whole
        thread while `items` counts one window into the middle of it.
        """
        page = models.CursorPage[models.ChatMessageResponse](
            items=[models.ChatMessageResponse.model_validate(make_message())],
            total=42,
            has_more=True,
            next_cursor="msg_1",
        )
        published = page.model_dump(by_alias=True)
        assert set(published) == {"items", "total", "hasMore", "nextCursor"}
        assert published["hasMore"] is True
        assert published["nextCursor"] == "msg_1"

    def test_the_last_window_carries_no_cursor(self):
        page = models.CursorPage[models.ChatMessageResponse](
            items=[], total=0, has_more=False, next_cursor=None
        )
        published = page.model_dump(by_alias=True)
        assert published["hasMore"] is False
        assert published["nextCursor"] is None


class TestRetiredSchemas:
    """The schemas that backed the deleted endpoints must not drift back in.

    Each was reachable only from a route that could not run. Leaving the schema behind is how the route
    comes back without the defect being fixed.
    """

    @pytest.mark.parametrize(
        "name",
        [
            "ChatRequest",  # POST /chat — called a function that was never written
            "ChatResponse",
            "RecommendationResponse",  # GET /recommendations — same class of dead import
            "ConversationListResponse",  # replaced by PaginatedResponse[ConversationResponse]
            "MessageListResponse",  # replaced by CursorPage[MessageResponse]
            "VoiceSessionStartRequest",  # never referenced by any route
            "VoiceSessionResponse",
        ],
    )
    def test_the_schema_is_gone(self, name):
        assert not hasattr(models, name), (
            f"{name} is back in intelligence/models.py. It backed an endpoint that could not run; "
            f"see the comment blocks in intelligence/routes.py before restoring it."
        )


class TestSpaceRoomsStayOutOfTheAskSurface:
    """Mounting the router made space-room conversations reachable over HTTP for the first time.

    Ask Maigie is the personal, one-to-one surface. `ChatSession.is_space_room` and `space_id` exist
    because the same handler also serves group rooms inside a Learning Space, so the listing has to
    separate them — and until the mount there was no way for anyone to notice if it did not.

    These compile the predicate to SQL rather than run it, because the property under test is which
    conditions are applied, and that does not need rows. What they cannot show is that the socket serves
    a real space-room turn end to end; that needs a live database and a live model.
    """

    @staticmethod
    def rendered(**kwargs) -> str:
        from sqlalchemy import select

        from src.domains.intelligence.conversation.conversation_service import conversation_filters
        from src.domains.intelligence.db_models import ChatSession

        stmt = select(ChatSession.id).where(*conversation_filters(**kwargs))
        return str(stmt.compile(compile_kwargs={"literal_binds": True}))

    def test_a_personal_listing_excludes_space_rooms(self):
        sql = self.rendered(user_id="user_1")
        assert '"isSpaceRoom" = false' in sql
        assert '"spaceId" IS NULL' in sql

    def test_a_personal_listing_is_scoped_to_the_owner(self):
        sql = self.rendered(user_id="user_1")
        assert "\"userId\" = 'user_1'" in sql

    def test_a_space_listing_is_scoped_to_that_space(self):
        sql = self.rendered(user_id="user_1", space_id="space_1")
        assert "\"spaceId\" = 'space_1'" in sql
        # A space listing must not also demand `spaceId IS NULL`, which would return nothing, nor drop
        # the ownership check.
        assert '"spaceId" IS NULL' not in sql
        assert "\"userId\" = 'user_1'" in sql

    def test_archived_conversations_are_excluded_either_way(self):
        assert '"isActive" = true' in self.rendered(user_id="user_1")
        assert '"isActive" = true' in self.rendered(user_id="user_1", space_id="space_1")

    def test_the_session_type_filter_is_applied_only_when_asked(self):
        assert '"sessionType"' not in self.rendered(user_id="user_1")
        assert "\"sessionType\" = 'onboarding'" in self.rendered(
            user_id="user_1", session_type="onboarding"
        )
