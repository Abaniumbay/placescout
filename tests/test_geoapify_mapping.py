import pytest

from placescout.geoapify import clean_name, map_details, pick_match, query_words


def search_result(confidence=0.95, result_type="amenity", place_id="abc"):
    return {
        "place_id": place_id,
        "result_type": result_type,
        "name": "Tour Eiffel",
        "rank": {"confidence": confidence},
    }


def test_one_confident_poi_is_a_match():
    match = pick_match({"results": [search_result()]})

    assert match.confidence == "single"
    assert match.place_id == "abc"
    assert match.name == "Tour Eiffel"


def test_no_results_is_no_match():
    assert pick_match({"results": []}).confidence == "none"


def test_low_confidence_is_ambiguous():
    assert pick_match({"results": [search_result(confidence=0.5)]}).confidence == "ambiguous"


def test_an_address_or_city_is_not_a_venue():
    assert pick_match({"results": [search_result(result_type="city")]}).confidence == "ambiguous"


def test_two_equally_good_venues_are_ambiguous():
    results = [search_result(place_id="a"), search_result(place_id="b", confidence=0.93)]

    assert pick_match({"results": results}).confidence == "ambiguous"


def test_a_clear_winner_over_a_weak_second_is_a_match():
    results = [search_result(place_id="a"), search_result(place_id="b", confidence=0.4)]

    assert pick_match({"results": results}).place_id == "a"


DETAILS = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {
                "feature_type": "details",
                "place_id": "abc",
                "name": "Tour Eiffel",
                "website": "https://www.toureiffel.paris/",
                "opening_hours": "Mo-Su 09:00-24:00",
                "contact": {"phone": "+33 892 70 12 39"},
                "formatted": "Tour Eiffel, 5 Avenue Anatole France, 75007 Paris, France",
                "wiki_and_media": {"wikidata": "Q243"},
                "datasource": {
                    "sourcename": "openstreetmap",
                    "attribution": "© OpenStreetMap contributors",
                    "license": "Open Database License",
                    "url": "https://www.openstreetmap.org/copyright",
                    "raw": {"osm_id": 5013364, "osm_type": "w"},
                },
            },
        }
    ],
}


def test_details_become_osm_facts_with_osm_attribution():
    place = map_details(DETAILS)

    assert {f.field: f.value for f in place.facts} == {
        "openingHours": "Mo-Su 09:00-24:00",
        "telephone": "+33 892 70 12 39",
        "address": "Tour Eiffel, 5 Avenue Anatole France, 75007 Paris, France",
        "website": "https://www.toureiffel.paris/",
    }
    source = place.facts[0].source
    assert source.name == "OpenStreetMap"
    assert source.url == "https://www.openstreetmap.org/way/5013364"
    assert source.license == "ODbL-1.0"
    assert source.attribution == "© OpenStreetMap contributors"
    assert source.may_store is True


def test_details_give_the_ids_the_other_sources_need():
    place = map_details(DETAILS)

    assert place.osm == "way/5013364"
    assert place.wikidata == "Q243"
    assert place.website == "https://www.toureiffel.paris/"


def test_details_without_a_feature_give_nothing():
    assert map_details({"features": []}) is None


def venue(place_id, confidence, lat, lon, name="x"):
    return {
        "place_id": place_id,
        "result_type": "amenity",
        "name": name,
        "rank": {"confidence": confidence},
        "lat": lat,
        "lon": lon,
    }


def test_a_near_duplicate_of_the_best_venue_is_the_same_place():
    # Field of Mars and "Champ de Mars – Tour Eiffel": about 200 m apart.
    results = [venue("a", 1.0, 48.8561, 2.2978, "Field of Mars"), venue("b", 0.81, 48.8561, 2.2951)]

    match = pick_match({"results": results})

    assert (match.confidence, match.place_id) == ("single", "a")


def test_two_strong_venues_far_apart_stay_ambiguous():
    results = [venue("a", 0.95, 52.37, 4.89), venue("b", 0.94, 52.35, 4.92)]

    assert pick_match({"results": results}).confidence == "ambiguous"


def test_the_top_result_is_the_anchor_even_when_ambiguous():
    results = [venue("a", 0.95, 52.37, 4.89), venue("b", 0.94, 52.35, 4.92)]

    assert pick_match({"results": results}).anchor == (52.37, 4.89)


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("🎭 Palais Garnier (Opéra)", "Palais Garnier"),
        ("⛴️ Seine boat – Vedettes du Pont Neuf", "Seine boat – Vedettes du Pont Neuf"),
        ("🗼 Champ de Mars & Eiffel Tower", "Champ de Mars & Eiffel Tower"),
        ("Pet‑Friendly Villa", "Pet‑Friendly Villa"),
        ("  Rijksmuseum  ", "Rijksmuseum"),
    ],
)
def test_names_lose_emoji_symbols_and_a_trailing_parenthetical(raw, clean):
    assert clean_name(raw) == clean


def test_a_transport_stop_is_never_the_venue():
    station = {
        **venue("rer", 1.0, 48.8556, 2.2895, "Champ de Mars - Tour Eiffel"),
        "category": "public_transport.train",
    }
    park = {**venue("park", 0.9, 48.8561, 2.2978, "Field of Mars"), "category": "leisure.park"}

    match = pick_match({"results": [station, park]}, query_words={"champ", "mars"})

    assert (match.confidence, match.place_id) == ("single", "park")


def test_a_venue_that_shares_no_word_with_the_query_is_not_a_match():
    # Inside Galeries Lafayette, the shops come first.
    results = [
        venue("chanel", 0.9, 48.8736, 2.3321, "Chanel"),
        venue("lv", 0.9, 48.8737, 2.3322, "Louis Vuitton Galeries Lafayette"),
    ]

    match = pick_match(
        {"results": results}, query_words={"galeries", "lafayette", "christmas", "tree"}
    )

    assert match.confidence == "ambiguous"
    assert match.anchor == (48.8736, 2.3321)


def test_query_words_ignore_accents_case_short_words_and_the_city():
    assert query_words("🗼 Champ de Mars & Eiffel Tower", "Champ de Mars, Paris") == {
        "champ",
        "mars",
        "eiffel",
        "tower",
    }
    assert query_words("Opéra night", None) == {"opera", "night"}
