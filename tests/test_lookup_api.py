import httpx
import pytest
import respx

from placescout.app import create_app
from placescout.settings import Settings

SITE = "https://www.example-bistro.nl/"

HOME = """<html><head>
<meta property="og:image" content="https://www.example-bistro.nl/share.jpg">
<script type="application/ld+json">
{"@context": "https://schema.org", "@type": "Restaurant", "telephone": "+31 20 123 4567",
 "hasMenu": "https://www.example-bistro.nl/menu"}
</script></head>
<body><article><h1>Bistro Example</h1><p>A small bistro on the Damrak serving seasonal Dutch food since 1998.
We cook with vegetables from our own garden and fish from the North Sea.</p></article>
<a href="/openingstijden">Openingstijden</a><a href="/prijzen">Prijzen</a><a href="/blog">Blog</a></body></html>"""

PAGE = """<html><body><article><h1>{title}</h1><p>{body} This paragraph is long enough
for text extraction to keep it as the main content of the page.</p></article></body></html>"""

GEO_SEARCH = {
    "results": [
        {
            "place_id": "pid",
            "result_type": "amenity",
            "name": "Bistro Example",
            "rank": {"confidence": 0.97},
        }
    ]
}
GEO_DETAILS = {
    "features": [
        {
            "properties": {
                "feature_type": "details",
                "opening_hours": "Mo-Su 12:00-22:00",
                "website": SITE,
                "wiki_and_media": {"wikidata": "Q42"},
                "datasource": {
                    "sourcename": "openstreetmap",
                    "attribution": "© OpenStreetMap contributors",
                    "raw": {"osm_id": 123, "osm_type": "n"},
                },
            }
        }
    ]
}
ENTITY = {
    "entities": {"Q42": {"claims": {"P18": [{"mainsnak": {"datavalue": {"value": "Bistro.jpg"}}}]}}}
}
IMAGEINFO = {
    "query": {
        "pages": {
            "1": {
                "imageinfo": [
                    {
                        "thumburl": "https://upload.wikimedia.org/Bistro.jpg",
                        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Bistro.jpg",
                        "extmetadata": {
                            "Artist": {"value": "Jan"},
                            "LicenseShortName": {"value": "CC BY 4.0"},
                            "LicenseUrl": {"value": "https://creativecommons.org/licenses/by/4.0"},
                        },
                    }
                ]
            }
        }
    }
}


async def public_resolver(host):
    return ["93.184.216.34"]


def settings(key="test-key"):
    return Settings(geoapify_api_key=key, contact="ops@example.org")


async def get(params, key="test-key"):
    app = create_app(settings(key), resolve=public_resolver)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/v1/lookup", params=params)


def mock_everything(robots="", home=HOME):
    respx.get("https://api.geoapify.com/v1/geocode/search").respond(json=GEO_SEARCH)
    respx.get("https://api.geoapify.com/v2/place-details").respond(json=GEO_DETAILS)
    respx.get("https://www.wikidata.org/w/api.php").respond(json=ENTITY)
    respx.get("https://commons.wikimedia.org/w/api.php").respond(json=IMAGEINFO)
    respx.get(f"{SITE}robots.txt").respond(200, text=robots)
    respx.get(SITE).respond(200, html=home)
    for path, title in [
        ("menu", "Menu"),
        ("openingstijden", "Openingstijden"),
        ("prijzen", "Prijzen"),
    ]:
        respx.get(f"{SITE}{path}").respond(
            200, html=PAGE.format(title=title, body=f"Our {title.lower()} for this season.")
        )


