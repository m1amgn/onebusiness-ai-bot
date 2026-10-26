import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from taskbot.domain.errors import (
    EventStreamError,
    InvalidTicketError,
    InvalidTokenError,
    TaskNotFoundError,
)
from taskbot.entrypoints.api.container import ApiServices, open_api_services
from taskbot.entrypoints.api.realtime import router as realtime_router
from taskbot.entrypoints.api.tasks import router as tasks_router
from taskbot.entrypoints.settings import load_settings
from taskbot.infrastructure.settings.api import ApiSettings


class Health(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)
    status: Literal["ok"] = "ok"


async def health() -> Health:
    return Health()


async def unauthorized(request: Request, error: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        headers={"WWW-Authenticate": "Bearer"},
        content={"detail": "Send /dashboard to the bot for a new link"},
    )


async def task_not_found(request: Request, error: Exception) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": "Task not found"})


async def stream_unavailable(request: Request, error: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        headers={"Retry-After": "5"},
        content={"detail": "Real-time events are unavailable"},
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = load_settings(ApiSettings)
    logging.basicConfig(level=settings.log_level)
    async with open_api_services(settings) as services:
        app.state.services = services
        yield


def create_app(services: ApiServices | None = None) -> FastAPI:
    app = FastAPI(
        title="AI Task Bot",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        generate_unique_id_function=lambda route: route.name,
        lifespan=lifespan if services is None else None,
    )
    if services is not None:
        app.state.services = services
    app.get("/health")(health)
    app.include_router(tasks_router)
    app.include_router(realtime_router)
    app.add_exception_handler(InvalidTokenError, unauthorized)
    app.add_exception_handler(InvalidTicketError, unauthorized)
    app.add_exception_handler(TaskNotFoundError, task_not_found)
    app.add_exception_handler(EventStreamError, stream_unavailable)
    return app
