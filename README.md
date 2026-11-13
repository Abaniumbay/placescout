# PlaceScout

PlaceScout is a small HTTP service. You give it a place (a name and where it is, a website, or a Wikidata id). It answers with what open sources and the place's own website say about it: opening hours, phone, address, price range, a menu link, photos, and the text of a few relevant pages.

**Every fact and photo says where it came from, under what licence, how to credit it, and whether you may store it (`mayStore`).** PlaceScout itself stores nothing and contains no AI. It collects, and your app decides.

It was built for [Caravan](https://github.com/Abaniumbay/caravan), a group trip planner. Google Places does not let apps keep what they fetch, so Caravan uses sources that do.

## API

```
GET /v1/lookup?name=Rijksmuseum&near=Amsterdam
GET /v1/lookup?website=https://www.example-bistro.nl/
GET /v1/lookup?wikidata=Q190804
GET /health
```

| Parameter | |
|---|---|
| `name` | Name of the venue. Needs a Geoapify key to search. |
| `near` | City or address, added to the search text. |
| `lat`, `lon` | Biases the search towards a point. |
| `website` | The venue's own site, if you already know it. |
| `wikidata` | A Wikidata id (`Q…`), if you already know it. |

At least one of `name`, `website` or `wikidata` is required.

```json
{
  "query": { "name": "Rijksmuseum", "near": "Amsterdam" },
  "match": { "confidence": "single", "name": "Rijksmuseum" },
  "ids": { "osm": "way/7094201", "wikidata": "Q190804", "website": "https://www.rijksmuseum.nl/" },
  "facts": [
    { "field": "openingHours", "value": "Mo-Su 09:00-17:00",
      "source": { "name": "OpenStreetMap", "url": "https://www.openstreetmap.org/way/7094201",
                  "license": "ODbL-1.0", "attribution": "© OpenStreetMap contributors",
                  "fetchedAt": "2026-11-13T10:00:00Z", "mayStore": true } }
  ],
  "photos": [
    { "url": "https://upload.wikimedia.org/…/1280px-Rijksmuseum_in_Amsterdam.jpg",
      "pageUrl": "https://commons.wikimedia.org/wiki/File:Rijksmuseum_in_Amsterdam.jpg",
      "credit": "Marco Almbauer, CC BY-SA 3.0, via Wikimedia Commons",
      "author": "Marco Almbauer", "license": "CC BY-SA 3.0",
      "licenseUrl": "https://creativecommons.org/licenses/by-sa/3.0", "mayStore": true },
    { "url": "https://www.rijksmuseum.nl/assets/…", "pageUrl": "https://www.rijksmuseum.nl/nl",
      "credit": "www.rijksmuseum.nl", "mayStore": false }
  ],
  "documents": [
    { "url": "https://www.rijksmuseum.nl/nl/tickets", "kind": "prices", "text": "…",
      "fetchedAt": "2026-11-13T10:00:00Z", "mayStore": false }
  ],
  "attributions": ["© OpenStreetMap contributors", "Powered by Geoapify"],
  "sources": [
    { "name": "geoapify", "status": "used", "reason": null },
    { "name": "wikidata", "status": "used", "reason": null },
    { "name": "commons", "status": "used", "reason": null },
    { "name": "website", "status": "skipped", "reason": "robots.txt" }
  ]
}
```

**`match.confidence`** is one of:
- `single`: one confident venue was found.
- `ambiguous`: several venues fit, or none fits well. No search-based facts are returned, because a wrong venue's facts are worse than none.
- `none`: nothing was found.
- `not_searched`: no `name` was given, there is no Geoapify key, or the search failed.

**`sources[].status`** is one of `used`, `empty`, `skipped` (robots.txt or an opt-out; the reason says which), `failed` or `not_configured`. Every source is best-effort: one failing does not stop the others.

## Sources and what you may do with them

| Source | Gives | `mayStore` | You must show |
|---|---|---|---|
| [Geoapify](https://www.geoapify.com/) (OpenStreetMap data) | match, OSM and Wikidata ids, website, phone, address, `opening_hours` | yes ("Cache/store results with no limits") | `© OpenStreetMap contributors` linked to <https://www.openstreetmap.org/copyright>; `Powered by Geoapify` on its free plan |
| Wikidata (CC0) | image (P18), official website (P856) | yes | nothing required |
| Wikimedia Commons | photo, author, licence, file page | yes | the `credit` line, linked to `pageUrl` and `licenseUrl` |
| The place's website: schema.org data and Open Graph tags | phone, address, opening hours, price range, menu link, images | facts yes; **images no** | "Source: domain", linked |
| The place's website: page text (home page plus up to 2 pages it links as menu, hours or prices) | `documents[]` for your own AI to read | **no** | link to the page for anything you derive |

**Images with `mayStore: false`:**
- Show them from their own URL.
- Never copy, proxy or resize them on your server.
- Credit and link them.
- Remove them on request.

**Documents** are there so your AI can read hours, menus and tips from the page and cite it. Store what your AI concludes, with the page as its source, not the page text.

## How the bot behaves

- Identifies itself as `PlaceScoutBot/<version> (+https://github.com/Abaniumbay/placescout; <contact>)`.
- Reads robots.txt first and obeys it for `PlaceScoutBot`:
  - a missing robots.txt allows everything;
  - one it cannot read allows nothing (RFC 9309).
- Skips a site that opts out of text and data mining (`tdm-reservation: 1` header or meta tag).
- Reads at most 3 pages per site per lookup and never crawls.
- Reads HTML only, up to 2 MB a page, with a 10 s timeout. It follows at most 3 redirects.
- Only fetches public addresses on ports 80 and 443, checked on every redirect, so it cannot be pointed at internal networks.

## Running it

```sh
uv sync
GEOAPIFY_API_KEY=… PLACESCOUT_CONTACT=you@example.org uv run uvicorn placescout.app:create_app --factory
```

or with the published image (tags: a release such as `0.1.0`, `main`, or `sha-…`):

```sh
docker run -p 8000:8000 -e GEOAPIFY_API_KEY=… -e PLACESCOUT_CONTACT=you@example.org \
  ghcr.io/abaniumbay/placescout:0.1.0
```

| Variable | |
|---|---|
| `GEOAPIFY_API_KEY` | Optional. Without it, searching by name is off; `website` and `wikidata` lookups still work. The free plan allows 3,000 requests a day for commercial use, with attribution. |
| `PLACESCOUT_CONTACT` | How site owners can reach you. Wikimedia requires a contact for bots. |

Every lookup logs one line with the match, the counts and each source's status. Page content is never logged.

## Development

```sh
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Tests never touch the network; HTTP is mocked with `respx`.

## Limits

- Menus published as PDF are not read.
- The address check and the request resolve DNS separately. A host that changes its answer in between (DNS rebinding) could slip through, so don't expose PlaceScout to untrusted callers.
- Search confidence comes from Geoapify's ranking. Chains with many branches usually come back `ambiguous` unless `near` or `lat`/`lon` narrows them down.

## Legal notes

Researched for the EU in November 2026. This is not legal advice.

- **Facts** such as opening hours, prices and phone numbers are not protected by copyright. One venue's facts are not a substantial part of a database (Directive 96/9/EC art. 7, 8, 15).
- **Fetching** a page to analyse it is covered by the text-and-data-mining exception (Directive 2019/790 art. 4) unless the site opts out, which PlaceScout honours.
- **Photos:**
  - Copying a photo needs the rightholder's permission (CJEU C-161/17 *Renckhoff*).
  - Linking to a freely accessible image is lawful unless it gets around anti-framing measures (C-466/12 *Svensson*, C-392/19 *VG Bild-Kunst*).
  - Hence site images are `mayStore: false`, and Commons images carry their licence.

## Licence

MIT
