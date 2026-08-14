"""Endpoints de santé et de version."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from app.api_deps import get_cache_client, get_inference_client
from app.core.config import Settings, get_settings
from app.domain.ports import CachePort, InferencePort
from app.models.chat import Capacites, VersionResponse

router = APIRouter(prefix="/v1", tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe : 200 si le processus répond."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(
    response: Response,
    cache: CachePort = Depends(get_cache_client),
    inference: InferencePort = Depends(get_inference_client),
) -> dict[str, object]:
    """Readiness probe : 200 si l'inférence et Redis sont disponibles."""
    inference_ok = await inference.ready()
    redis_ok = await cache.ping()
    pret = inference_ok and redis_ok
    if not pret:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"ready": pret, "inference": inference_ok, "redis": redis_ok}


@router.get("/version", response_model=VersionResponse)
async def version(settings: Settings = Depends(get_settings)) -> VersionResponse:
    """Retourne les versions de l'API et du modèle."""
    return VersionResponse(
        api_version=settings.api_version,
        model_name=settings.model_name,
        model_version=settings.model_version,
        inference_backend=settings.inference_backend,
        profil_materiel=settings.profil_materiel,
        capacites=Capacites(
            parcelles=settings.parcelles_enabled,
            rapports=settings.rapports_enabled,
            # Le drapeau ne suffit pas : le VLM ne tient que sur GPU (spec §7.7). En
            # profil CPU l'API répond une indisponibilité explicite — l'interface ne
            # doit donc pas proposer la capacité, sous peine de promettre ce qui ne
            # viendra pas.
            vision=settings.vision_enabled and settings.profil_materiel == "gpu",
        ),
    )
