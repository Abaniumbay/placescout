from placescout.wikimedia import map_entity, map_imageinfo


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
