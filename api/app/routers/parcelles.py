"""Endpoints /v1/parcelles — parcelles cacaoyères et captures terrain (V3, C1).

Adaptateurs HTTP du service métier : **aucune règle ici**. On traduit les exceptions
du service en codes de statut, et c'est tout.

Cloisonnement par appareil, comme les sessions V2 (D1) : chaque requête porte un
identifiant anonyme ``X-Device-Id``. Un navigateur ne voit jamais les parcelles d'un
autre — et l'on ne stocke aucune IP.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api_deps import (
    get_cache_client,
    get_client_ip,
    get_device_id_obligatoire,
    get_service_constats,
    get_service_parcelles,
)
from app.domain.ports import CachePort
from app.models.constat import ConstatReponse
from app.models.parcelle import (
    CaptureReponse,
    CaptureRequest,
    CreerParcelleRequest,
    GeometrieRequest,
    ParcelleReponse,
)
from app.services.constats import (
    CaptureIntrouvable,
    ServiceConstats,
    VisionIndisponibleErreur,
)
from app.services.parcelles import (
    GeometrieInvalide,
    ParcelleIntrouvable,
    QuotaAppareilDepasse,
    ServiceParcelles,
    StockageIndisponible,
)

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


# Budget propre à l'analyse visuelle. Une génération de vision suivie d'une génération
# de conseil occupe le CPU des dizaines de secondes : partager le budget d'un simple
# GET laisserait une poignée de requêtes saturer l'inférence (OWASP API4). Compté par
# appareil, et non par IP : derrière un partage de connexion, une IP porte plusieurs
# producteurs légitimes.
_CONSTATS_PAR_FENETRE = 3
_CONSTATS_FENETRE_S = 60
_TROP_D_ANALYSES = "Trop d'analyses d'images demandées. Patientez une minute avant la suivante."


async def _garde_analyse(cache: CachePort, device_id: str) -> None:
    """Applique le quota d'analyses visuelles, par appareil.

    Args:
        cache: Port de cache portant les compteurs.
        device_id: Identifiant anonyme de l'appareil appelant.

    Raises:
        HTTPException: 429 si le quota d'analyses est dépassé.
    """
    if await cache.hit_quota(f"constat:{device_id}", _CONSTATS_PAR_FENETRE, _CONSTATS_FENETRE_S):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=_TROP_D_ANALYSES)


@router.post("/parcelles", response_model=ParcelleReponse, status_code=status.HTTP_201_CREATED)
async def creer_parcelle(
    payload: CreerParcelleRequest,
    client_ip: str = Depends(get_client_ip),
    device_id: str = Depends(get_device_id_obligatoire),
    cache: CachePort = Depends(get_cache_client),
    service: ServiceParcelles = Depends(get_service_parcelles),
) -> ParcelleReponse:
    """Crée une parcelle rattachée à l'appareil appelant."""
    await _garde_debit(cache, client_ip)
    parcelle = await service.creer(device_id, payload)
    return ParcelleReponse.model_validate(parcelle, from_attributes=True)


@router.get("/parcelles", response_model=list[ParcelleReponse])
async def lister_parcelles(
    device_id: str = Depends(get_device_id_obligatoire),
    service: ServiceParcelles = Depends(get_service_parcelles),
) -> list[ParcelleReponse]:
    """Liste les parcelles de l'appareil appelant."""
    parcelles = await service.lister(device_id)
    return [ParcelleReponse.model_validate(p, from_attributes=True) for p in parcelles]


@router.get("/parcelles/{identifiant}", response_model=ParcelleReponse)
async def obtenir_parcelle(
    identifiant: str,
    device_id: str = Depends(get_device_id_obligatoire),
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
    device_id: str = Depends(get_device_id_obligatoire),
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
    device_id: str = Depends(get_device_id_obligatoire),
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
    except QuotaAppareilDepasse as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    except StockageIndisponible as exc:
        raise HTTPException(
            status_code=status.HTTP_507_INSUFFICIENT_STORAGE, detail=str(exc)
        ) from exc
    return CaptureReponse.model_validate(capture, from_attributes=True)


@router.get("/parcelles/{identifiant}/captures/{capture_id}", response_model=CaptureReponse)
async def obtenir_capture(
    identifiant: str,
    capture_id: str,
    device_id: str = Depends(get_device_id_obligatoire),
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


@router.post(
    "/parcelles/{identifiant}/captures/{capture_id}/constat",
    response_model=ConstatReponse,
    status_code=status.HTTP_201_CREATED,
)
async def produire_constat(
    identifiant: str,
    capture_id: str,
    client_ip: str = Depends(get_client_ip),
    device_id: str = Depends(get_device_id_obligatoire),
    cache: CachePort = Depends(get_cache_client),
    service: ServiceConstats = Depends(get_service_constats),
) -> ConstatReponse:
    """Produit le constat visuel d'une capture.

    Raises:
        HTTPException: 404 si la capture est inconnue, 503 si la vision est
            indisponible (profil CPU ou VLM absent), 429 si le débit est dépassé.
    """
    await _garde_debit(cache, client_ip)
    await _garde_analyse(cache, device_id)
    try:
        constat = await service.produire(identifiant, capture_id, device_id)
    except CaptureIntrouvable as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Capture inconnue."
        ) from exc
    except VisionIndisponibleErreur as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return ConstatReponse.model_validate(constat, from_attributes=True)
