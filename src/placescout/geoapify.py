"""Geoapify: OpenStreetMap data with terms that allow storing results
("Cache/store results with no limits"). Finds the venue and gives its OSM and
Wikidata ids, website, phone, address and opening hours."""

import re
import unicodedata
from dataclasses import dataclass, field

from placescout.geo import metres_between
from placescout.models import Fact, Source

BASE = "https://api.geoapify.com"
# Geoapify's geocoder takes 5-12 s for long free-text queries; a site gets 10.
TIMEOUT_SECONDS = 20

# A wrong venue's facts look authoritative and nobody re-checks them, so only
# a confident, unique venue counts as a match.
MIN_CONFIDENCE = 0.8
VENUE_TYPES = {"amenity", "building"}
# Geoapify often returns one landmark twice ("Field of Mars" and "Champ de
# Mars - Tour Eiffel"); strong venues this close to the best are the same place.
SAME_PLACE_METRES = 300
# How far a Wikidata item found by name may be from where the search pointed:
# close for a venue or street, wider for a district or city.
POINT_TYPES = VENUE_TYPES | {"street"}
NEAR_VENUE_METRES = 2_000
NEAR_AREA_METRES = 15_000
# A location that only says where something is relative to another place
# ("Near Hôtel Eiffel Segur", "Opéra → hotel") names no venue of its own.
# Shorter words ("de", "la", "the") say nothing about which place is meant.
MIN_WORD = 4
RELATIVE_LOCATION = re.compile(r"^\s*(near|close to|around|between|next to)\b|→|->", re.I)

OSM_TYPES = {"n": "node", "w": "way", "r": "relation"}
OSM_ATTRIBUTION = "© OpenStreetMap contributors"


@dataclass
class SearchMatch:
    confidence: str
    place_id: str | None = None
    name: str | None = None
    # Where the best result is, matched or not: what a by-name search elsewhere
    # must be near to count, and how near (a city is wider than a venue).
    anchor: tuple[float, float] | None = None
    reach_metres: float = 0


@dataclass
class Place:
    facts: list[Fact] = field(default_factory=list)
    osm: str | None = None
    wikidata: str | None = None
    website: str | None = None
    attribution: str = OSM_ATTRIBUTION


def search_params(name: str, near: str | None, lat: float | None, lon: float | None) -> dict:
    params = {"text": ", ".join(p for p in (name, near) if p), "format": "json", "limit": 2}
    if lat is not None and lon is not None:
        params["bias"] = f"proximity:{lon},{lat}"
    return params


def clean_name(text: str) -> str:
    """Drops emoji, symbols and a trailing parenthetical: "🎭 Palais Garnier
    (Opéra)" searches as "Palais Garnier"."""
    kept = "".join(
        ch
        for ch in text
        if not unicodedata.category(ch).startswith("S")
        and unicodedata.category(ch) not in ("Mn", "Cf")
    )
    kept = re.sub(r"\s*\([^)]*\)\s*$", "", kept)
    return re.sub(r"\s+", " ", kept).strip()


def query_words(name: str | None, near: str | None) -> set[str]:
    """The words a match's name must share one of: the name's, and the
    location's except its last part, which is usually the city ("Paris" is in
    too many place names to count)."""
    parts = [clean_name(name or "")]
    if near:
        segments = [p for p in near.split(",") if p.strip()]
        parts += segments[:-1] if len(segments) > 1 else segments
    return {w for p in parts for w in _words(p)}


def _words(text: str) -> set[str]:
    folded = "".join(
        ch for ch in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(ch)
    )
    return {w for w in re.findall(r"\w+", folded) if len(w) >= MIN_WORD}


def pick_match(search: dict, query_words: set[str] | None = None) -> SearchMatch:
    # Caravan asks about places to be, never about the stop next to them: a
    # station named after the park must not become the park.
    results = [
        r
        for r in search.get("results") or []
        if not str(r.get("category") or "").startswith("public_transport")
    ]
    if not results:
        return SearchMatch("none")

    best = results[0]
    anchor = _point(best)
    reach = NEAR_VENUE_METRES if best.get("result_type") in POINT_TYPES else NEAR_AREA_METRES

    def confident_venue(r: dict) -> bool:
        return (
            r.get("result_type") in VENUE_TYPES
            and (r.get("rank") or {}).get("confidence", 0) >= MIN_CONFIDENCE
        )

    def elsewhere(r: dict) -> bool:
        point = _point(r)
        return not (anchor and point and metres_between(anchor, point) <= SAME_PLACE_METRES)

    def named_like_query(r: dict) -> bool:
        return query_words is None or bool(_words(r.get("name") or "") & query_words)

    rivals = [r for r in results[1:] if confident_venue(r) and elsewhere(r)]
    if not confident_venue(best) or not named_like_query(best) or rivals:
        return SearchMatch("ambiguous", anchor=anchor, reach_metres=reach)
    return SearchMatch("single", best.get("place_id"), best.get("name"), anchor, reach)


def _point(result: dict) -> tuple[float, float] | None:
    lat, lon = result.get("lat"), result.get("lon")
    return (lat, lon) if isinstance(lat, (int, float)) and isinstance(lon, (int, float)) else None


def map_details(details: dict) -> Place | None:
    features = details.get("features") or []
    props = next(
        (
            f.get("properties") or {}
            for f in features
            if (f.get("properties") or {}).get("feature_type", "details") == "details"
        ),
        None,
    )
    if props is None:
        return None

    datasource = props.get("datasource") or {}
    raw = datasource.get("raw") or {}
    osm_type = OSM_TYPES.get(str(raw.get("osm_type", "")))
    osm = f"{osm_type}/{raw['osm_id']}" if osm_type and raw.get("osm_id") else None
    is_osm = datasource.get("sourcename", "openstreetmap") == "openstreetmap"
    attribution = datasource.get("attribution") or OSM_ATTRIBUTION
    source = Source(
        name="OpenStreetMap" if is_osm else datasource.get("sourcename", "Geoapify"),
        url=f"https://www.openstreetmap.org/{osm}"
        if osm
        else datasource.get("url", "https://www.openstreetmap.org/copyright"),
        license="ODbL-1.0" if is_osm else datasource.get("license"),
        attribution=attribution,
        may_store=True,
    )

    values = {
        "openingHours": props.get("opening_hours"),
        "telephone": (props.get("contact") or {}).get("phone"),
        "address": props.get("formatted"),
        "website": props.get("website"),
    }
    return Place(
        facts=[
            Fact(field=k, value=v.strip(), source=source)
            for k, v in values.items()
            if isinstance(v, str) and v.strip()
        ],
        osm=osm,
        wikidata=(props.get("wiki_and_media") or {}).get("wikidata"),
        website=props.get("website"),
        attribution=attribution,
    )
