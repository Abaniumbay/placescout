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


@respx.mock
async def test_a_linked_page_that_reserves_text_and_data_mining_is_not_returned():
    mock_everything()
    respx.get(f"{SITE}menu").respond(
        200, html=PAGE.format(title="Menu", body="Our menu."), headers={"tdm-reservation": "1"}
    )

    body = (await get({"name": "Bistro Example"})).json()

    assert [d["kind"] for d in body["documents"]] == ["home", "hours"]


GARNIER = (48.8720, 2.3316)


def wikidata_router(search_hits, entities):
    def respond(request):
        action = request.url.params.get("action")
        if action == "wbsearchentities":
            return httpx.Response(200, json={"search": [{"id": q} for q in search_hits]})
        return httpx.Response(200, json={"entities": entities})

    return respond


def coords_entity(lat, lon, image):
    return {
        "claims": {
            "P625": [{"mainsnak": {"datavalue": {"value": {"latitude": lat, "longitude": lon}}}}],
            "P18": [{"mainsnak": {"datavalue": {"value": image}}}],
        },
        "labels": {"en": {"value": "Palais Garnier"}},
    }


@respx.mock
async def test_search_text_is_cleaned_of_emoji_and_parentheticals():
    route = respx.get("https://api.geoapify.com/v1/geocode/search").respond(json={"results": []})

    await get({"name": "🎭 Palais Garnier (Opéra)", "near": "Paris"})

    assert route.calls[0].request.url.params["text"] == "Palais Garnier, Paris"


@respx.mock
async def test_the_location_alone_is_tried_when_name_and_location_do_not_match_one_place():
    def respond(request):
        text = request.url.params["text"]
        if text == "Palais Garnier, Place de l'Opéra, Paris":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "place_id": "pg",
                            "result_type": "amenity",
                            "name": "Palais Garnier",
                            "rank": {"confidence": 1},
                            "lat": GARNIER[0],
                            "lon": GARNIER[1],
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"results": []})

    respx.get("https://api.geoapify.com/v1/geocode/search").mock(side_effect=respond)
    respx.get("https://api.geoapify.com/v2/place-details").respond(json=GEO_DETAILS)
    mock_site_and_commons()

    body = (
        await get({"name": "Opera night", "near": "Palais Garnier, Place de l'Opéra, Paris"})
    ).json()

    assert body["match"] == {"confidence": "single", "name": "Palais Garnier"}


@respx.mock
async def test_an_ambiguous_place_still_gets_its_photo_from_a_nearby_wikidata_item():
    respx.get("https://api.geoapify.com/v1/geocode/search").respond(
        json={
            "results": [
                {
                    "place_id": "a",
                    "result_type": "amenity",
                    "rank": {"confidence": 1},
                    "lat": 48.8714,
                    "lon": 2.3320,
                },
                {
                    "place_id": "b",
                    "result_type": "amenity",
                    "rank": {"confidence": 0.9},
                    "lat": 48.86,
                    "lon": 2.35,
                },
            ]
        }
    )
    respx.get("https://www.wikidata.org/w/api.php").mock(
        side_effect=wikidata_router(
            ["Q9", "Q191710"],
            {
                "Q9": coords_entity(45.0, 7.0, "Elsewhere.jpg"),
                "Q191710": coords_entity(*GARNIER, "Bistro.jpg"),
            },
        )
    )
    respx.get("https://commons.wikimedia.org/w/api.php").respond(json=IMAGEINFO)

    body = (await get({"name": "Palais Garnier", "near": "Paris"})).json()

    assert body["match"]["confidence"] == "ambiguous"
    assert body["facts"] == []
    assert body["ids"]["wikidata"] == "Q191710"
    assert [p["mayStore"] for p in body["photos"]] == [True]
    report = next(s for s in body["sources"] if s["name"] == "wikidata")
    assert report["status"] == "used" and report["reason"].startswith("found by name")


@respx.mock
async def test_without_any_location_a_name_search_is_never_trusted():
    respx.get("https://api.geoapify.com/v1/geocode/search").respond(json={"results": []})
    wikidata = respx.get("https://www.wikidata.org/w/api.php").respond(
        json={"search": [{"id": "Q1"}]}
    )

    body = (await get({"name": "Palais Garnier"})).json()

    assert not wikidata.called
    assert body["photos"] == []


