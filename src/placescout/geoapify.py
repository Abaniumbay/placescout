"""Geoapify: OpenStreetMap data with terms that allow storing results
("Cache/store results with no limits"). Finds the venue and gives its OSM and
Wikidata ids, website, phone, address and opening hours."""

from dataclasses import dataclass, field

from placescout.models import Fact, Source

BASE = "https://api.geoapify.com"

# A wrong venue's facts look authoritative and nobody re-checks them, so only
# a confident, unique venue counts as a match.
MIN_CONFIDENCE = 0.8
VENUE_TYPES = {"amenity", "building"}

OSM_TYPES = {"n": "node", "w": "way", "r": "relation"}
OSM_ATTRIBUTION = "© OpenStreetMap contributors"


@dataclass
class SearchMatch:
    confidence: str
    place_id: str | None = None
    name: str | None = None


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


def pick_match(search: dict) -> SearchMatch:
    results = search.get("results") or []
    if not results:
        return SearchMatch("none")

    def confident_venue(r: dict) -> bool:
        return (
            r.get("result_type") in VENUE_TYPES
            and (r.get("rank") or {}).get("confidence", 0) >= MIN_CONFIDENCE
        )

    best = results[0]
    if not confident_venue(best) or any(confident_venue(r) for r in results[1:]):
        return SearchMatch("ambiguous")
    return SearchMatch("single", best.get("place_id"), best.get("name"))


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
