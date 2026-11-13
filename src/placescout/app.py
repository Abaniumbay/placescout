import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Query, Request

from placescout import __version__
from placescout.fetch import Fetcher, Resolver, resolve_host
from placescout.lookup import lookup
from placescout.models import LookupResult
from placescout.models import Query as LookupQuery
from placescout.settings import Settings


def create_app(settings: Settings | None = None, resolve: Resolver = resolve_host) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with httpx.AsyncClient(
            headers={"user-agent": settings.user_agent},
            timeout=settings.timeout_seconds,
            follow_redirects=False,
        ) as client:
            app.state.client = client
            yield

    app = FastAPI(title="PlaceScout", version=__version__, lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/v1/lookup", response_model=LookupResult)
    async def lookup_place(
        request: Request,
        name: str | None = Query(None, max_length=200),
        near: str | None = Query(None, max_length=200),
        lat: float | None = Query(None, ge=-90, le=90),
        lon: float | None = Query(None, ge=-180, le=180),
        website: str | None = Query(None, max_length=2000),
        wikidata: str | None = Query(None, pattern=r"^Q\d+$"),
    ) -> LookupResult:
        if not (name or website or wikidata):
            raise HTTPException(422, "Give at least one of name, website or wikidata.")
        client = request.app.state.client
        query = LookupQuery(
            name=name, near=near, lat=lat, lon=lon, website=website, wikidata=wikidata
        )
        fetcher = Fetcher(client, resolve=resolve, max_bytes=settings.max_page_bytes)
        return await lookup(query, client, fetcher, settings)

    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
