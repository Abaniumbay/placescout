"""Fetching pages from websites we were pointed at, politely and safely.

Websites come from callers and from open data, so every URL (and every
redirect) is checked to resolve to a public address before it is requested:
PlaceScout must not become a way to reach the network it runs in."""

import asyncio
import ipaddress
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx
from protego import Protego

from placescout.settings import Settings

BOT_NAME = "PlaceScoutBot"
MAX_REDIRECTS = 3
MAX_ROBOTS_BYTES = 500_000  # RFC 9309 asks crawlers to read at least 500 KiB
HTML_TYPES = ("text/html", "application/xhtml+xml")

Resolver = Callable[[str], Awaitable[list[str]]]


class FetchError(Exception):
    pass


@dataclass
class Page:
    url: str
    html: str
    headers: dict[str, str]


async def resolve_host(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, None)
    return list({info[4][0] for info in infos})


class Fetcher:
    """One per lookup: robots.txt is cached for the lookup's lifetime only."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        resolve: Resolver = resolve_host,
        max_bytes: int = Settings.max_page_bytes,
    ):
        self._client = client
        self._resolve = resolve
        self._max_bytes = max_bytes
        self._robots: dict[str, Protego | bool] = {}

    async def get_html(self, url: str) -> Page:
        async with self._get(url) as response:
            if response.status_code != 200:
                raise FetchError(f"HTTP {response.status_code}")
            content_type = response.headers.get("content-type", "").split(";")[0].strip()
            if content_type not in HTML_TYPES:
                raise FetchError("not HTML")
            body = await _read(response, self._max_bytes)
            return Page(
                url=str(response.url),
                html=body.decode(response.encoding or "utf-8", errors="replace"),
                headers=dict(response.headers),
            )

    async def robots_allows(self, url: str) -> bool:
        """RFC 9309: a missing robots.txt (4xx) allows everything; one we
        cannot read (5xx, timeout, refused) allows nothing."""
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            self._robots[origin] = await self._read_robots(origin)
        rules = self._robots[origin]
        return rules if isinstance(rules, bool) else rules.can_fetch(url, BOT_NAME)

    async def _read_robots(self, origin: str) -> Protego | bool:
        try:
            async with self._get(f"{origin}/robots.txt") as response:
                if 400 <= response.status_code < 500:
                    return True
                if response.status_code != 200:
                    return False
                body = await _read(response, MAX_ROBOTS_BYTES, truncate=True)
                return Protego.parse(body.decode("utf-8", errors="replace"))
        except (FetchError, httpx.HTTPError):
            return False

    @asynccontextmanager
    async def _get(self, url: str) -> AsyncIterator[httpx.Response]:
        """Follows up to MAX_REDIRECTS redirects, checking every hop."""
        for _ in range(MAX_REDIRECTS + 1):
            await self.check(url)
            response = await self._client.send(self._client.build_request("GET", url), stream=True)
            if not response.is_redirect:
                try:
                    yield response
                finally:
                    await response.aclose()
                return
            await response.aclose()
            url = urljoin(url, response.headers["location"])
        raise FetchError("too many redirects")

    async def check(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise FetchError("only http and https URLs")
        if parts.port not in (None, 80, 443):
            raise FetchError("only ports 80 and 443")
        try:
            addresses = await self._resolve(parts.hostname)
        except OSError as error:
            raise FetchError("cannot resolve host") from error
        if not addresses or not all(_is_public(a) for a in addresses):
            raise FetchError("not a public address")


def _is_public(address: str) -> bool:
    try:
        return ipaddress.ip_address(address.split("%")[0]).is_global
    except ValueError:
        return False


async def _read(response: httpx.Response, limit: int, truncate: bool = False) -> bytes:
    chunks, size = [], 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > limit:
            if truncate:
                chunks.append(chunk[: limit - (size - len(chunk))])
                break
            raise FetchError("too large")
        chunks.append(chunk)
    return b"".join(chunks)
