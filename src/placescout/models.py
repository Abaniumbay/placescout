"""The v1 response format. Every fact and photo says where it came from and
whether the caller may keep it (``mayStore``); see README.md."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


def now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


class Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class Source(Model):
    name: str
    url: str
    license: str | None = None
    attribution: str | None = None
    fetched_at: datetime = Field(default_factory=now)
    may_store: bool


class Fact(Model):
    field: str
    value: str
    source: Source


class Photo(Model):
    url: str
    page_url: str
    credit: str
    may_store: bool
    author: str | None = None
    license: str | None = None
    license_url: str | None = None


class Document(Model):
    """Plain text of a page, for the caller's own AI to read. Never storable:
    it is the site's own wording."""

    url: str
    kind: Literal["home", "menu", "hours", "prices"]
    text: str
    fetched_at: datetime = Field(default_factory=now)
    may_store: Literal[False] = False


class Match(Model):
    confidence: Literal["single", "ambiguous", "none", "not_searched"]
    name: str | None = None


class Ids(Model):
    osm: str | None = None
    wikidata: str | None = None
    website: str | None = None


class SourceReport(Model):
    name: str
    status: Literal["used", "empty", "skipped", "failed", "not_configured"]
    reason: str | None = None


class Query(Model):
    name: str | None = None
    near: str | None = None
    lat: float | None = None
    lon: float | None = None
    website: str | None = None
    wikidata: str | None = None


class LookupResult(Model):
    query: Query
    match: Match
    ids: Ids
    facts: list[Fact] = []
    photos: list[Photo] = []
    documents: list[Document] = []
    attributions: list[str] = []
    sources: list[SourceReport] = []
