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
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cache-Control": "no-store",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}

# CSP stricte par défaut : les réponses d'API sont du JSON, elles n'ont aucune
# ressource à charger. « default-src 'none' » est donc le réglage le plus sûr.
CSP_API = "default-src 'none'; frame-ancestors 'none'"

# CSP de l'interface web quand l'API la sert elle-même (même origine, cf.
# main._monter_interface). Alignée sur la balise meta de web/index.html : autorise
# les ressources locales (JS/CSS/img) et l'appel à l'API. Sans cette distinction, la
# CSP « default-src 'none' » ci-dessus bloquerait tout le rendu de l'UI (CSS, modules
# JS, images, fetch). En production l'UI est servie par nginx, mais ce mode reste
# valide en local et pour un déploiement mono-conteneur.
CSP_UI = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self' http: https:; base-uri 'none'; form-action 'none'; "
    "object-src 'none'; frame-ancestors 'none'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Ajoute les en-têtes de sécurité et retire l'en-tête Server.

    La CSP est choisie selon le type de réponse : permissive pour le document HTML
    de l'interface (servie à la même origine), stricte pour tout le reste (API JSON).
    Un en-tête CSP déjà posé par une route (ex. console de curation) est respecté.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for header, valeur in SECURITY_HEADERS.items():
            response.headers.setdefault(header, valeur)
        type_contenu = response.headers.get("content-type", "")
        csp = CSP_UI if type_contenu.startswith("text/html") else CSP_API
        response.headers.setdefault("Content-Security-Policy", csp)
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
