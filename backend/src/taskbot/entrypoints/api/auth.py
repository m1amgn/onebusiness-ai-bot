from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from taskbot.domain.errors import InvalidTokenError
from taskbot.domain.users import AuthenticatedUser
from taskbot.entrypoints.api.container import Services

bearer = HTTPBearer(auto_error=False)


async def current_user(
    services: Services,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> AuthenticatedUser:
    if credentials is None:
        raise InvalidTokenError("Invalid dashboard token")
    return await services.authenticate.execute(credentials.credentials)


type CurrentUser = Annotated[AuthenticatedUser, Depends(current_user)]
