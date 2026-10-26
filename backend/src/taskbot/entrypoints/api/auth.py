from typing import Annotated, cast

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from taskbot.domain.errors import InvalidTokenError
from taskbot.domain.users import AuthenticatedUser
from taskbot.entrypoints.api.container import ApiServices

bearer = HTTPBearer(auto_error=False)


def get_services(request: Request) -> ApiServices:
    # Starlette's State erases types at the application boundary.
    return cast(ApiServices, request.app.state.services)


type Services = Annotated[ApiServices, Depends(get_services)]


async def current_user(
    services: Services,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> AuthenticatedUser:
    if credentials is None:
        raise InvalidTokenError("Invalid dashboard token")
    return await services.authenticate.execute(credentials.credentials)


type CurrentUser = Annotated[AuthenticatedUser, Depends(current_user)]
