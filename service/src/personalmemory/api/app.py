from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from personalmemory import __version__
from personalmemory.api.routes.activities import router as activities_router
from personalmemory.api.routes.capsules import router as capsules_router
from personalmemory.api.routes.connectors import router as connectors_router
from personalmemory.api.routes.envelopes import router as envelopes_router
from personalmemory.api.routes.exceptions import router as exceptions_router
from personalmemory.api.routes.health import router as health_router
from personalmemory.api.routes.memories import router as memories_router
from personalmemory.api.routes.observations import router as observations_router
from personalmemory.api.routes.protocol import router as protocol_router
from personalmemory.api.routes.sources import router as sources_router
from personalmemory.api.routes.system import router as system_router
from personalmemory.application.sync import SyncCoordinator
from personalmemory.infrastructure.database.models import MemorySpaceRecord
from personalmemory.infrastructure.database.session import Database
from personalmemory.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database = Database(active_settings)
        database.initialize()
        app.state.settings = active_settings
        app.state.database = database
        with database.session_factory.begin() as session:
            if session.get(MemorySpaceRecord, "space_default") is None:
                session.add(MemorySpaceRecord(id="space_default", name="我的记忆"))
        coordinator = SyncCoordinator(
            database,
            poll_interval=active_settings.sync_interval,
            settings=active_settings,
        )
        app.state.sync_coordinator = coordinator
        if active_settings.enable_background_sync:
            await coordinator.start()
        yield
        if active_settings.enable_background_sync:
            await coordinator.stop()
        database.dispose()

    app = FastAPI(
        title="PersonalMemory Local API",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    app.state.bootstrap_settings = active_settings
    app.state.request_shutdown = lambda: None
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:1420",
            "http://127.0.0.1:1421",
            "http://127.0.0.1:1422",
            "http://tauri.localhost",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.include_router(health_router, prefix="/api/v1")
    app.include_router(capsules_router, prefix="/api/v1")
    app.include_router(connectors_router, prefix="/api/v1")
    app.include_router(envelopes_router, prefix="/api/v1")
    app.include_router(memories_router, prefix="/api/v1")
    app.include_router(observations_router, prefix="/api/v1")
    app.include_router(activities_router, prefix="/api/v1")
    app.include_router(exceptions_router, prefix="/api/v1")
    app.include_router(protocol_router, prefix="/api/v1")
    app.include_router(system_router, prefix="/api/v1")
    app.include_router(sources_router, prefix="/api/v1")
    return app