def mock_site_and_commons():
    respx.get("https://www.wikidata.org/w/api.php").respond(json=ENTITY)
    respx.get("https://commons.wikimedia.org/w/api.php").respond(json=IMAGEINFO)
    respx.get(f"{SITE}robots.txt").respond(200, text="")
    respx.get(SITE).respond(200, html=HOME)
    for path in ("menu", "openingstijden", "prijzen"):
        respx.get(f"{SITE}{path}").respond(200, html=PAGE.format(title=path, body="x"))


@respx.mock
async def test_the_location_alone_decides_where_to_look_when_neither_search_is_one_place():
    # "Inside Notre-Dame, Notre-Dame de Paris, …" finds a street in Belgium;
    # the location alone finds the cathedral, with low confidence.
    def respond(request):
        if request.url.params["text"].startswith("Inside"):
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "place_id": "be",
                            "result_type": "street",
                            "rank": {"confidence": 0.5},
                            "lat": 50.60,
                            "lon": 4.33,
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "place_id": "nd",
                        "result_type": "amenity",
                        "rank": {"confidence": 0.33},
                        "lat": 48.8529,
                        "lon": 2.3501,
                    }
                ]
            },
        )

    respx.get("https://api.geoapify.com/v1/geocode/search").mock(side_effect=respond)
    respx.get("https://www.wikidata.org/w/api.php").mock(
        side_effect=wikidata_router(
            ["Q2981"], {"Q2981": coords_entity(48.853, 2.3498, "Bistro.jpg")}
        )
    )
    respx.get("https://commons.wikimedia.org/w/api.php").respond(json=IMAGEINFO)

    body = (
        await get(
            {"name": "⛪ Inside Notre-Dame", "near": "Notre-Dame de Paris, Parvis Notre-Dame"}
        )
    ).json()

    assert body["ids"]["wikidata"] == "Q2981"


@respx.mock
async def test_a_slow_geoapify_answer_is_retried_once():
    route = respx.get("https://api.geoapify.com/v1/geocode/search").mock(
        side_effect=[httpx.ReadTimeout("slow"), httpx.Response(200, json={"results": []})]
    )

    body = (await get({"name": "Shakespeare and Company"})).json()

    assert route.call_count == 2
    assert {"name": "geoapify", "status": "empty", "reason": None} in body["sources"]


@respx.mock
async def test_a_match_that_wikidata_calls_a_station_is_dropped_for_the_place_itself():
    respx.get("https://api.geoapify.com/v1/geocode/search").respond(
        json={
            "results": [
                {
                    "place_id": "rer",
                    "result_type": "amenity",
                    "name": "Champ de Mars - Tour Eiffel",
                    "rank": {"confidence": 0.9},
                    "lat": 48.8556,
                    "lon": 2.2895,
                }
            ]
        }
    )
    respx.get("https://api.geoapify.com/v2/place-details").respond(
        json={
            "features": [
                {
                    "properties": {
                        "feature_type": "details",
                        "opening_hours": "24/7",
                        "wiki_and_media": {"wikidata": "Q800620"},
                        "datasource": {
                            "sourcename": "openstreetmap",
                            "raw": {"osm_id": 1, "osm_type": "n"},
                        },
                    }
                }
            ]
        }
    )

    def wikidata(request):
        params = request.url.params
        if params.get("action") == "wbsearchentities":
            return httpx.Response(200, json={"search": [{"id": "Q217925"}]})
        if params.get("ids") == "Q800620":
            return httpx.Response(
                200,
                json={
                    "entities": {
                        "Q800620": {
                            "claims": {
                                "P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q55488"}}}}],
                                "P18": [{"mainsnak": {"datavalue": {"value": "RER.jpg"}}}],
                            }
                        }
                    }
                },
            )
        return httpx.Response(
            200, json={"entities": {"Q217925": coords_entity(48.8561, 2.2978, "Bistro.jpg")}}
        )

    respx.get("https://www.wikidata.org/w/api.php").mock(side_effect=wikidata)
    respx.get("https://commons.wikimedia.org/w/api.php").respond(json=IMAGEINFO)

    body = (
        await get({"name": "Champ de Mars & Eiffel Tower", "near": "Champ de Mars, Paris"})
    ).json()

    assert body["match"]["confidence"] == "ambiguous"
    assert body["facts"] == []
    assert body["ids"]["wikidata"] == "Q217925"
    assert body["ids"]["osm"] is None
