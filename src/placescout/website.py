"""What a place's own website says about itself: schema.org data, Open Graph
tags and the plain text of a few relevant pages.

Facts are storable (facts are not copyrighted, and one venue's facts are not a
substantial part of a database). Images and page text are not: images are
returned as links for the caller to show from the site's own server, and text
only for the caller's AI to read."""

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urldefrag, urljoin, urlsplit

import extruct
import lxml.html
import trafilatura

from placescout.models import Document, Fact, Photo, Source

# Items that describe the website itself rather than the place.
NOT_A_PLACE = {
    "WebSite",
    "WebPage",
    "BreadcrumbList",
    "ItemList",
    "SiteNavigationElement",
    "SearchAction",
    "ImageObject",
    "Article",
    "NewsArticle",
    "BlogPosting",
    "Person",
    "FAQPage",
    "Question",
    "Answer",
    "VideoObject",
    "Event",
}

DAYS = {
    "monday": "Mon",
    "tuesday": "Tue",
    "wednesday": "Wed",
    "thursday": "Thu",
    "friday": "Fri",
    "saturday": "Sat",
    "sunday": "Sun",
    "publicholidays": "Holidays",
}

# Link text or path words that point at a menu, opening hours or prices,
# in the languages travellers in Europe meet most.
LINK_KINDS: list[tuple[str, tuple[str, ...]]] = [
    ("menu", ("menu", "menukaart", "carte", "speisekarte", "carta", "menù")),
    (
        "hours",
        (
            "opening",
            "openingstijden",
            "openingsuren",
            "hours",
            "horaires",
            "öffnungszeiten",
            "oeffnungszeiten",
            "horario",
            "orari",
        ),
    ),
    (
        "prices",
        (
            "prices",
            "pricing",
            "prijzen",
            "tarifs",
            "preise",
            "precios",
            "prezzi",
            "tickets",
            "billets",
            "rates",
        ),
    ),
]


@dataclass
class Structured:
    facts: list[Fact] = field(default_factory=list)
    photos: list[Photo] = field(default_factory=list)
    menu_url: str | None = None


def site_name(url: str) -> str:
    return urlsplit(url).hostname or url


def parse_structured(html: str, page_url: str) -> Structured:
    data = extruct.extract(
        html, base_url=page_url, syntaxes=["json-ld", "microdata", "opengraph"], uniform=True
    )
    source = Source(name=site_name(page_url), url=page_url, may_store=True)
    result = Structured()

    for item in _places(data.get("json-ld", []) + data.get("microdata", [])):
        values = _place_values(item, page_url)
        if values:
            result.menu_url = values.get("menuUrl")
            result.facts = [Fact(field=k, value=v, source=source) for k, v in values.items()]
            images = _image_urls(item.get("image"), page_url)
            break
    else:
        images = []

    og_images = [
        urljoin(page_url, og["og:image"])
        for og in data.get("opengraph", [])
        if isinstance(og.get("og:image"), str) and og["og:image"].strip()
    ]
    for url in dict.fromkeys(og_images + images):
        result.photos.append(
            Photo(url=url, page_url=page_url, credit=site_name(page_url), may_store=False)
        )
    return result


def _places(items: list[Any]):
    for item in items:
        if not isinstance(item, dict):
            continue
        if "@graph" in item:
            yield from _places(item["@graph"])
            continue
        types = item.get("@type", [])
        types = {types} if isinstance(types, str) else set(types)
        if types and not types & NOT_A_PLACE:
            yield item


def _place_values(item: dict, page_url: str) -> dict[str, str]:
    values = {
        "telephone": _text(item.get("telephone")),
        "priceRange": _text(item.get("priceRange")),
        "address": _address(item.get("address")),
        "openingHours": _opening_hours(item),
        "menuUrl": _menu_url(item.get("hasMenu"), page_url),
    }
    return {k: v for k, v in values.items() if v}


def _text(value: Any) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, (str, int, float)):
        return str(value).strip() or None
    return None


