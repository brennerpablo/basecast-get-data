"""Bearer token check. Cloud Run allows unauthenticated calls, so the API checks the token itself."""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status

from basecast_get_data.config import get_settings


def require_token(authorization: str | None = Header(default=None, include_in_schema=False)) -> None:
    expected = get_settings().api_token
    token = (authorization or "").removeprefix("Bearer ").strip()
    # An unset API_TOKEN locks the API instead of opening it.
    if not expected or not secrets.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing token")
