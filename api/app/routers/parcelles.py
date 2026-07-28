"""Endpoints /v1/parcelles — parcelles cacaoyères et captures terrain (V3, C1).

Adaptateurs HTTP du service métier : **aucune règle ici**. On traduit les exceptions
du service en codes de statut, et c'est tout.

Cloisonnement par appareil, comme les sessions V2 (D1) : chaque requête porte un
identifiant anonyme ``X-Device-Id``. Un navigateur ne voit jamais les parcelles d'un
autre — et l'on ne stocke aucune IP.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api_deps import get_cache_client, get_client_ip, get_device_id, get_service_parcelles
from app.domain.ports import CachePort
from app.models.parcelle import (
    CaptureReponse,
    CaptureRequest,
    CreerParcelleRequest,
    GeometrieRequest,
    ParcelleReponse,
)
from app.services.parcelles import GeometrieInvalide, ParcelleIntrouvable, ServiceParcelles

router = APIRouter(prefix="/v1", tags=["parcelles"])

_TROP_DE_REQUETES = "Trop de requêtes, veuillez réessayer dans une minute."


async def _garde_debit(cache: CachePort, client_ip: str) -> None:
    """Applique le rate-limit par IP.

    Args:
        cache: Port de cache portant le compteur de débit.
        client_ip: Adresse IP cliente déterminée par la dépendance dédiée.

    Raises:
        HTTPException: 429 si la limite est dépassée.
    """
    if await cache.hit_rate_limit(client_ip):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=_TROP_DE_REQUETES)


@router.post("/parcelles", response_model=ParcelleReponse, status_code=status.HTTP_201_CREATED)
async def creer_parcelle(
    payload: CreerParcelleRequest,
    client_ip: str = Depends(get_client_ip),
    device_id: str = Depends(get_device_id),
    cache: CachePort = Depends(get_cache_client),
    service: ServiceParcelles = Depends(get_service_parcelles),
) -> ParcelleReponse:
    """Crée une parcelle rattachée à l'appareil appelant."""
    await _garde_debit(cache, client_ip)
    parcelle = await service.creer(device_id, payload)
    return ParcelleReponse.model_validate(parcelle, from_attributes=True)


@router.get("/parcelles", response_model=list[ParcelleReponse])
async def lister_parcelles(
    device_id: str = Depends(get_device_id),
    service: ServiceParcelles = Depends(get_service_parcelles),
) -> list[ParcelleReponse]:
    """Liste les parcelles de l'appareil appelant."""
    parcelles = await service.lister(device_id)
    return [ParcelleReponse.model_validate(p, from_attributes=True) for p in parcelles]


@router.get("/parcelles/{identifiant}", response_model=ParcelleReponse)
async def obtenir_parcelle(
    identifiant: str,
    device_id: str = Depends(get_device_id),
    service: ServiceParcelles = Depends(get_service_parcelles),
) -> ParcelleReponse:
    """Retourne une parcelle de l'appareil appelant.

    Raises:
        HTTPException: 404 si elle n'existe pas pour cet appareil.
    """
    parcelle = await service.obtenir(identifiant, device_id)
    if parcelle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Parcelle inconnue.")
    return ParcelleReponse.model_validate(parcelle, from_attributes=True)


@router.put("/parcelles/{identifiant}/geometrie", response_model=ParcelleReponse)
async def enregistrer_geometrie(
    identifiant: str,
    payload: GeometrieRequest,
    client_ip: str = Depends(get_client_ip),
    device_id: str = Depends(get_device_id),
    cache: CachePort = Depends(get_cache_client),
    service: ServiceParcelles = Depends(get_service_parcelles),
) -> ParcelleReponse:
    """Enregistre le contour relevé d'une parcelle.

    Raises:
        HTTPException: 404 si la parcelle est inconnue, 422 si la géométrie est
            invalide (le motif est renvoyé tel quel, il est destiné au producteur).
    """
    await _garde_debit(cache, client_ip)
    try:
        parcelle = await service.enregistrer_geometrie(identifiant, device_id, payload)
    except ParcelleIntrouvable as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Parcelle inconnue."
        ) from exc
    except GeometrieInvalide as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.motif
        ) from exc
    return ParcelleReponse.model_validate(parcelle, from_attributes=True)


@router.post(
    "/parcelles/{identifiant}/captures",
    response_model=CaptureReponse,
    status_code=status.HTTP_201_CREATED,
)
async def deposer_capture(
    identifiant: str,
    payload: CaptureRequest,
    client_ip: str = Depends(get_client_ip),
    device_id: str = Depends(get_device_id),
    cache: CachePort = Depends(get_cache_client),
    service: ServiceParcelles = Depends(get_service_parcelles),
) -> CaptureReponse:
    """Dépose une capture terrain (images échantillonnées et/ou trace GPS).

    Raises:
        HTTPException: 404 si la parcelle est inconnue, 422 si la trace est invalide.
    """
    await _garde_debit(cache, client_ip)
    try:
        capture = await service.deposer_capture(identifiant, device_id, payload)
    except ParcelleIntrouvable as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Parcelle inconnue."
        ) from exc
    except GeometrieInvalide as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.motif
        ) from exc
    return CaptureReponse.model_validate(capture, from_attributes=True)


@router.get("/parcelles/{identifiant}/captures/{capture_id}", response_model=CaptureReponse)
async def obtenir_capture(
    identifiant: str,
    capture_id: str,
    device_id: str = Depends(get_device_id),
    service: ServiceParcelles = Depends(get_service_parcelles),
) -> CaptureReponse:
    """Retourne une capture de l'appareil appelant.

    Raises:
        HTTPException: 404 si la capture est inconnue ou ne concerne pas la parcelle.
    """
    capture = await service.obtenir_capture(capture_id, device_id)
    if capture is None or capture.parcelle != identifiant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Capture inconnue.")
    return CaptureReponse.model_validate(capture, from_attributes=True)
