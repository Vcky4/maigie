"""No request field may be accepted and then quietly discarded.

This guards a defect class rather than a defect. Every repository translates a request dictionary onto
ORM attributes through a `field_map`, and every one of them used to be written so that a key the map
did not know was **silently dropped**: the request succeeded, the response looked correct because it
was built from the request rather than from the row, and the value never reached the database.

Four instances were found, and they are the reason this file exists:

- `search` on the course list endpoint — accepted, dropped, so searching returned everything.
- `category`, `tags`, `outcomes`, `instructorName`, `instructorRole` on course create — five fields on
  a form, all doing nothing on their first day.
- The three flashcard review aids reaching a `_map_flashcard` that did not list them.
- Worst, because it was deliberate: a test asserting that an unknown key *is* dropped, whose own
  docstring noted the same permissiveness had already hidden a defect. The behaviour was pinned in
  place instead of fixed.

`map_fields` now refuses. These tests prove the refusal works, that it is applied everywhere, and —
the part that actually prevents recurrence — that each request model's fields are all mappable.
"""

from __future__ import annotations

import inspect
import pkgutil
import re
from importlib import import_module
from pathlib import Path

import pytest

from src.shared.field_mapping import UnmappedFieldError, map_fields, reject_unclearable

SRC = Path(__file__).resolve().parents[1] / "src"


class TestMapFields:
    def test_a_mapped_field_is_translated(self):
        assert map_fields({"topicId": "t1"}, {"topicId": "topic_id"}, entity="x") == {
            "topic_id": "t1"
        }

    def test_an_unmapped_field_raises_rather_than_disappearing(self):
        with pytest.raises(UnmappedFieldError) as excinfo:
            map_fields({"typo": 1}, {"real": "real"}, entity="widget")

        message = str(excinfo.value)
        # The message has to name the offending field and the mapper, or the developer who sees it in
        # CI cannot act on it without reading this module.
        assert "typo" in message
        assert "widget" in message
        assert "real" in message

    def test_an_ignored_field_is_allowed_through_and_not_stored(self):
        """The escape hatch. A field handled elsewhere is exempted explicitly at the call site, so the
        decision is visible in a diff rather than implied by silence."""
        assert map_fields({"handledElsewhere": 1}, {"a": "a"}, entity="x", ignore={"handledElsewhere"}) == {}

    def test_an_omitted_key_stays_omitted(self):
        """What makes `exclude_unset=True` meaningful: "not sent" and "sent as null" must stay
        distinguishable, or clearing a field becomes inexpressible — which is exactly the bug
        `update_course` had."""
        assert map_fields({}, {"a": "a"}, entity="x") == {}

    def test_an_explicit_null_is_preserved(self):
        assert map_fields({"a": None}, {"a": "a"}, entity="x") == {"a": None}


class TestRejectUnclearable:
    """The other half of the defect: clearing a field was impossible and reported success.

    Five services filtered nulls out of an update while their route dumped the body with
    `exclude_unset=True`, so a key only arrived when the client sent it and the filter then removed
    exactly the ones sent as null. `{"category": null}` returned `200` and changed nothing.
    """

    def test_a_nullable_column_may_be_cleared(self):
        from src.domains.knowledge.db_models import Course

        # No exception: `category` is nullable, so clearing it is a legitimate request.
        reject_unclearable({"category": None}, Course)

    def test_a_non_nullable_column_may_not_be_cleared(self):
        from src.domains.knowledge.db_models import Course

        with pytest.raises(ValueError, match="title"):
            reject_unclearable({"title": None}, Course)

    def test_a_value_that_is_not_null_is_never_refused(self):
        from src.domains.knowledge.db_models import Course

        reject_unclearable({"title": "A new title"}, Course)

    def test_nullability_is_read_from_the_schema_not_a_hand_written_list(self):
        """Which is what stops the rule drifting: a column made NOT NULL in a later migration starts
        being enforced without anyone remembering to update anything."""
        from src.domains.knowledge.db_models import Course

        nullable = [c.name for c in Course.__table__.columns if c.nullable]
        assert "category" in nullable
        assert "title" not in nullable

    def test_a_wire_name_that_differs_from_its_attribute_is_resolved(self):
        from src.domains.knowledge.db_models import Topic

        # `knowledgeCheck` is the column, `knowledge_check` the attribute. Nullable, so allowed.
        reject_unclearable({"knowledgeCheck": None}, Topic, field_map={"knowledgeCheck": "knowledge_check"})

    def test_an_unknown_key_is_left_to_the_mapper(self):
        """Whether an unmapped field may be cleared is `map_fields`' business, not this one's — two
        functions reporting the same error differently is worse than one."""
        from src.domains.knowledge.db_models import Course

        reject_unclearable({"notAColumn": None}, Course)


