"""One lookup: find the venue, then ask each source what it knows.

Every source is best-effort: a source that fails or opts out is reported in
``sources`` and the others still answer."""

import asyncio
import logging
import time
from dataclasses import dataclass, field, replace

import httpx

from placescout import geoapify, wikimedia
from placescout.fetch import Disallowed, Fetcher, FetchError
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
    name = geoapify.clean_name(query.name) if query.name else ""
    anchor: tuple[tuple[float, float], float] | None = (
        ((query.lat, query.lon), geoapify.NEAR_VENUE_METRES)
        if query.lat is not None and query.lon is not None
        else None
    )

    if not settings.geoapify_api_key:
        reports.append(SourceReport(name="geoapify", status="not_configured"))
    elif name:
        match, place, found_anchor = await _run(
            reports,
            "geoapify",
            _geoapify(query, name, client, settings.geoapify_api_key),
            found=lambda value: value[1] is not None,
        ) or (match, None, None)
        anchor = anchor or found_anchor
        if place:
            ids.osm = place.osm
            ids.wikidata = ids.wikidata or place.wikidata
            ids.website = ids.website or place.website

    entity = None
    if ids.wikidata:
        entity = await _run(reports, "wikidata", _wikidata(client, ids.wikidata))
        if entity and entity.transport_stop and place and not query.wikidata:
            # Geoapify matched the stop named after the place ("Champ de Mars
            # - Tour Eiffel" station for the Champ de Mars): not the place.
            log.info("lookup.dropped_transport_stop wikidata=%s osm=%s", ids.wikidata, ids.osm)
            place, entity, match = None, None, Match(confidence="ambiguous")
            ids = Ids(website=query.website, wikidata=query.wikidata)
    if not entity and not ids.wikidata and name and anchor:
        nearby = await _run_nearby(reports, client, _search_terms(name, query.near), anchor)
        if nearby:
            ids.wikidata = nearby.qid
            entity = nearby.entity
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


async def _geoapify(query: Query, name: str, client: httpx.AsyncClient, key: str):
    """Searches the name with its location; when that is not one place and
    the location names a venue of its own, searches the location alone."""
    words = geoapify.query_words(name, query.near)
    found = await _search(client, key, name, query.near, query.lat, query.lon, words)
    if (
        found.confidence != "single"
        and query.near
        and not geoapify.RELATIVE_LOCATION.search(query.near)
    ):
        alone = await _search(
            client, key, geoapify.clean_name(query.near), None, query.lat, query.lon, words
        )
        if alone.confidence == "single":
            found = alone
        elif alone.anchor is not None:
            # The location alone says better where to look than the name with
            # it ("Inside Notre-Dame, …" found a street in Belgium).
            found = replace(found, anchor=alone.anchor, reach_metres=alone.reach_metres)
    anchor = (found.anchor, found.reach_metres) if found.anchor else None
    match = Match(confidence=found.confidence, name=found.name)
    if found.confidence != "single":
        return match, None, anchor
    details = await client.get(
        f"{geoapify.BASE}/v2/place-details",
        params={"id": found.place_id, "apiKey": key},
        timeout=geoapify.TIMEOUT_SECONDS,
    )
    details.raise_for_status()
    return match, geoapify.map_details(details.json()), anchor


async def _search(client, key, name, near, lat, lon, words) -> geoapify.SearchMatch:
    params = {**geoapify.search_params(name, near, lat, lon), "apiKey": key}
    url = f"{geoapify.BASE}/v1/geocode/search"
    try:
        search = await client.get(url, params=params, timeout=geoapify.TIMEOUT_SECONDS)
    except httpx.TimeoutException:
        # Geoapify is sometimes slow for a moment; one more try, then give up.
        search = await client.get(url, params=params, timeout=geoapify.TIMEOUT_SECONDS)
    search.raise_for_status()
    return geoapify.pick_match(search.json(), words)


def _search_terms(name: str, near: str | None) -> list[str]:
    """The name, then the location's first part when it names a venue
    ("Champ de Mars, Paris" for "Champ de Mars & Eiffel Tower")."""
    terms = [name]
    if near and not geoapify.RELATIVE_LOCATION.search(near):
        first = geoapify.clean_name(near.split(",")[0])
        if first and first.lower() != name.lower():
            terms.append(first)
    return terms


async def _run_nearby(reports, client, terms, anchor):
    """Wikidata by name, accepted only near where the search pointed."""
    point, reach = anchor
    try:
        for term in terms:
            response = await client.get(
                wikimedia.WIKIDATA_API, params=wikimedia.search_params(term)
            )
            response.raise_for_status()
            qids = wikimedia.search_ids(response.json())
            if not qids:
                continue
            response = await client.get(
                wikimedia.WIKIDATA_API, params=wikimedia.entities_params(qids)
            )
            response.raise_for_status()
            nearby = wikimedia.nearest_entity(response.json(), qids, point, reach)
            if nearby:
                reports.append(
                    SourceReport(
                        name="wikidata",
                        status="used",
                        reason=f"found by name, {round(nearby.metres)} m: "
                        f"{nearby.label or nearby.qid}",
                    )
                )
                return nearby
    except (httpx.HTTPError, ValueError) as error:
        reports.append(SourceReport(name="wikidata", status="failed", reason=type(error).__name__))
        return None
    reports.append(
        SourceReport(name="wikidata", status="empty", reason="nothing nearby by that name")
    )
    return None


async def _wikidata(client: httpx.AsyncClient, qid: str):
    response = await client.get(wikimedia.WIKIDATA_API, params=wikimedia.entity_params(qid))
    response.raise_for_status()
    return wikimedia.map_entity(response.json(), qid)


async def _commons(client: httpx.AsyncClient, file_name: str):
    response = await client.get(wikimedia.COMMONS_API, params=wikimedia.imageinfo_params(file_name))
    response.raise_for_status()
    return wikimedia.map_imageinfo(response.json())


async def _website(fetcher: Fetcher, url: str, settings: Settings) -> Found:
    try:
        home = await fetcher.get_html(url)
    except Disallowed as disallowed:
        raise Skipped(str(disallowed)) from disallowed
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
        try:
            page = await fetcher.get_html(page_url)
        except FetchError, httpx.HTTPError:
            return None
        if tdm_reserved(page.html, page.headers):
            return None
        return page_text(page.html, page.url, kind, settings.max_document_chars)

    documents = [page_text(home.html, home.url, "home", settings.max_document_chars)]
    documents += await asyncio.gather(*(read(u, k) for u, k in more))
    return Found(
        facts=structured.facts,
        photos=structured.photos,
        documents=[d for d in documents if d],
    )
