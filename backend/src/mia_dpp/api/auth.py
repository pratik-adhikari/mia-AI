"""Verified Clerk identity for FastAPI routes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from clerk_backend_api import Clerk
from clerk_backend_api.security.types import AuthenticateRequestOptions
from fastapi import Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from mia_dpp.persistence.catalogue import LOCAL_USER_ID

if TYPE_CHECKING:
    from mia_dpp.mia import Mia


async def authenticated_user(request: Request) -> str:
    """Return the signed Clerk subject; local unconfigured development uses one fixed account."""

    application: Mia = request.app.state.mia
    settings = application.settings
    secret = settings.clerk_secret_key
    jwt_key = settings.clerk_jwt_key
    if secret is None and jwt_key is None:
        if settings.vercel_environment:
            raise HTTPException(status_code=503, detail="Clerk authentication is not configured")
        return LOCAL_USER_ID

    client = Clerk(bearer_auth=secret.get_secret_value() if secret is not None else None)
    options = AuthenticateRequestOptions(
        secret_key=secret.get_secret_value() if secret is not None else None,
        jwt_key=jwt_key.get_secret_value() if jwt_key is not None else None,
        authorized_parties=settings.authorized_parties,
    )
    try:
        state = await run_in_threadpool(client.authenticate_request, request, options)
    except Exception as error:
        raise HTTPException(status_code=401, detail="Invalid authentication token") from error
    user_id = state.payload.get("sub") if state.payload else None
    if not state.is_signed_in or not isinstance(user_id, str) or not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user_id


AuthenticatedUser = Annotated[str, Depends(authenticated_user)]
