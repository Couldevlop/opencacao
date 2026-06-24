"""Endpoints /v1/sessions — gestion des conversations persistées (V2).

Adaptateurs HTTP du dépôt de sessions : aucune logique métier ici, on délègue au
``SessionStorePort`` et on traduit l'absence en 404. La création est protégée par
le rate-limit par IP (anti-abus : éviter la création massive de sessions).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api_deps import get_cache_client, get_client_ip, get_session_store
from app.domain.ports import CachePort, SessionStorePort
from app.models.session import (
    CreerSessionRequest,
    Session,
    SessionAvecMessages,
)

router = APIRouter(prefix="/v1", tags=["sessions"])


@router.post("/sessions", response_model=Session, status_code=status.HTTP_201_CREATED)
async def creer_session(
    payload: CreerSessionRequest | None = None,
    client_ip: str = Depends(get_client_ip),
    cache: CachePort = Depends(get_cache_client),
    store: SessionStorePort = Depends(get_session_store),
) -> Session:
    """Crée une nouvelle session de conversation et retourne ses métadonnées.

    Raises:
        HTTPException: 429 si le rate-limit par IP est dépassé.
    """
    if await cache.hit_rate_limit(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Trop de requêtes, veuillez réessayer dans une minute.",
        )
    params = payload or CreerSessionRequest()
    return await store.creer_session(params.langue, params.canal, params.titre)


@router.get("/sessions", response_model=list[Session])
async def lister_sessions(
    limite: int = Query(default=50, ge=1, le=200),
    decalage: int = Query(default=0, ge=0),
    store: SessionStorePort = Depends(get_session_store),
) -> list[Session]:
    """Liste les sessions, de la plus récemment active à la plus ancienne."""
    return await store.lister_sessions(limite, decalage)


@router.get("/sessions/{session_id}", response_model=SessionAvecMessages)
async def obtenir_session(
    session_id: str,
    store: SessionStorePort = Depends(get_session_store),
) -> SessionAvecMessages:
    """Retourne une session et tous ses messages.

    Raises:
        HTTPException: 404 si la session n'existe pas.
    """
    detail = await store.obtenir_session_avec_messages(session_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session inconnue.")
    return detail


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def supprimer_session(
    session_id: str,
    store: SessionStorePort = Depends(get_session_store),
) -> Response:
    """Supprime une session et tous ses messages.

    Raises:
        HTTPException: 404 si la session n'existe pas.
    """
    if not await store.supprimer_session(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session inconnue.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
