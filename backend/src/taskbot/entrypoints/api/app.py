from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict


class Health(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    status: Literal["ok"] = "ok"


async def health() -> Health:
    return Health()


def create_app() -> FastAPI:
    app = FastAPI(title="AI Task Bot")
    app.get("/health", response_model=Health)(health)
    return app
