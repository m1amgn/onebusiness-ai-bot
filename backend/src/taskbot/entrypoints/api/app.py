from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict


class Health(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    status: Literal["ok"] = "ok"


async def health() -> Health:
    return Health()


def create_app() -> FastAPI:
    app = FastAPI(title="AI Task Bot", generate_unique_id_function=lambda route: route.name)
    app.get("/health")(health)
    return app
