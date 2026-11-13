import httpx
import pytest
import respx

from placescout.fetch import Disallowed, Fetcher, FetchError

PUBLIC = "93.184.216.34"


async def public_resolver(host: str) -> list[str]:
    return [PUBLIC]


def no_robots_txt():
    respx.get("https://example.com/robots.txt").respond(404)


def fetcher(client, resolve=public_resolver, max_bytes=1000):
    return Fetcher(client, resolve=resolve, max_bytes=max_bytes)


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "10.1.2.3", "192.168.0.5", "169.254.169.254", "::1", "fd00::1"]
)
async def test_refuses_sites_that_resolve_to_private_addresses(address):
    async def resolve(host):
        return [address]

    async with httpx.AsyncClient() as client:
        with pytest.raises(FetchError, match="not a public address"):
            await fetcher(client, resolve).get_html("https://example.com/")


@pytest.mark.parametrize(
    "url", ["ftp://example.com/", "https://example.com:8443/", "file:///etc/passwd"]
)
async def test_refuses_other_schemes_and_ports(url):
    async with httpx.AsyncClient() as client:
        with pytest.raises(FetchError):
            await fetcher(client).get_html(url)


@respx.mock
async def test_follows_a_redirect_but_checks_where_it_goes():
    no_robots_txt()
    respx.get("https://example.com/").respond(301, headers={"location": "http://internal.example/"})

    async def resolve(host):
        return ["10.0.0.1"] if host == "internal.example" else [PUBLIC]

    async with httpx.AsyncClient() as client:
        with pytest.raises(FetchError, match="not a public address"):
            await fetcher(client, resolve).get_html("https://example.com/")


@respx.mock
async def test_returns_html_with_its_final_url():
    no_robots_txt()
    respx.get("https://example.com/").respond(302, headers={"location": "/en/"})
    respx.get("https://example.com/en/").respond(200, html="<p>hi</p>")

    async with httpx.AsyncClient() as client:
        page = await fetcher(client).get_html("https://example.com/")

    assert page.url == "https://example.com/en/"
    assert page.html == "<p>hi</p>"


@respx.mock
async def test_refuses_pages_that_are_not_html():
    no_robots_txt()
    respx.get("https://example.com/menu").respond(
        200, content=b"%PDF", headers={"content-type": "application/pdf"}
    )

    async with httpx.AsyncClient() as client:
        with pytest.raises(FetchError, match="not HTML"):
            await fetcher(client).get_html("https://example.com/menu")


@respx.mock
async def test_refuses_pages_over_the_size_cap():
    no_robots_txt()
    respx.get("https://example.com/").respond(200, html="x" * 5000)

    async with httpx.AsyncClient() as client:
        with pytest.raises(FetchError, match="too large"):
            await fetcher(client, max_bytes=1000).get_html("https://example.com/")


@respx.mock
async def test_robots_txt_decides_per_path_for_our_bot():
    respx.get("https://example.com/robots.txt").respond(
        200, text="User-agent: PlaceScoutBot\nDisallow: /private\n\nUser-agent: *\nDisallow: /"
    )

    async with httpx.AsyncClient() as client:
        f = fetcher(client)
        assert await f.robots_allows("https://example.com/menu") is True
        assert await f.robots_allows("https://example.com/private/x") is False


@respx.mock
async def test_missing_robots_txt_allows_everything():
    respx.get("https://example.com/robots.txt").respond(404)

    async with httpx.AsyncClient() as client:
        assert await fetcher(client).robots_allows("https://example.com/menu") is True


@respx.mock
async def test_unreachable_robots_txt_allows_nothing():
    respx.get("https://example.com/robots.txt").respond(503)

    async with httpx.AsyncClient() as client:
        assert await fetcher(client).robots_allows("https://example.com/menu") is False


@respx.mock
async def test_robots_txt_is_read_once_per_site():
    route = respx.get("https://example.com/robots.txt").respond(200, text="")

    async with httpx.AsyncClient() as client:
        f = fetcher(client)
        await f.robots_allows("https://example.com/a")
        await f.robots_allows("https://example.com/b")

    assert route.call_count == 1


@respx.mock
async def test_a_redirect_into_a_disallowed_path_is_not_followed():
    respx.get("https://example.com/robots.txt").respond(
        200, text="User-agent: PlaceScoutBot\nDisallow: /private"
    )
    respx.get("https://example.com/").respond(302, headers={"location": "/private/home"})
    private = respx.get("https://example.com/private/home").respond(200, html="<p>secret</p>")

    async with httpx.AsyncClient() as client:
        with pytest.raises(Disallowed):
            await fetcher(client).get_html("https://example.com/")

    assert not private.called


@respx.mock
async def test_a_redirect_to_another_site_consults_that_sites_robots_txt():
    respx.get("https://example.com/robots.txt").respond(404)
    respx.get("https://example.com/").respond(301, headers={"location": "https://other.example/"})
    respx.get("https://other.example/robots.txt").respond(200, text="User-agent: *\nDisallow: /")
    other = respx.get("https://other.example/").respond(200, html="<p>x</p>")

    async with httpx.AsyncClient() as client:
        with pytest.raises(Disallowed):
            await fetcher(client).get_html("https://example.com/")

    assert not other.called
