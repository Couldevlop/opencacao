"""Endpoints /v1/sessions — gestion des conversations persistées (V2).

Adaptateurs HTTP du dépôt de sessions : aucune logique métier ici, on délègue au
``SessionStorePort`` et on traduit l'absence en 404. La création est protégée par
le rate-limit par IP (anti-abus : éviter la création massive de sessions).

Cloisonnement par appareil (D1) : chaque requête porte un identifiant anonyme
``X-Device-Id`` ; les conversations listées, lues, renommées, supprimées et
recherchées sont restreintes à cet appareil — un navigateur ne voit jamais les
conversations d'un autre.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api_deps import get_cache_client, get_client_ip, get_device_id, get_session_store
from app.domain.ports import CachePort, SessionStorePort
from app.models.session import (
    CreerSessionRequest,
    RenommerSessionRequest,
    Session,
    SessionAvecMessages,
)

router = APIRouter(prefix="/v1", tags=["sessions"])


@router.post("/sessions", response_model=Session, status_code=status.HTTP_201_CREATED)
async def creer_session(
    payload: CreerSessionRequest | None = None,
    client_ip: str = Depends(get_client_ip),
    device_id: str = Depends(get_device_id),
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
    return await store.creer_session(
        params.langue, params.canal, params.titre, proprietaire=device_id
    )


@router.get("/sessions", response_model=list[Session])
async def lister_sessions(
    limite: int = Query(default=50, ge=1, le=200),
    decalage: int = Query(default=0, ge=0),
    device_id: str = Depends(get_device_id),
    store: SessionStorePort = Depends(get_session_store),
) -> list[Session]:
    """Liste les sessions de l'appareil, de la plus récemment active à la plus ancienne."""
    return await store.lister_sessions(limite, decalage, proprietaire=device_id)


@router.get("/sessions/recherche", response_model=list[Session])
async def rechercher_sessions(
    q: str = Query(min_length=1, max_length=200),
    limite: int = Query(default=50, ge=1, le=200),
    device_id: str = Depends(get_device_id),
    store: SessionStorePort = Depends(get_session_store),
) -> list[Session]:
    """Recherche plein-texte (titre + contenu) dans les conversations de l'appareil (C5).

    Déclaré avant ``/sessions/{session_id}`` pour que « recherche » ne soit pas
    interprété comme un identifiant de session.
    """
    return await store.rechercher_sessions(q, proprietaire=device_id, limite=limite)


@router.get("/sessions/{session_id}", response_model=SessionAvecMessages)
async def obtenir_session(
    session_id: str,
    device_id: str = Depends(get_device_id),
    store: SessionStorePort = Depends(get_session_store),
) -> SessionAvecMessages:
    """Retourne une session et tous ses messages.

    Raises:
        HTTPException: 404 si la session n'existe pas ou n'appartient pas à l'appareil.
    """
    detail = await store.obtenir_session_avec_messages(session_id, proprietaire=device_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session inconnue.")
    return detail


@router.patch("/sessions/{session_id}", response_model=Session)
async def renommer_session(
    session_id: str,
    payload: RenommerSessionRequest,
    device_id: str = Depends(get_device_id),
    store: SessionStorePort = Depends(get_session_store),
) -> Session:
    """Renomme une conversation et retourne ses métadonnées à jour (C3).

    Raises:
        HTTPException: 404 si la session n'existe pas ou n'appartient pas à l'appareil.
    """
    if not await store.renommer_session(session_id, payload.titre, proprietaire=device_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session inconnue.")
    session = await store.obtenir_session(session_id, proprietaire=device_id)
    if session is None:  # course improbable (suppression concurrente)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session inconnue.")
    return session


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def supprimer_session(
    session_id: str,
    device_id: str = Depends(get_device_id),
    store: SessionStorePort = Depends(get_session_store),
) -> Response:
    """Supprime une session et tous ses messages.

    Raises:
        HTTPException: 404 si la session n'existe pas ou n'appartient pas à l'appareil.
    """
    if not await store.supprimer_session(session_id, proprietaire=device_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session inconnue.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
