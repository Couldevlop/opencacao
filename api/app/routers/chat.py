"""Endpoint POST /v1/chat — adaptateur HTTP du cas d'usage ConseilService."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api_deps import get_client_ip, get_conseil_service
from app.application.conseil_service import ConseilService
from app.core.config import Settings, get_settings
from app.domain.exceptions import InferenceUnavailable, RateLimitDepasse
from app.models.chat import ChatRequest, ChatResponse

router = APIRouter(prefix="/v1", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    client_ip: str = Depends(get_client_ip),
    service: ConseilService = Depends(get_conseil_service),
    settings: Settings = Depends(get_settings),
) -> ChatResponse:
    """Répond à une question agronomique cacao.

    Le router ne contient aucune logique métier : il délègue à ConseilService et
    traduit les exceptions du domaine en codes HTTP.

    Raises:
        HTTPException: 429 si rate-limit dépassé, 503 si inférence indisponible.
    """
    try:
        conseil = await service.conseiller(payload.question, payload.langue, client_ip)
    except RateLimitDepasse as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Trop de requêtes, veuillez réessayer dans une minute.",
        ) from exc
    except InferenceUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Le service de conseil est momentanément indisponible.",
        ) from exc

    return ChatResponse(
        reponse=conseil.reponse,
        sources=conseil.sources,
        confiance=conseil.confiance,
        redirection_anader=conseil.redirection_anader,
    )
