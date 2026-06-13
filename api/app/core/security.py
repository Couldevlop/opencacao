"""Middlewares de sécurité (recommandations OWASP)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# En-têtes de sécurité appliqués à chaque réponse (OWASP Secure Headers).
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cache-Control": "no-store",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Ajoute les en-têtes de sécurité et retire l'en-tête Server."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for header, valeur in SECURITY_HEADERS.items():
            response.headers.setdefault(header, valeur)
        if "server" in response.headers:
            del response.headers["server"]
        return response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Rejette les requêtes dont le corps dépasse une taille maximale (anti-DoS).

    OWASP API4:2023 — Unrestricted Resource Consumption.
    """

    def __init__(self, app: object, max_body_bytes: int) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._max = max_body_bytes

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        content_length = request.headers.get("content-length")
        if (
            content_length is not None
            and content_length.isdigit()
            and int(content_length) > self._max
        ):
            return JSONResponse(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                content={"detail": "Corps de requête trop volumineux."},
            )
        return await call_next(request)
