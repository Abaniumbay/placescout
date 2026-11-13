from placescout.website import discover_links, parse_structured, tdm_reserved

BASE = "https://www.example-bistro.nl/"

JSON_LD_PAGE = """
<html><head>
<meta property="og:image" content="/img/share.jpg">
<meta property="og:title" content="Bistro Example">
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Restaurant",
  "name": "Bistro Example",
  "telephone": "+31 20 123 4567",
  "priceRange": "€€",
  "hasMenu": "https://www.example-bistro.nl/menu",
  "image": "https://www.example-bistro.nl/img/front.jpg",
  "address": {"@type": "PostalAddress", "streetAddress": "Damrak 1",
              "postalCode": "1012 LG", "addressLocality": "Amsterdam"},
  "openingHoursSpecification": [
    {"@type": "OpeningHoursSpecification", "dayOfWeek": ["Monday", "Tuesday"],
     "opens": "12:00", "closes": "22:00"},
    {"@type": "OpeningHoursSpecification", "dayOfWeek": "https://schema.org/Sunday",
     "opens": "10:00", "closes": "16:00"}
  ]
}
</script>
</head><body><p>Welcome</p></body></html>
"""


def facts_by_field(facts):
    return {fact.field: fact.value for fact in facts}


def test_reads_business_facts_from_json_ld():
    result = parse_structured(JSON_LD_PAGE, BASE)

    assert facts_by_field(result.facts) == {
        "telephone": "+31 20 123 4567",
        "priceRange": "€€",
        "address": "Damrak 1, 1012 LG Amsterdam",
        "openingHours": "Mon, Tue 12:00-22:00; Sun 10:00-16:00",
        "menuUrl": "https://www.example-bistro.nl/menu",
    }
    assert result.menu_url == "https://www.example-bistro.nl/menu"


def test_every_site_fact_names_the_page_as_its_source_and_may_be_stored():
    result = parse_structured(JSON_LD_PAGE, BASE)

    for fact in result.facts:
        assert fact.source.url == BASE
        assert fact.source.name == "www.example-bistro.nl"
        assert fact.source.may_store is True


def test_site_images_are_returned_as_links_that_may_not_be_stored():
    result = parse_structured(JSON_LD_PAGE, BASE)

    assert [(p.url, p.may_store, p.credit, p.page_url) for p in result.photos] == [
        ("https://www.example-bistro.nl/img/share.jpg", False, "www.example-bistro.nl", BASE),
        ("https://www.example-bistro.nl/img/front.jpg", False, "www.example-bistro.nl", BASE),
    ]


def test_plain_opening_hours_string_is_kept():
    page = """<script type="application/ld+json">
    {"@context": "https://schema.org", "@type": "Hotel", "openingHours": ["Mo-Fr 09:00-17:00", "Sa 10:00-14:00"]}
    </script>"""

    assert facts_by_field(parse_structured(page, BASE).facts) == {
        "openingHours": "Mo-Fr 09:00-17:00; Sa 10:00-14:00"
    }


def test_json_ld_in_a_graph_is_found():
    page = """<script type="application/ld+json">
    {"@context": "https://schema.org", "@graph": [
      {"@type": "WebSite", "name": "x"},
      {"@type": ["LocalBusiness", "TouristAttraction"], "telephone": "+33 1 23"}
    ]}</script>"""

    assert facts_by_field(parse_structured(page, BASE).facts) == {"telephone": "+33 1 23"}


def test_empty_values_are_not_facts():
    page = """<script type="application/ld+json">
    {"@context": "https://schema.org", "@type": "LocalBusiness", "telephone": " ",
     "openingHoursSpecification": []}</script>"""

    assert parse_structured(page, BASE).facts == []


def test_page_without_structured_data_gives_nothing():
    result = parse_structured("<html><body>hi</body></html>", BASE)

    assert result.facts == [] and result.photos == [] and result.menu_url is None


def test_discovers_menu_hours_and_price_pages_in_several_languages():
    page = """<body>
      <a href="/over-ons">Over ons</a>
      <a href="/menukaart">Menukaart</a>
      <a href="https://www.example-bistro.nl/openingstijden">Openingstijden</a>
      <a href="/fr/carte">La carte</a>
      <a href="/tickets">Tickets &amp; prices</a>
      <a href="https://other-site.com/menu">Menu elsewhere</a>
      <a href="/speisekarte.pdf">Speisekarte</a>
    </body>"""

    assert discover_links(page, BASE, limit=3) == [
        "https://www.example-bistro.nl/menukaart",
        "https://www.example-bistro.nl/openingstijden",
        "https://www.example-bistro.nl/fr/carte",
    ]


def test_discovered_links_skip_duplicates_and_the_page_itself():
    page = """<a href="/">Menu</a><a href="/menu#lunch">Menu</a><a href="/menu">Our menu</a>"""

    assert discover_links(page, BASE, limit=3) == ["https://www.example-bistro.nl/menu"]


def test_tdm_reservation_in_header_or_meta_is_respected():
    assert tdm_reserved("<html></html>", {"tdm-reservation": "1"}) is True
    assert tdm_reserved('<meta name="tdm-reservation" content="1">', {}) is True
    assert tdm_reserved('<meta name="tdm-reservation" content="0">', {}) is False
    assert tdm_reserved("<html></html>", {}) is False
