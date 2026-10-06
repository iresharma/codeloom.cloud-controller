from __future__ import annotations

from fastapi import HTTPException

SESSION_INVALID = "session_invalid"
GITHUB_AUTH_EXPIRED = "github_auth_expired"


def http_error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})
