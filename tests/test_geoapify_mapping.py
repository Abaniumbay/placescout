from placescout.geoapify import map_details, pick_match


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
