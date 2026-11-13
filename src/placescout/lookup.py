"""One lookup: find the venue, then ask each source what it knows.

Every source is best-effort: a source that fails or opts out is reported in
``sources`` and the others still answer."""

import asyncio
import logging
import time
from dataclasses import dataclass, field

import httpx

from placescout import geoapify, wikimedia
from placescout.fetch import Fetcher, FetchError
from placescout.models import (
    Document,
    Fact,
    Ids,
    LookupResult,
    Match,
    Photo,
    Query,
    SourceReport,
)
from placescout.settings import Settings
from placescout.website import (
    discover_pages,
    page_text,
    parse_structured,
    same_site,
    tdm_reserved,
)

log = logging.getLogger("placescout")

GEOAPIFY_ATTRIBUTION = "Powered by Geoapify"


@dataclass
class Found:
    facts: list[Fact] = field(default_factory=list)
    photos: list[Photo] = field(default_factory=list)
    documents: list[Document] = field(default_factory=list)


class Skipped(Exception):
    """A source chose not to answer (robots.txt, opt-out); not a failure."""


async def lookup(
    query: Query, client: httpx.AsyncClient, fetcher: Fetcher, settings: Settings
) -> LookupResult:
    started = time.monotonic()
    reports: list[SourceReport] = []
    ids = Ids(website=query.website, wikidata=query.wikidata)
    match = Match(confidence="not_searched")
    place: geoapify.Place | None = None

    if not settings.geoapify_api_key:
        reports.append(SourceReport(name="geoapify", status="not_configured"))
    elif query.name:
        match, place = await _run(
            reports,
            "geoapify",
            _geoapify(query, client, settings.geoapify_api_key),
            found=lambda value: value[1] is not None,
        ) or (match, None)
        if place:
            ids.osm = place.osm
            ids.wikidata = ids.wikidata or place.wikidata
            ids.website = ids.website or place.website

    entity = None
    if ids.wikidata:
        entity = await _run(reports, "wikidata", _wikidata(client, ids.wikidata))
        if entity:
            ids.website = ids.website or entity.website

    commons, site = await asyncio.gather(
        _run(reports, "commons", _commons(client, entity.image_file))
        if entity and entity.image_file
        else _none(),
        _run(reports, "website", _website(fetcher, ids.website, settings))
        if ids.website
        else _none(),
    )

    found = Found(facts=list(place.facts) if place else [])
    if commons:
        found.photos.append(commons)
    if site:
        found.facts += site.facts
        found.photos += site.photos
        found.documents += site.documents

    attributions = []
    if any(f.source.name == "OpenStreetMap" for f in found.facts):
        attributions.append(place.attribution if place else geoapify.OSM_ATTRIBUTION)
    if place:
        attributions.append(GEOAPIFY_ATTRIBUTION)

    result = LookupResult(
        query=query,
        match=match,
        ids=ids,
        facts=found.facts,
        photos=found.photos,
        documents=found.documents,
        attributions=attributions,
        sources=reports,
    )
    log.info(
        "lookup name=%r near=%r match=%s facts=%d photos=%d documents=%d sources=%s ms=%d",
        query.name,
        query.near,
        match.confidence,
        len(result.facts),
        len(result.photos),
        len(result.documents),
        ",".join(f"{r.name}:{r.status}" + (f"({r.reason})" if r.reason else "") for r in reports),
        (time.monotonic() - started) * 1000,
    )
    return result


async def _none():
    return None


async def _run(reports: list[SourceReport], name: str, step, found=lambda v: v is not None):
    try:
        value = await step
    except Skipped as skipped:
        reports.append(SourceReport(name=name, status="skipped", reason=str(skipped)))
        return None
    except (httpx.HTTPError, FetchError, ValueError) as error:
        reason = str(error) if isinstance(error, FetchError) else type(error).__name__
        reports.append(SourceReport(name=name, status="failed", reason=reason))
        return None
    reports.append(SourceReport(name=name, status="used" if found(value) else "empty"))
    return value


async def _geoapify(query: Query, client: httpx.AsyncClient, key: str):
    params = geoapify.search_params(query.name, query.near, query.lat, query.lon)
    search = await client.get(
        f"{geoapify.BASE}/v1/geocode/search", params={**params, "apiKey": key}
    )
    search.raise_for_status()
    found = geoapify.pick_match(search.json())
    match = Match(confidence=found.confidence, name=found.name)
    if found.confidence != "single":
        return match, None
    details = await client.get(
        f"{geoapify.BASE}/v2/place-details", params={"id": found.place_id, "apiKey": key}
    )
    details.raise_for_status()
    return match, geoapify.map_details(details.json())


async def _wikidata(client: httpx.AsyncClient, qid: str):
    response = await client.get(wikimedia.WIKIDATA_API, params=wikimedia.entity_params(qid))
    response.raise_for_status()
    return wikimedia.map_entity(response.json(), qid)


async def _commons(client: httpx.AsyncClient, file_name: str):
    response = await client.get(wikimedia.COMMONS_API, params=wikimedia.imageinfo_params(file_name))
    response.raise_for_status()
    return wikimedia.map_imageinfo(response.json())


async def _website(fetcher: Fetcher, url: str, settings: Settings) -> Found:
    if not await fetcher.robots_allows(url):
        raise Skipped("robots.txt")
    home = await fetcher.get_html(url)
    if tdm_reserved(home.html, home.headers):
        raise Skipped("tdm-reservation")

    structured = parse_structured(home.html, home.url)
    pages: dict[str, str] = {}
    if structured.menu_url and same_site(structured.menu_url, home.url):
        pages[structured.menu_url] = "menu"
    for page_url, kind in discover_pages(home.html, home.url, settings.max_pages_per_site):
        pages.setdefault(page_url, kind)
    pages.pop(home.url, None)
    more = list(pages.items())[: settings.max_pages_per_site - 1]

    async def read(page_url: str, kind: str) -> Document | None:
        if not await fetcher.robots_allows(page_url):
            return None
        try:
            page = await fetcher.get_html(page_url)
        except (FetchError, httpx.HTTPError):
            return None
        return page_text(page.html, page.url, kind, settings.max_document_chars)

    documents = [page_text(home.html, home.url, "home", settings.max_document_chars)]
    documents += await asyncio.gather(*(read(u, k) for u, k in more))
    return Found(
        facts=structured.facts,
        photos=structured.photos,
        documents=[d for d in documents if d],
    )
