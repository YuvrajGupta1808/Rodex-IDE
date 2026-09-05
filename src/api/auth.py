from __future__ import annotations

import os

import firebase_admin
from firebase_admin import auth as firebase_auth
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.requests import Request
from starlette.responses import RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

SESSION_COOKIE_NAME = "rodex_session"
SESSION_MAX_AGE_SECONDS = 14 * 24 * 60 * 60  # 14 days

_PUBLIC_PATHS = {"/health", "/login", "/favicon.ico"}
_PUBLIC_PREFIXES = ("/login/", "/auth/", "/ui/login", "/styles/")

if not firebase_admin._apps:
    firebase_admin.initialize_app()


def _serializer() -> URLSafeTimedSerializer:
    secret = os.environ["SESSION_SECRET_KEY"]
    return URLSafeTimedSerializer(secret, salt="rodex-session")


def create_session_cookie(id_token: str) -> str:
    """Verify a Firebase ID token and mint our own signed session token."""
    decoded = firebase_auth.verify_id_token(id_token)
    email = decoded.get("email")
    if not email:
        raise ValueError("Firebase token has no email claim.")
    return _serializer().dumps({"email": email})


def _verify_session_cookie(value: str) -> dict | None:
    try:
        return _serializer().loads(value, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None


def _is_public(path: str) -> bool:
    if path in _PUBLIC_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in _PUBLIC_PREFIXES)


class SessionAuthMiddleware(BaseHTTPMiddleware):
    """Gates every route behind a signed session cookie, except login/health."""

    async def dispatch(self, request: Request, call_next):
        if _is_public(request.url.path):
            return await call_next(request)

        cookie = request.cookies.get(SESSION_COOKIE_NAME)
        session = _verify_session_cookie(cookie) if cookie else None
        if session is None:
            if request.url.path.startswith("/api"):
                from starlette.responses import JSONResponse

                return JSONResponse({"detail": "Not authenticated"}, status_code=401)
            return RedirectResponse(url="/login")

        request.state.user_email = session["email"]
        return await call_next(request)