class TestNoSilentDropRemains:
    """The pattern itself must be gone from the repositories, not just fixed where it was noticed.

    A grep, deliberately. The alternative is calling every mapper with junk and asserting it raises,
    which needs a live session for some of them and would still miss any mapper the test forgot to
    enumerate. The pattern is textual, so a textual check catches every instance including ones written
    after this test.
    """

    #: `{map[k]: v for k, v in data.items() if k in map}` and its variants — the shape that drops.
    SILENT_DROP = re.compile(
        r"for k, v in data\.items\(\)\s*if k in ",
        re.S,
    )

    def test_no_repository_still_filters_unknown_keys_away(self):
        offenders = []
        for path in SRC.glob("domains/*/repository.py"):
            if self.SILENT_DROP.search(path.read_text()):
                offenders.append(str(path.relative_to(SRC)))

        assert offenders == [], (
            f"These repositories still discard unmapped request fields silently: {offenders}. "
            f"Use `map_fields(data, field_map, entity=...)` so an unmapped field raises instead."
        )

    def test_every_repository_mapper_goes_through_map_fields(self):
        """A mapper that hand-rolls its translation is a mapper that can start dropping again."""
        offenders = []
        for path in SRC.glob("domains/*/repository.py"):
            text = path.read_text()
            # `identity` is exempt and the reason is in its own mapper: it does not drop, it converts
            # camelCase to snake_case and passes everything through, so an unknown key reaches the ORM
            # and fails loudly at the constructor. Different behaviour, not a silent loss.
            if "identity" in str(path):
                continue
            for match in re.finditer(r"def (_map_[a-z_]+)\(.*?(?=\n    (?:@|def |async def )|\Z)", text, re.S):
                body = match.group(0)
                if "map_fields(" not in body and "return {" in body:
                    offenders.append(f"{path.relative_to(SRC)}::{match.group(1)}")

        assert offenders == [], (
            f"These mappers build their result by hand rather than through `map_fields`: {offenders}."
        )


