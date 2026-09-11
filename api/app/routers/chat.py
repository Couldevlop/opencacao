"""Endpoints POST /v1/chat (et /v1/chat/stream) — adaptateurs HTTP du cas d'usage."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.api_deps import (
    get_cache_client,
    get_client_ip,
    get_device_id_obligatoire,
    get_dialogue_service,
    get_journal,
    get_service_constat_chat,
)
from app.application.constat_chat import ServiceConstatChat
from app.application.dialogue_session import DialogueSessionService
from app.core.config import Settings, get_settings
from app.domain.exceptions import InferenceUnavailable, RateLimitDepasse
from app.domain.ports import CachePort, JournalPort
from app.models.chat import DISCLAIMER, ChatRequest, ChatResponse
from app.models.domain import Confiance
from app.routers.gardes import garde_analyse, garde_debit

router = APIRouter(prefix="/v1", tags=["chat"])


async def _journaliser_visite(
    request: Request, client_ip: str, canal: str, journal: JournalPort
) -> None:
    """Enregistre une visite anonymisée (pays/continent résolus localement, IP non stockée)."""
    geo = getattr(request.app.state, "geo", None)
    pays, continent = geo.localiser(client_ip) if geo is not None else ("", "")
    await journal.enregistrer_visite(pays, continent, canal)


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    request: Request,
    client_ip: str = Depends(get_client_ip),
    dialogue: DialogueSessionService = Depends(get_dialogue_service),
    settings: Settings = Depends(get_settings),
    journal: JournalPort = Depends(get_journal),
) -> ChatResponse:
    """Répond à une question agronomique cacao.

    Le router ne contient aucune logique métier : il délègue au cas d'usage et
    traduit les exceptions du domaine en codes HTTP. Avec un ``session_id``, la
    mémoire de la conversation est gérée côté serveur (V2).

    Raises:
        HTTPException: 429 si rate-limit dépassé, 503 si inférence indisponible,
            404 si le ``session_id`` fourni est inconnu.
    """
    await _journaliser_visite(request, client_ip, payload.canal.value, journal)
    historique = [m.model_dump() for m in payload.historique]
    try:
        conseil = await dialogue.conseiller(
            payload.question,
            payload.langue,
            client_ip,
            session_id=payload.session_id,
            historique=historique,
        )
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

    if conseil is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session inconnue.")

    return ChatResponse(
        reponse=conseil.reponse,
        sources=conseil.sources,
        confiance=conseil.confiance,
        redirection_anader=conseil.redirection_anader,
        interaction_id=conseil.interaction_id,
        session_id=payload.session_id,
    )


@router.post("/chat/stream")
async def chat_stream(
    payload: ChatRequest,
    request: Request,
    client_ip: str = Depends(get_client_ip),
    dialogue: DialogueSessionService = Depends(get_dialogue_service),
    journal: JournalPort = Depends(get_journal),
) -> StreamingResponse:
    """Répond en flux (Server-Sent Events) pour un affichage progressif.

    Chaque ligne est un événement ``data: {json}`` : ``token`` (fragment de texte),
    ``done`` (métadonnées finales, enrichi du ``session_id`` si fourni) ou ``error``
    (rate-limit / indisponible / session_inconnue). Le statut HTTP reste 200 ; les
    erreurs métier sont portées par un événement.
    """
    await _journaliser_visite(request, client_ip, payload.canal.value, journal)
    historique = [m.model_dump() for m in payload.historique]

    async def flux() -> object:
        try:
            async for evenement in dialogue.conseiller_stream(
                payload.question,
                payload.langue,
                client_ip,
                session_id=payload.session_id,
                historique=historique,
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


@router.post("/chat/photo")
async def chat_photo(
    payload: ChatRequest,
    request: Request,
    client_ip: str = Depends(get_client_ip),
    device_id: str = Depends(get_device_id_obligatoire),
    cache: CachePort = Depends(get_cache_client),
    constat: ServiceConstatChat = Depends(get_service_constat_chat),
    journal: JournalPort = Depends(get_journal),
) -> StreamingResponse:
    """Analyse une photo envoyée dans le fil, en flux.

    **Pourquoi une route à part, et pourquoi en flux.** L'analyse d'une image tient
    un budget de plusieurs dizaines de secondes, là où Cloudflare coupe vers 100 s :
    servie sur `/v1/chat` synchrone, elle rendrait un 524 devant le public. Le flux
    émet un premier octet immédiatement (`progress`), puis le constat.

    Une route distincte, plutôt qu'un champ de plus sur `/chat/stream`, garde le
    chemin textuel — celui qui sert la quasi-totalité du trafic — strictement
    inchangé la veille d'une démonstration.

    Les mêmes événements que `/chat/stream` sont émis, pour que l'interface réutilise
    son rendu : ``progress``, ``token``, ``done``, ``error``.

    Les gardes de débit sont celles, partagées, du parcours parcelle : une analyse
    d'image mobilise l'inférence des dizaines de secondes, et une seconde porte non
    protégée annulerait la protection de la première.

    Raises:
        HTTPException: 429 si le débit par IP ou le quota d'analyses par appareil est
            dépassé.
    """
    await garde_debit(cache, client_ip)
    await garde_analyse(cache, device_id)
    await _journaliser_visite(request, client_ip, payload.canal.value, journal)
    tours = " ".join(m.content for m in payload.historique if m.role == "user")
    conversation = f"{tours} {payload.question}".strip()

    async def flux() -> object:
        # Premier octet immédiat : sans lui, l'attente est muette et le proxy coupe.
        debut = {"type": "progress", "text": "Lecture de la photo…"}
        yield f"data: {json.dumps(debut, ensure_ascii=False)}\n\n"
        try:
            resultat = await constat.analyser(payload.question, payload.images, conversation)
        except InferenceUnavailable:
            yield f'data: {json.dumps({"type": "error", "kind": "indisponible"})}\n\n'
            return
        texte = resultat.conseil_reprise or resultat.texte
        yield f"data: {json.dumps({'type': 'token', 'text': texte}, ensure_ascii=False)}\n\n"
        final = {
            "type": "done",
            "sources": [],
            # Un constat est descriptif : il ne prétend jamais à une confiance élevée,
            # et il oriente toujours vers un agent de terrain.
            "confiance": Confiance.MOYENNE.value if resultat.texte else Confiance.FAIBLE.value,
            "redirection_anader": True,
            "disclaimer": DISCLAIMER,
            "session_id": payload.session_id,
        }
        yield f"data: {json.dumps(final, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        flux(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
