"""Endpoints /v1/auth — authentification par lien magique (D2, optionnelle).

Deux routes : demander un lien (envoi best-effort, réponse 202 systématique pour ne
pas révéler si l'email est connu) et vérifier un jeton (renvoie l'identité de compte).
Désactivées (404) tant que ``auth_enabled`` est faux.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api_deps import get_auth_service, get_cache_client, get_client_ip
from app.application.auth_service import AuthService
from app.core.config import Settings, get_settings
from app.domain.ports import CachePort
from app.models.auth import DemandeLienRequest, IdentiteResponse, VerifierLienRequest

router = APIRouter(prefix="/v1/auth", tags=["auth"])


def _verifier_actif(settings: Settings) -> None:
    """Lève 404 si l'authentification est désactivée."""
    if not settings.auth_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Authentification désactivée."
        )


def _base_url(settings: Settings, request: Request) -> str:
    """URL publique pour fabriquer le lien (réglage explicite, sinon celle de la requête)."""
    return settings.auth_base_url or str(request.base_url)


@router.post("/request", status_code=status.HTTP_202_ACCEPTED)
async def demander_lien(
    payload: DemandeLienRequest,
    request: Request,
    client_ip: str = Depends(get_client_ip),
    cache: CachePort = Depends(get_cache_client),
    auth: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    """Envoie un lien magique à l'email fourni (réponse 202, anti-énumération).

    Raises:
        HTTPException: 404 si l'auth est désactivée, 429 si le rate-limit est dépassé.
    """
    _verifier_actif(settings)
    if await cache.hit_rate_limit(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Trop de demandes, veuillez réessayer dans une minute.",
        )
    await auth.demander_lien(payload.email, _base_url(settings, request))
    return {"detail": "Si cette adresse est valide, un lien de connexion vient d'être envoyé."}


@router.post("/verify", response_model=IdentiteResponse)
async def verifier_lien(
    payload: VerifierLienRequest,
    auth: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
) -> IdentiteResponse:
    """Vérifie un jeton de lien magique et renvoie l'identité de compte.

    Raises:
        HTTPException: 404 si l'auth est désactivée, 400 si le lien est invalide/expiré.
    """
    _verifier_actif(settings)
    identite = await auth.verifier(payload.token)
    if identite is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lien invalide ou expiré. Demandez-en un nouveau.",
        )
    return identite
