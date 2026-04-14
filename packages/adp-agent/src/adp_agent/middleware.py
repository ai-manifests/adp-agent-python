"""
FastAPI middleware: bearer-token auth + simple fixed-window rate limiter.
Mirrors the C# and TypeScript runtime middleware semantics.
"""
from __future__ import annotations

import hmac
import time
from collections import defaultdict
from typing import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .config import AgentConfig

_PROTECTED_PREFIXES = (
    "/api/propose",
    "/api/respond-falsification",
    "/api/deliberate",
    "/api/record-outcome",
    "/api/budget",
    "/api/anchor/",
)


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Bearer-token auth for ADP inbound admin endpoints. Public endpoints
    (manifest, calibration snapshot, health, ADJ read-only queries) pass
    through unchanged; protected endpoints require an
    ``Authorization: Bearer <token>`` header that matches
    :attr:`AgentConfig.auth.bearer_token` via constant-time comparison.
    """

    def __init__(self, app, config: AgentConfig) -> None:
        super().__init__(app)
        self._config = config

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if not self._requires_auth(request.url.path):
            return await call_next(request)

        auth = self._config.auth
        if auth is None or not auth.bearer_token:
            return JSONResponse(
                {
                    "error": "auth_not_configured",
                    "message": "This endpoint requires authentication, but the agent has no bearer token configured.",
                },
                status_code=401,
            )

        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            return JSONResponse(
                {"error": "missing_bearer_token", "message": "Missing 'Authorization: Bearer <token>' header."},
                status_code=401,
            )
        provided = header[len("Bearer "):].strip()
        if not hmac.compare_digest(provided, auth.bearer_token):
            return JSONResponse({"error": "invalid_bearer_token"}, status_code=401)

        return await call_next(request)

    @staticmethod
    def _requires_auth(path: str) -> bool:
        return any(path.startswith(p) for p in _PROTECTED_PREFIXES)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Fixed-window rate limiter keyed by client IP. Intentionally coarse —
    real load shedding belongs in the reverse proxy.
    """

    def __init__(
        self,
        app,
        max_requests_per_window: int = 120,
        window_seconds: int = 60,
    ) -> None:
        super().__init__(app)
        self._max = max_requests_per_window
        self._window = window_seconds
        self._windows: dict[str, tuple[float, int]] = {}

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        key = self._client_key(request)
        now = time.monotonic()
        start, count = self._windows.get(key, (now, 0))
        if now - start >= self._window:
            start, count = now, 0
        count += 1
        self._windows[key] = (start, count)

        if count > self._max:
            retry_after = int(self._window - (now - start))
            return JSONResponse(
                {
                    "error": "rate_limit_exceeded",
                    "retryAfterSeconds": retry_after,
                },
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )

        return await call_next(request)

    @staticmethod
    def _client_key(request: Request) -> str:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",", 1)[0].strip()
        return request.client.host if request.client else "unknown"


__all__ = ["AuthMiddleware", "RateLimitMiddleware"]
