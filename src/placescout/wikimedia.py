"""Wikidata (CC0) for a place's image and official website, and Wikimedia
Commons for that image's author and licence. Commons images may be stored,
credited with author, licence and file page."""

from dataclasses import dataclass

import lxml.html

from placescout.geo import metres_between
from placescout.models import Photo

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
PHOTO_WIDTH = 1280


# Wikidata classes (P31) of stops: a station named after a park is not the
# park, and its sign is not the park's photo.
TRANSPORT_STOPS = {
    "Q55488",  # railway station
    "Q1793804",  # station building
    "Q22808403",  # underground station
    "Q928830",  # metro station
    "Q953806",  # bus stop
    "Q2175765",  # tram stop
    "Q719456",  # station
    "Q18543139",  # central station
    "Q1339195",  # station building
}


@dataclass
class Entity:
    image_file: str | None = None
    website: str | None = None
    transport_stop: bool = False


@dataclass
class Nearby:
    """A Wikidata item found by name, and how far it is from the anchor."""

    qid: str
    label: str | None
    metres: float
    entity: Entity


def search_params(name: str) -> dict:
    return {
        "action": "wbsearchentities",
        "search": name,
        "language": "en",
        "uselang": "en",
        "type": "item",
        "limit": 7,
        "format": "json",
    }


def search_ids(data: dict) -> list[str]:
    return [hit["id"] for hit in data.get("search") or [] if isinstance(hit.get("id"), str)]


def entities_params(qids: list[str]) -> dict:
    return {
        "action": "wbgetentities",
        "ids": "|".join(qids),
        "props": "claims|labels",
        "languages": "en",
        "format": "json",
    }


def nearest_entity(
    data: dict, qids: list[str], anchor: tuple[float, float], max_metres: float
) -> Nearby | None:
    """The candidate closest to the anchor, if any is within reach. A name
    alone is not enough: a same-named place in another city must not match."""
    entities = data.get("entities") or {}
    best: Nearby | None = None
    for qid in qids:
        item = entities.get(qid) or {}
        point = _coordinates(item.get("claims") or {})
        if point is None:
            continue
        metres = metres_between(anchor, point)
        entity = map_entity({"entities": {qid: item}}, qid)
        if entity.transport_stop:
            continue
        if metres <= max_metres and (best is None or metres < best.metres):
            label = ((item.get("labels") or {}).get("en") or {}).get("value")
            best = Nearby(qid, label, metres, entity)
    return best


def _coordinates(claims: dict) -> tuple[float, float] | None:
    for claim in claims.get("P625") or []:
        value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value") or {}
        lat, lon = value.get("latitude"), value.get("longitude")
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            return (lat, lon)
    return None


def entity_params(qid: str) -> dict:
    return {"action": "wbgetentities", "ids": qid, "props": "claims", "format": "json"}


def imageinfo_params(file_name: str) -> dict:
    return {
        "action": "query",
        "titles": f"File:{file_name}",
        "prop": "imageinfo",
        "iiprop": "url|extmetadata",
        "iiurlwidth": PHOTO_WIDTH,
        "format": "json",
    }


def map_entity(data: dict, qid: str) -> Entity:
    claims = ((data.get("entities") or {}).get(qid) or {}).get("claims") or {}
    classes = {
        ((c.get("mainsnak") or {}).get("datavalue") or {}).get("value", {}).get("id")
        for c in claims.get("P31") or []
        if isinstance(((c.get("mainsnak") or {}).get("datavalue") or {}).get("value"), dict)
    }
    return Entity(
        image_file=_first(claims, "P18"),
        website=_first(claims, "P856"),
        transport_stop=bool(classes & TRANSPORT_STOPS),
    )


def _first(claims: dict, prop: str) -> str | None:
    for claim in claims.get(prop) or []:
        value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def map_imageinfo(data: dict) -> Photo | None:
    pages = ((data.get("query") or {}).get("pages") or {}).values()
    info = next((p["imageinfo"][0] for p in pages if p.get("imageinfo")), None)
    if info is None:
        return None
    meta = info.get("extmetadata") or {}
    license_name = _meta(meta, "LicenseShortName")
    page_url = info.get("descriptionurl")
    url = info.get("thumburl") or info.get("url")
    # Wikimedia gives no warranty on licence data; without it we cannot credit
    # the photo properly, so it is not offered.
    if not (license_name and page_url and url):
        return None
    author = _meta(meta, "Artist")
    return Photo(
        url=url,
        page_url=page_url,
        credit=", ".join(p for p in (author, license_name, "via Wikimedia Commons") if p),
        may_store=True,
        author=author,
        license=license_name,
        license_url=_meta(meta, "LicenseUrl"),
    )


def _meta(meta: dict, key: str) -> str | None:
    value = (meta.get(key) or {}).get("value")
    if not isinstance(value, str) or not value.strip():
        return None
    # Artist is HTML (usually a link to the user page); keep the name only.
    return lxml.html.fromstring(value).text_content().strip() or None
