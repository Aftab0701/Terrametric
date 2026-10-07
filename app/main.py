from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .api import files
from .config import Settings
from .db import Base, make_engine, make_session_factory
from .errors import AppError

DESCRIPTION = """
Upload a **Shapefile (.zip)** or **KML/KMZ** and get back per-feature **area** and **length**,
computed in a locally-chosen projected CRS (never in raw degrees) and cross-checked against an
independent geodesic calculation on the WGS84 ellipsoid.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        engine.dispose()

    app = FastAPI(title="Terrametric", version="1.0.0", description=DESCRIPTION, lifespan=lifespan)
    app.state.settings = settings
    app.state.session_factory = make_session_factory(engine)

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_body())

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(files.router)
    return app

