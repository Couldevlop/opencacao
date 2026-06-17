"""Endpoints POST /v1/chat (et /v1/chat/stream) — adaptateurs HTTP du cas d'usage."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

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


@router.post("/chat/stream")
async def chat_stream(
    payload: ChatRequest,
    client_ip: str = Depends(get_client_ip),
    service: ConseilService = Depends(get_conseil_service),
) -> StreamingResponse:
    """Répond en flux (Server-Sent Events) pour un affichage progressif.

    Chaque ligne est un événement ``data: {json}`` : ``token`` (fragment de texte),
    ``done`` (métadonnées finales) ou ``error`` (rate-limit / indisponible). Le
    statut HTTP reste 200 ; les erreurs métier sont portées par un événement.
    """

    async def flux() -> object:
        try:
            async for evenement in service.conseiller_stream(
                payload.question, payload.langue, client_ip
            ):
                yield f"data: {json.dumps(evenement, ensure_ascii=False)}\n\n"
        except RateLimitDepasse:
            yield f'data: {json.dumps({"type": "error", "kind": "rate_limit"})}\n\n'
        except InferenceUnavailable:
            yield f'data: {json.dumps({"type": "error", "kind": "indisponible"})}\n\n'

    return StreamingResponse(
        flux(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # désactive le buffering nginx (SSE temps réel)
        },
    )