class TestRequestModelsAreFullyMappable:
    """Every field a request model accepts must be storable somewhere.

    This is the check that actually prevents recurrence. The others catch the *pattern*; this catches
    the *omission* — a field added to a Pydantic request model and never wired into the mapper behind
    it. That is the shape all four real defects took.

    Scope note: this asserts a field is *named* in the domain's mappers or services, not that it is
    stored correctly. A full check would need to call each endpoint, which the Postgres API suites do.
    This is the cheap guard that runs on every commit and would have caught all four.
    """

    #: Fields that legitimately never reach a mapper, with the reason each is exempt.
    EXEMPT = {
        # Read-side parameters: filters, pagination and sorting shape a query rather than a row.
        "page", "pageSize", "sortBy", "sortOrder", "search", "limit", "offset", "cursor",
        # Routing and identity, supplied by the path or the token rather than the body.
        "id", "userId",
        # Generation and workflow inputs consumed by a service and never persisted as-is.
        "type", "prompt", "query", "quality", "completed", "deckId",
    }

    def _request_models(self):
        """Every `*Create` / `*Update` Pydantic model in the domains."""
        from pydantic import BaseModel

        models = []
        for module_info in pkgutil.walk_packages([str(SRC / "domains")], prefix="src.domains."):
            if not module_info.name.endswith(".models"):
                continue
            try:
                module = import_module(module_info.name)
            except Exception:  # pragma: no cover - a module that cannot import is another test's problem
                continue
            for name, obj in vars(module).items():
                if (
                    inspect.isclass(obj)
                    and issubclass(obj, BaseModel)
                    and obj is not BaseModel
                    and (name.endswith("Create") or name.endswith("Update"))
                ):
                    models.append((module_info.name, name, obj))
        return models

    def test_request_model_fields_are_named_somewhere_in_their_domain(self):
        models = self._request_models()
        # If this collapses to nothing the test is vacuous, which is worse than failing.
        assert len(models) > 20, f"expected many request models, found {len(models)}"

        domain_sources: dict[str, str] = {}
        unmapped: list[str] = []

        for module_name, model_name, model in models:
            domain = module_name.split(".")[2]
            if domain not in domain_sources:
                domain_dir = SRC / "domains" / domain
                domain_sources[domain] = "\n".join(
                    path.read_text() for path in domain_dir.rglob("*.py")
                )
            source = domain_sources[domain]

            for field_name, field in model.model_fields.items():
                wire_name = field.alias or field_name
                if wire_name in self.EXEMPT:
                    continue
                # Named anywhere in the domain, in either of the two ways a field is legitimately
                # consumed:
                #
                #   - as a dictionary key, `"category"`, which is how the mappers see it. Quoted, so
                #     `category` does not match `categoryLabel`.
                #   - as an attribute, `body.modelId`, which is how routes that build an ORM object by
                #     hand see it. Missing this produced two false positives on the first run, and a
                #     guard that cries wolf gets switched off.
                if f'"{wire_name}"' in source or f"'{wire_name}'" in source:
                    continue
                if re.search(rf"\.{re.escape(wire_name)}\b", source):
                    continue
                unmapped.append(f"{model_name}.{wire_name} ({domain})")

        assert unmapped == [], (
            "These request fields are accepted by a model but named nowhere in their domain, so they "
            "are almost certainly being discarded:\n  " + "\n  ".join(sorted(unmapped))
        )


class TestResponseModelsAreFullyPopulated:
    """A response field no route populates comes back null, silently.

    The mirror of the request-side defect, and it bit immediately after that one was guarded:
    `Course.sourcePrompt` and `Course.teachingStyle` were accepted, mapped, and **stored** correctly, then
    omitted from the two places that construct `CourseResponse` with explicit keyword arguments. The
    endpoint answered `200` with `null` for a value the database held, and only a Postgres test that read
    the row back caught it — a test asserting on the create response alone would have passed.

    Why this shape of check: a response model validated with `model_validate(obj, from_attributes=True)`
    cannot have this problem, because the fields are read off the object. It only appears where a route
    builds the model by hand, which some do because they compose derived figures alongside stored ones.
    So the check is against the routes that do that.
    """

    #: Response models built by hand in a route, paired with the module that builds them.
    HAND_BUILT = {
        "CourseResponse": "domains/knowledge/routes.py",
        "CourseListItem": "domains/knowledge/routes.py",
        "TopicLocationResponse": "domains/knowledge/routes.py",
    }

    #: Fields a route legitimately never names, with the reason.
    EXEMPT = {
        # Derived per request under a different local name, or carried by a nested model.
        "createdAt", "updatedAt", "id", "userId", "title", "description",
    }

    def test_every_hand_built_response_field_is_named_by_its_route(self):
        from importlib import import_module

        missing: list[str] = []
        for model_name, route_path in self.HAND_BUILT.items():
            source = (SRC / route_path).read_text()
            module = import_module("src.domains.knowledge.models")
            model = getattr(module, model_name)

            for field_name, field in model.model_fields.items():
                wire_name = field.alias or field_name
                if wire_name in self.EXEMPT:
                    continue
                # Named as a keyword argument, or read off an object. Either proves the route knows about
                # it; neither proves it is correct, which is what the API tests are for.
                if f"{wire_name}=" in source or f".{field_name}" in source:
                    continue
                missing.append(f"{model_name}.{wire_name}")

        assert missing == [], (
            "These response fields are declared but never populated by the route that builds the model, so "
            "they return null regardless of what is stored:\n  " + "\n  ".join(sorted(missing))
        )
