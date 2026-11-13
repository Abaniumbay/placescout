from placescout.wikimedia import map_entity, map_imageinfo, nearest_entity, search_ids


def claim(value):
    return {"mainsnak": {"snaktype": "value", "datavalue": {"value": value}}}


ENTITY = {
    "entities": {
        "Q190804": {
            "claims": {
                "P18": [claim("Rijksmuseum in Amsterdam.jpg")],
                "P856": [claim("https://www.rijksmuseum.nl/")],
            }
        }
    }
}


def test_entity_gives_image_file_and_official_website():
    entity = map_entity(ENTITY, "Q190804")

    assert entity.image_file == "Rijksmuseum in Amsterdam.jpg"
    assert entity.website == "https://www.rijksmuseum.nl/"


def test_entity_without_claims_gives_nothing_useful():
    entity = map_entity({"entities": {"Q1": {"claims": {}}}}, "Q1")

    assert entity.image_file is None and entity.website is None


IMAGEINFO = {
    "query": {
        "pages": {
            "49956045": {
                "title": "File:Rijksmuseum in Amsterdam.jpg",
                "imageinfo": [
                    {
                        "thumburl": "https://upload.wikimedia.org/thumb/800px-Rijksmuseum.jpg",
                        "url": "https://upload.wikimedia.org/Rijksmuseum.jpg",
                        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Rijksmuseum_in_Amsterdam.jpg",
                        "extmetadata": {
                            "Artist": {
                                "value": '<a href="//commons.wikimedia.org/wiki/User:A">Marco Almbauer</a>'
                            },
                            "LicenseShortName": {"value": "CC BY-SA 3.0"},
                            "LicenseUrl": {
                                "value": "https://creativecommons.org/licenses/by-sa/3.0"
                            },
                        },
                    }
                ],
            }
        }
    }
}


def test_commons_photo_may_be_stored_and_carries_author_and_licence():
    photo = map_imageinfo(IMAGEINFO)

    assert photo.url == "https://upload.wikimedia.org/thumb/800px-Rijksmuseum.jpg"
    assert photo.page_url == "https://commons.wikimedia.org/wiki/File:Rijksmuseum_in_Amsterdam.jpg"
    assert photo.author == "Marco Almbauer"
    assert photo.license == "CC BY-SA 3.0"
    assert photo.license_url == "https://creativecommons.org/licenses/by-sa/3.0"
    assert photo.credit == "Marco Almbauer, CC BY-SA 3.0, via Wikimedia Commons"
    assert photo.may_store is True


def test_public_domain_photo_needs_no_licence_url():
    info = IMAGEINFO["query"]["pages"]["49956045"]["imageinfo"][0]["extmetadata"]
    pd = {
        "query": {
            "pages": {
                "1": {
                    "imageinfo": [
                        {
                            **IMAGEINFO["query"]["pages"]["49956045"]["imageinfo"][0],
                            "extmetadata": {
                                "Artist": info["Artist"],
                                "LicenseShortName": {"value": "Public domain"},
                            },
                        }
                    ]
                }
            }
        }
    }

    photo = map_imageinfo(pd)

    assert photo.license == "Public domain" and photo.license_url is None


def test_photo_without_licence_metadata_is_skipped():
    info = IMAGEINFO["query"]["pages"]["49956045"]["imageinfo"][0]
    bare = {"query": {"pages": {"1": {"imageinfo": [{**info, "extmetadata": {}}]}}}}

    assert map_imageinfo(bare) is None


def test_missing_file_is_skipped():
    assert map_imageinfo({"query": {"pages": {"-1": {"missing": ""}}}}) is None


def located(qid, lat, lon, image=None, website=None):
    claims = {"P625": [{"mainsnak": {"datavalue": {"value": {"latitude": lat, "longitude": lon}}}}]}
    if image:
        claims["P18"] = [claim(image)]
    if website:
        claims["P856"] = [claim(website)]
    return {"claims": claims, "labels": {"en": {"value": f"label {qid}"}}}


def test_name_search_takes_the_nearest_candidate_within_reach():
    entities = {
        "entities": {
            "Q1": located("Q1", 45.0, 7.0, "far.jpg"),  # same name, another city
            "Q2": located("Q2", 48.8720, 2.3316, "garnier.jpg", "https://www.operadeparis.fr/"),
        }
    }

    found = nearest_entity(entities, ["Q1", "Q2"], anchor=(48.8714, 2.3320), max_metres=2000)

    assert found.qid == "Q2"
    assert found.entity.image_file == "garnier.jpg"
    assert found.entity.website == "https://www.operadeparis.fr/"
    assert found.label == "label Q2"
    assert found.metres < 100


def test_name_search_rejects_candidates_beyond_reach():
    entities = {"entities": {"Q1": located("Q1", 48.90, 2.40, "x.jpg")}}

    assert nearest_entity(entities, ["Q1"], anchor=(48.8714, 2.3320), max_metres=2000) is None


def test_name_search_ignores_candidates_without_coordinates():
    entities = {"entities": {"Q1": {"claims": {"P18": [claim("x.jpg")]}}}}

    assert nearest_entity(entities, ["Q1"], anchor=(48.8714, 2.3320), max_metres=2000) is None


def test_search_results_become_candidate_ids_in_order():
    assert search_ids({"search": [{"id": "Q2"}, {"id": "Q1"}, {}]}) == ["Q2", "Q1"]


def instance_of(*qids):
    return [{"mainsnak": {"datavalue": {"value": {"id": q}}}} for q in qids]


def test_a_station_is_recognised_as_a_transport_stop():
    data = {"entities": {"Q800620": {"claims": {"P31": instance_of("Q55488", "Q1793804")}}}}

    assert map_entity(data, "Q800620").transport_stop is True


def test_a_park_is_not_a_transport_stop():
    data = {"entities": {"Q217925": {"claims": {"P31": instance_of("Q22698")}}}}

    assert map_entity(data, "Q217925").transport_stop is False


def test_name_search_skips_transport_stops():
    station = located("Q1", 48.8556, 2.2895, "rer.jpg")
    station["claims"]["P31"] = instance_of("Q55488")
    park = located("Q2", 48.8561, 2.2978, "park.jpg")

    found = nearest_entity(
        {"entities": {"Q1": station, "Q2": park}},
        ["Q1", "Q2"],
        anchor=(48.8556, 2.2895),
        max_metres=2000,
    )

    assert found.qid == "Q2"
