"""FastAPI application factory.

Run with:  uvicorn app.main:create_app --factory --port 8000
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import admin, auth, network, operator, passenger, stream
from app.config import Settings
from app.engine import DomainError, TransitEngine
from app.services.auth import AuthError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


class RevalidatingStaticFiles(StaticFiles):
    """Static files that browsers must revalidate (ETag) so a new release is never served stale."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(settings: Optional[Settings] = None, engine: Optional[TransitEngine] = None) -> FastAPI:
    settings = settings or Settings()
    engine = engine or TransitEngine(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.enable_background_tasks:
            engine.start_background_tasks()
        yield
        await engine.stop()

    app = FastAPI(
        title="Smart Transit and Crowdsourcing Engine",
        description="Baku bus network: live tracking, per-bus crowdedness from passenger reports, "
                    "operator workflow, role management and anomaly alerts.",
        version="3.0.0",
        lifespan=lifespan,
    )
    app.state.engine = engine

    @app.exception_handler(DomainError)
    async def domain_error_handler(_: Request, exc: DomainError):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

    @app.exception_handler(AuthError)
    async def auth_error_handler(_: Request, exc: AuthError):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

    @app.get("/api/health", tags=["system"])
    def health():
        return {
            "status": "ok", "time": engine.clock.now().isoformat(),
            "routes": len(engine.network.routes), "stops": len(engine.network.stops),
            "buses": len(engine.fleet.all()), "sse_clients": engine.events.subscriber_count,
        }

    for module in (auth, network, passenger, operator, admin, stream):
        app.include_router(module.router)

    # Local mode: serve the static React frontend from the same origin (Docker uses nginx instead).
    frontend_dir = os.path.abspath(settings.frontend_dir)
    if os.path.isdir(frontend_dir):
        app.mount("/", RevalidatingStaticFiles(directory=frontend_dir, html=True), name="frontend")

    return app