@respx.mock
async def test_full_lookup_combines_every_source_with_its_credit():
    mock_everything()

    response = await get({"name": "Bistro Example", "near": "Amsterdam"})

    assert response.status_code == 200
    body = response.json()
    assert body["match"] == {"confidence": "single", "name": "Bistro Example"}
    assert body["ids"] == {"osm": "node/123", "wikidata": "Q42", "website": SITE}

    facts = {(f["field"], f["source"]["name"]) for f in body["facts"]}
    assert ("openingHours", "OpenStreetMap") in facts
    assert ("telephone", "www.example-bistro.nl") in facts
    assert all(f["source"]["mayStore"] for f in body["facts"])

    assert [(p["url"], p["mayStore"]) for p in body["photos"]] == [
        ("https://upload.wikimedia.org/Bistro.jpg", True),
        ("https://www.example-bistro.nl/share.jpg", False),
    ]
    assert body["photos"][0]["credit"] == "Jan, CC BY 4.0, via Wikimedia Commons"

    assert [(d["kind"], d["url"]) for d in body["documents"]] == [
        ("home", SITE),
        ("menu", f"{SITE}menu"),
        ("hours", f"{SITE}openingstijden"),
    ]
    assert all(d["mayStore"] is False for d in body["documents"])
    assert "© OpenStreetMap contributors" in body["attributions"]
    assert "Powered by Geoapify" in body["attributions"]


@respx.mock
async def test_never_reads_more_than_three_pages_of_a_site():
    mock_everything()

    await get({"name": "Bistro Example"})

    site_pages = [
        c.request.url
        for c in respx.calls
        if c.request.url.host == "www.example-bistro.nl" and c.request.url.path != "/robots.txt"
    ]
    assert len(site_pages) == 3


@respx.mock
async def test_a_site_that_disallows_our_bot_is_not_read():
    mock_everything(robots="User-agent: PlaceScoutBot\nDisallow: /")

    body = (await get({"name": "Bistro Example"})).json()

    assert body["documents"] == []
    assert all(f["source"]["name"] == "OpenStreetMap" for f in body["facts"])
    assert {"name": "website", "status": "skipped", "reason": "robots.txt"} in body["sources"]
    assert [
        c.request.url.path for c in respx.calls if c.request.url.host == "www.example-bistro.nl"
    ] == ["/robots.txt"]


@respx.mock
async def test_a_site_that_reserves_text_and_data_mining_is_not_used():
    mock_everything(home=HOME.replace("<head>", '<head><meta name="tdm-reservation" content="1">'))

    body = (await get({"name": "Bistro Example"})).json()

    assert body["documents"] == []
    assert all(p["mayStore"] for p in body["photos"])
    assert {"name": "website", "status": "skipped", "reason": "tdm-reservation"} in body["sources"]


@respx.mock
async def test_an_ambiguous_match_returns_no_facts():
    respx.get("https://api.geoapify.com/v1/geocode/search").respond(
        json={
            "results": [
                {"place_id": "a", "result_type": "amenity", "rank": {"confidence": 0.95}},
                {"place_id": "b", "result_type": "amenity", "rank": {"confidence": 0.94}},
            ]
        }
    )

    body = (await get({"name": "Starbucks", "near": "Amsterdam"})).json()

    assert body["match"]["confidence"] == "ambiguous"
    assert body["facts"] == [] and body["photos"] == [] and body["documents"] == []


@respx.mock
async def test_without_a_geoapify_key_a_given_website_still_works():
    mock_everything()

    body = (await get({"website": SITE}, key=None)).json()

    assert body["match"]["confidence"] == "not_searched"
    assert {"name": "geoapify", "status": "not_configured", "reason": None} in body["sources"]
    assert any(f["field"] == "telephone" for f in body["facts"])


@respx.mock
async def test_a_failing_source_is_reported_and_the_rest_still_answer():
    mock_everything()
    respx.get("https://www.wikidata.org/w/api.php").mock(side_effect=httpx.ConnectTimeout("slow"))

    body = (await get({"name": "Bistro Example"})).json()

    assert any(s["name"] == "wikidata" and s["status"] == "failed" for s in body["sources"])
    assert any(f["source"]["name"] == "OpenStreetMap" for f in body["facts"])


@respx.mock
async def test_every_request_identifies_our_bot():
    mock_everything()

    await get({"name": "Bistro Example"})

    for call in respx.calls:
        assert call.request.headers["user-agent"].startswith("PlaceScoutBot/")
        assert "ops@example.org" in call.request.headers["user-agent"]


@pytest.mark.parametrize("params", [{}, {"near": "Paris"}])
async def test_needs_a_name_website_or_wikidata_id(params):
    assert (await get(params)).status_code == 422
