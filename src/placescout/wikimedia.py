"""Wikidata (CC0) for a place's image and official website, and Wikimedia
Commons for that image's author and licence. Commons images may be stored,
credited with author, licence and file page."""

from dataclasses import dataclass

import lxml.html

from placescout.models import Photo

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
PHOTO_WIDTH = 1280


@dataclass
class Entity:
    image_file: str | None = None
    website: str | None = None


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
    return Entity(image_file=_first(claims, "P18"), website=_first(claims, "P856"))


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