def _address(value: Any) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, str):
        return value.strip() or None
    if not isinstance(value, dict):
        return None
    locality = " ".join(
        p for p in (_text(value.get("postalCode")), _text(value.get("addressLocality"))) if p
    )
    parts = [_text(value.get("streetAddress")), locality or None]
    return ", ".join(p for p in parts if p) or None


def _opening_hours(item: dict) -> str | None:
    plain = item.get("openingHours")
    if isinstance(plain, str) and plain.strip():
        return plain.strip()
    if isinstance(plain, list) and any(isinstance(p, str) and p.strip() for p in plain):
        return "; ".join(p.strip() for p in plain if isinstance(p, str) and p.strip())

    specs = item.get("openingHoursSpecification")
    specs = [specs] if isinstance(specs, dict) else specs or []
    lines = []
    for spec in specs:
        if not isinstance(spec, dict):
            continue
        days = spec.get("dayOfWeek")
        days = [days] if isinstance(days, str) else days or []
        names = [DAYS.get(str(d).rsplit("/", 1)[-1].lower()) for d in days]
        opens, closes = _hhmm(spec.get("opens")), _hhmm(spec.get("closes"))
        if all(names) and names and opens and closes:
            lines.append(f"{', '.join(names)} {opens}-{closes}")
    return "; ".join(lines) or None


def _hhmm(value: Any) -> str | None:
    text = _text(value)
    return text[:5] if text and re.match(r"^\d{2}:\d{2}", text) else None


def _menu_url(value: Any, page_url: str) -> str | None:
    if isinstance(value, dict):
        value = value.get("url") or value.get("@id")
    text = _text(value)
    return urljoin(page_url, text) if text and not text.startswith("#") else None


def _image_urls(value: Any, page_url: str) -> list[str]:
    values = value if isinstance(value, list) else [value]
    urls = []
    for v in values:
        if isinstance(v, dict):
            v = v.get("url") or v.get("contentUrl")
        if isinstance(v, str) and v.strip():
            urls.append(urljoin(page_url, v.strip()))
    return urls


def discover_links(html: str, page_url: str, limit: int) -> list[str]:
    """Same-site links to menu, hours or price pages, in page order. PDFs are
    left out: v1 reads HTML only."""
    return [url for url, _ in discover_pages(html, page_url, limit)]


def discover_pages(html: str, page_url: str, limit: int) -> list[tuple[str, str]]:
    try:
        root = lxml.html.fromstring(html)
    except lxml.etree.ParserError, ValueError:
        return []
    host = _bare_host(page_url)
    home = urldefrag(page_url).url
    found: dict[str, str] = {}
    for anchor in root.iter("a"):
        href = anchor.get("href")
        if not href:
            continue
        url = urldefrag(urljoin(page_url, href)).url
        parts = urlsplit(url)
        if (
            parts.scheme not in ("http", "https")
            or _bare_host(url) != host
            or url in (home, home.rstrip("/"))
            or parts.path.lower().endswith(".pdf")
            or url in found
        ):
            continue
        words = f"{anchor.text_content()} {parts.path}".lower()
        kind = next((k for k, keys in LINK_KINDS if any(key in words for key in keys)), None)
        if kind:
            found[url] = kind
            if len(found) == limit:
                break
    return list(found.items())


def same_site(url: str, other: str) -> bool:
    return _bare_host(url) == _bare_host(other)


def _bare_host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.")


def tdm_reserved(html: str, headers: dict[str, str]) -> bool:
    """True when the site opts out of text and data mining (TDMRep header or
    meta tag), which the EU exception we rely on requires us to honour."""
    if any(k.lower() == "tdm-reservation" and v.strip() == "1" for k, v in headers.items()):
        return True
    try:
        root = lxml.html.fromstring(html)
    except lxml.etree.ParserError, ValueError:
        return False
    return any(
        (meta.get("name") or "").strip().lower() == "tdm-reservation"
        and (meta.get("content") or "").strip() == "1"
        for meta in root.iter("meta")
    )


def page_text(html: str, url: str, kind: str, max_chars: int) -> Document | None:
    text = trafilatura.extract(html, url=url, include_comments=False, include_tables=True)
    if not text or not text.strip():
        return None
    return Document(url=url, kind=kind, text=text.strip()[:max_chars])
