from pydantic import BaseModel


class ApiError(BaseModel):
    detail: str


def json_error_response() -> dict[str, object]:
    return {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/ApiError"}}}}
