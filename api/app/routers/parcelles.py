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
from app.routers.gardes import garde_analyse, garde_debit
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


@router.post("/parcelles", response_model=ParcelleReponse, status_code=status.HTTP_201_CREATED)
async def creer_parcelle(
    payload: CreerParcelleRequest,
    client_ip: str = Depends(get_client_ip),
    device_id: str = Depends(get_device_id_obligatoire),
    cache: CachePort = Depends(get_cache_client),
    service: ServiceParcelles = Depends(get_service_parcelles),
) -> ParcelleReponse:
    """Crée une parcelle rattachée à l'appareil appelant."""
    await garde_debit(cache, client_ip)
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


# POST en plus de PUT, et ce n'est pas un caprice de style. Le WAF ModSecurity du
# contrôleur d'ingress applique le jeu de règles OWASP CRS, dont la règle 911100
# n'autorise que GET/HEAD/POST/OPTIONS : tout PUT venant d'un navigateur était rejeté
# en 403 AVANT d'atteindre l'API. Constaté le 19/08/2026 — le parcours GPS, fonction
# phare de « Ma parcelle », n'avait donc jamais fonctionné en production, tandis que
# le dépôt de photos (POST) passait, ce qui rendait la panne d'autant plus discrète.
#
# Le WAF est partagé avec d'autres sites en production ; y ouvrir PUT est le correctif
# juste, mais il touche une infrastructure commune. On expose donc ici le même
# traitement sous un verbe que le WAF laisse passer. PUT reste offert : il est
# sémantiquement correct, et il fonctionne pour tout client qui n'est pas derrière ce
# WAF (tests, intégrations serveur à serveur).
@router.post("/parcelles/{identifiant}/geometrie", response_model=ParcelleReponse)
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
    await garde_debit(cache, client_ip)
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
    await garde_debit(cache, client_ip)
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
    await garde_debit(cache, client_ip)
    await garde_analyse(cache, device_id)
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
