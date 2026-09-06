"""Shared response-model base.

Every table in this database names its columns in camelCase and every SQLAlchemy model maps them
onto snake_case attributes. A response model therefore has to read one spelling and emit the other,
and the two ways of getting that wrong have both now happened in production code:

- Reading the *column* name off the instance — ``user.paymentProvider`` — which raises
  ``AttributeError`` and surfaces as a `500`. Swept out of the repository and guarded by
  ``tests/test_orm_attribute_names.py``.
- Declaring the *field* in camelCase on a model with ``from_attributes=True``, so validation looks
  for an attribute that does not exist. `TopicResponse` did this and made `POST .../topics`
  answer `500`, while `estimatedHours` — which has a default — was quietly dropped instead.

The second is the nastier one, because a field with a default fails silently: the endpoint returns
`200` with a null where the value should be. It is also invisible to the attribute-name guard,
which greps for reads and finds nothing to read.

``CamelModel`` closes both by construction. Fields are declared snake_case, matching the ORM, and
the alias generator emits camelCase on the wire. ``populate_by_name`` keeps the many call sites that
construct these models with camelCase keyword arguments working unchanged.

Lives here rather than in one domain because two domains now need it, and a copy in each is how the
two would drift.
"""

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """Base for schemas that read SQLAlchemy objects and serialize camelCase JSON.

    Supports both:

    - ORM attribute reading: ``CamelModel.model_validate(orm_object)``
    - Direct construction with either spelling: ``CamelModel(userId="abc")`` or
      ``CamelModel(user_id="abc")``

    One caveat worth knowing: ``to_camel`` lowercases acronyms, so ``is_ai_generated`` becomes
    ``isAiGenerated``. Where a published field name disagrees with the generator, pin it with an
    explicit ``Field(alias=...)`` rather than letting the contract quietly change.
    """

    model_config = ConfigDict(
        from_attributes=True,
        alias_generator=to_camel,
        populate_by_name=True,
    )


ResponseT = TypeVar("ResponseT")


class PaginatedResponse(CamelModel, Generic[ResponseT]):
    """The canonical pagination envelope for list endpoints.

    ``pages`` rather than a ``hasMore`` boolean, because it answers strictly more: a pager needs the
    total, and "is there another page" is ``page < pages``. The reverse does not hold.

    Here rather than in one domain for the same reason as ``CamelModel``: more than one domain
    returns pages, and a copy per domain is how two envelopes with different field names appear.
    """

    items: list[ResponseT]
    total: int
    page: int
    page_size: int
    pages: int


class CursorPage(CamelModel, Generic[ResponseT]):
    """The canonical envelope for list endpoints that page by cursor rather than by number.

    A chat thread pages backwards from the newest message by id, so ``page`` and ``pages`` have no
    meaning for it — filling them in with ``1`` to reuse ``PaginatedResponse`` would publish a
    fabricated measurement, and the caller cannot tell a fabricated page number from a real one.

    ``has_more`` rather than leaving the caller to compare ``len(items)`` against ``total``: with a
    cursor, ``total`` counts the whole thread while ``items`` counts one window into the middle of it,
    so that comparison does not answer the question. ``next_cursor`` is the value to pass back as
    ``before``, and is ``None`` exactly when ``has_more`` is false.

    Here rather than in one domain for the same reason as ``PaginatedResponse``.
    """

    items: list[ResponseT]
    total: int
    has_more: bool
    next_cursor: str | None = None
