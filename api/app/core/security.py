"""Middlewares de sécurité (recommandations OWASP)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping

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
    # La géolocalisation est autorisée à la SEULE origine de l'interface : l'écran
    # « Ma parcelle » relève le contour d'une plantation au GPS, et un allowlist vide
    # (« geolocation=() ») fait refuser la permission par le navigateur sans même
    # demander son avis au producteur. Micro et caméra restent interdits : les quatre
    # modalités de capture passent par un <input type="file" capture>, qui délègue à
    # l'application photo du téléphone et ne requiert aucune de ces deux permissions.
    "Permissions-Policy": "geolocation=(self), microphone=(), camera=()",
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


# Méthodes HTTP dépourvues de corps : exiger d'elles un Content-Length casserait
# toute l'API. Elles traversent le middleware sans contrôle.
_METHODES_SANS_CORPS = frozenset({"GET", "HEAD", "OPTIONS", "DELETE", "TRACE"})


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Rejette les requêtes dont le corps dépasse une taille maximale (anti-DoS).

    OWASP API4:2023 — Unrestricted Resource Consumption.

    Deux propriétés que la première version n'avait pas :

    * **Un Content-Length absent est refusé (411)**, et non laissé passer. Sans cela,
      un client utilisant ``Transfer-Encoding: chunked`` omet l'en-tête et contourne
      intégralement la borne — la protection ne tenait qu'à la bonne volonté de
      l'appelant. Les méthodes sans corps (GET, HEAD…) restent évidemment exemptées.
    * **Un plafond par préfixe de chemin.** Le plafond global est volontairement bas
      (quelques kilo-octets suffisent à une question). Les routes qui transportent
      légitimement des images — les captures de parcelle — reçoivent un plafond propre,
      plutôt que de relâcher la borne pour toute l'API.
    """

    def __init__(
        self,
        app: object,
        max_body_bytes: int,
        plafonds_par_prefixe: Mapping[str, int] | None = None,
    ) -> None:
        """Initialise le middleware.

        Args:
            app: Application ASGI encapsulée.
            max_body_bytes: Plafond par défaut, en octets.
            plafonds_par_prefixe: Plafonds spécifiques, par préfixe de chemin. Le
                préfixe le plus long qui correspond l'emporte.
        """
        super().__init__(app)  # type: ignore[arg-type]
        self._max = max_body_bytes
        self._plafonds = dict(plafonds_par_prefixe or {})

    def _plafond(self, chemin: str) -> int:
        """Retourne le plafond applicable au chemin (préfixe le plus long gagne)."""
        correspondants = [p for p in self._plafonds if chemin.startswith(p)]
        if not correspondants:
            return self._max
        return self._plafonds[max(correspondants, key=len)]

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method.upper() in _METHODES_SANS_CORPS:
            return await call_next(request)

        content_length = request.headers.get("content-length")
        if content_length is None:
            return JSONResponse(
                status_code=status.HTTP_411_LENGTH_REQUIRED,
                content={"detail": "En-tête Content-Length requis."},
            )
        if not content_length.isdigit():
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "En-tête Content-Length invalide."},
            )
        if int(content_length) > self._plafond(request.url.path):
            return JSONResponse(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                content={"detail": "Corps de requête trop volumineux."},
            )
        return await call_next(request)
