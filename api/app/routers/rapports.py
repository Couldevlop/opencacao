"""Endpoints /v1/rapports — atelier de livrables (V3, C3).

Adaptateurs HTTP du service métier : **aucune règle ici**. On traduit les exceptions
en codes de statut, et c'est tout.

Cloisonnement par appareil, comme les parcelles : chaque requête porte un
``X-Device-Id`` obligatoire.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, StringConstraints

from app.api_deps import (
    get_cache_client,
    get_client_ip,
    get_device_id_obligatoire,
    get_service_rapports,
)
from app.application.intention_rapport import DEMANDE_MAX, resoudre_demande
from app.core.rapports_store import Rapport
from app.domain.ports import CachePort
from app.models.rapport import GabaritReponse, IntentionReponse, RapportReponse
from app.services.gabarits import (
    Gabarit,
    GabaritInconnu,
    GabaritInvalide,
    charger_gabarit,
    lister_gabarits,
)
from app.services.rapports import RapportIntrouvable, ServiceRapports, SujetVide

router = APIRouter(prefix="/v1", tags=["rapports"])

# Budget propre à la génération de livrables. Une étude enchaîne une génération PAR
# SECTION — plusieurs minutes de CPU sur le profil actuel. Partager le budget d'un
# simple GET laisserait une poignée de requêtes saturer l'inférence (OWASP API4).
# Compté par appareil, et non par IP : derrière un partage de connexion, une IP porte
# plusieurs demandeurs légitimes.
_RAPPORTS_PAR_FENETRE = 3
_RAPPORTS_FENETRE_S = 300
_TROP_DE_RAPPORTS = "Trop de documents demandés. Patientez quelques minutes avant le suivant."

# Budget de l'export. Moins cher qu'une génération, mais loin d'être gratuit : rendre
# un document coûte de 0,3 à 1,3 s de CPU, et la route est rappelable en boucle sur un
# rapport déjà produit. Plus généreux que la génération — on télécharge légitimement
# les quatre formats — mais borné.
_EXPORTS_PAR_FENETRE = 20
_EXPORTS_FENETRE_S = 300
_TROP_D_EXPORTS = "Trop de téléchargements. Patientez quelques minutes."


async def _garde_export(cache: CachePort, device_id: str) -> None:
    """Applique le quota de téléchargement, par appareil.

    Args:
        cache: Port de cache portant les compteurs.
        device_id: Identifiant anonyme de l'appareil appelant.

    Raises:
        HTTPException: 429 si le quota est dépassé.
    """
    if await cache.hit_quota(f"export:{device_id}", _EXPORTS_PAR_FENETRE, _EXPORTS_FENETRE_S):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=_TROP_D_EXPORTS)


async def _garde_generation(cache: CachePort, device_id: str) -> None:
    """Applique le quota de génération de livrables, par appareil.

    Args:
        cache: Port de cache portant les compteurs.
        device_id: Identifiant anonyme de l'appareil appelant.

    Raises:
        HTTPException: 429 si le quota est dépassé.
    """
    if await cache.hit_quota(f"rapport:{device_id}", _RAPPORTS_PAR_FENETRE, _RAPPORTS_FENETRE_S):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=_TROP_DE_RAPPORTS)


class CreerRapportRequest(BaseModel):
    """Demande de génération d'un livrable."""

    gabarit: str = Field(min_length=1, max_length=64)
    sujet: str = Field(min_length=1, max_length=200)


class IntentionRequest(BaseModel):
    """Demande écrite librement, à interpréter.

    Le plafond vient de la couche métier (``DEMANDE_MAX``) : deux valeurs
    indépendantes finiraient par diverger, et c'est le métier qui sait ce qu'il
    accepte de traiter.
    """

    demande: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=DEMANDE_MAX),
    ]


def _en_gabarit_reponse(gabarit: Gabarit) -> GabaritReponse:
    """Projette un gabarit sur son schéma d'API, plan compris."""
    return GabaritReponse(
        identifiant=gabarit.identifiant,
        titre=gabarit.titre,
        public=gabarit.public,
        mention=gabarit.mention,
        sections=[section.titre for section in gabarit.sections],
    )


def _en_reponse(rapport: Rapport) -> RapportReponse:
    """Projette un job sur son schéma d'API."""
    return RapportReponse(
        identifiant=rapport.identifiant,
        gabarit=rapport.gabarit,
        sujet=rapport.sujet,
        etat=rapport.etat.value,
        sections_faites=rapport.sections_faites,
        sections_total=rapport.sections_total,
    )


@router.get("/rapports/gabarits", response_model=list[GabaritReponse])
async def lister_les_gabarits() -> list[GabaritReponse]:
    """Liste les gabarits disponibles, **plan compris**.

    L'écran affiche le sommaire AVANT de lancer la génération : le plan est connu
    d'avance — c'est tout l'intérêt du gabarit — et le découvrir au fil du flux
    priverait le lecteur de ce qu'il attend.
    """
    return [_en_gabarit_reponse(charger_gabarit(nom)) for nom in lister_gabarits()]


@router.post("/rapports/intention", response_model=IntentionReponse)
async def comprendre_demande(
    payload: IntentionRequest,
    device_id: str = Depends(get_device_id_obligatoire),
    cache: CachePort = Depends(get_cache_client),
    client_ip: str = Depends(get_client_ip),
) -> IntentionReponse:
    """Interprète une demande écrite librement, sans rien produire.

    On demande un document, on ne le configure pas. La résolution est déterministe
    et ne consomme pas d'inférence : elle ne mérite donc pas le quota de génération,
    qui protège le coût réel. Elle mérite en revanche le limiteur générique **par IP**
    comme tout POST de l'application — un quota par appareil ne protégerait rien, le
    client choisissant son ``X-Device-Id`` (OWASP API4).

    Ce qui n'est pas certain ressort avec ses candidats et un statut 200 : une
    ambiguïté est une question à poser, pas une panne.

    Raises:
        HTTPException: 429 si le débit est dépassé, 503 si un gabarit est illisible.
    """
    # L'IP vient de la dépendance dédiée, jamais de `request.client.host`. En production
    # l'application est derrière Cloudflare : `request.client.host` y est l'IP de l'edge,
    # LA MÊME pour tout le monde. Le compteur serait alors global — soit il ne freine
    # aucun attaquant, soit il verrouille tous les utilisateurs d'un coup. `get_client_ip`
    # lit CF-Connecting-IP, et seulement si l'on est configuré pour faire confiance au
    # proxy (anti-spoofing, OWASP API4).
    if await cache.hit_rate_limit(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Trop de requêtes. Patientez quelques instants.",
        )
    try:
        catalogue = [charger_gabarit(nom) for nom in lister_gabarits()]
    except GabaritInvalide as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Les types de documents sont momentanément indisponibles.",
        ) from exc

    # La résolution est synchrone et parcourt jusqu'à 2 000 caractères : laissée dans
    # la coroutine, elle bloquerait la boucle d'événements — donc aussi les flux SSE du
    # chat. Même raison que le rendu binaire (`services/rapports.py`).
    intention = await asyncio.to_thread(resoudre_demande, payload.demande, catalogue)
    par_nom = {gabarit.identifiant: gabarit for gabarit in catalogue}
    return IntentionReponse(
        gabarit=intention.gabarit,
        sujet=intention.sujet,
        certaine=intention.certaine,
        candidats=[_en_gabarit_reponse(par_nom[nom]) for nom in intention.candidats],
    )


@router.post("/rapports", response_model=RapportReponse, status_code=status.HTTP_201_CREATED)
async def creer_rapport(
    payload: CreerRapportRequest,
    device_id: str = Depends(get_device_id_obligatoire),
    cache: CachePort = Depends(get_cache_client),
    service: ServiceRapports = Depends(get_service_rapports),
) -> RapportReponse:
    """Crée un job de génération.

    Raises:
        HTTPException: 404 si le gabarit est inconnu, 429 si le quota est dépassé.
    """
    await _garde_generation(cache, device_id)
    try:
        rapport = await service.creer(payload.gabarit, payload.sujet, device_id)
    except GabaritInconnu as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Gabarit inconnu."
        ) from exc
    except GabaritInvalide as exc:
        # Gabarit livré mais malformé (montage ConfigMap raté) : c'est une erreur
        # d'exploitation, pas une faute du client — mais elle mérite mieux qu'une trace.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ce gabarit est momentanément indisponible.",
        ) from exc
    except SujetVide as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Ce sujet est vide. Indiquez sur quoi doit porter le document.",
        ) from exc
    return _en_reponse(rapport)


@router.get("/rapports", response_model=list[RapportReponse])
async def lister_rapports(
    device_id: str = Depends(get_device_id_obligatoire),
    service: ServiceRapports = Depends(get_service_rapports),
) -> list[RapportReponse]:
    """Liste les jobs de l'appareil appelant."""
    return [_en_reponse(rapport) for rapport in await service.lister(device_id)]


@router.get("/rapports/{identifiant}", response_model=RapportReponse)
async def obtenir_rapport(
    identifiant: str,
    device_id: str = Depends(get_device_id_obligatoire),
    service: ServiceRapports = Depends(get_service_rapports),
) -> RapportReponse:
    """Retourne l'état d'un job.

    Raises:
        HTTPException: 404 si le job est inconnu ou d'un autre appareil.
    """
    rapport = await service.obtenir(identifiant, device_id)
    if rapport is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rapport inconnu.")
    return _en_reponse(rapport)


@router.get("/rapports/{identifiant}/stream")
async def flux_rapport(
    identifiant: str,
    device_id: str = Depends(get_device_id_obligatoire),
    cache: CachePort = Depends(get_cache_client),
    service: ServiceRapports = Depends(get_service_rapports),
) -> StreamingResponse:
    """Diffuse la rédaction, section par section.

    Le premier événement part avant toute génération : une réponse longue sur CPU
    doit streamer un premier octet vite, sans quoi l'edge coupe (524 de juin).

    Raises:
        HTTPException: 404 si le job est inconnu ou d'un autre appareil, 429 si le
            quota de génération est dépassé.
    """
    # Le budget suit le COÛT RÉEL. Créer un job ne coûte rien ; en exécuter un coûte
    # une génération par section. Garder seulement le POST laissait un client créer
    # trois jobs puis relancer leurs flux en boucle, sans jamais toucher au quota.
    await _garde_generation(cache, device_id)
    if await service.obtenir(identifiant, device_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rapport inconnu.")

    async def _evenements() -> AsyncIterator[str]:
        async for evenement in service.executer(identifiant, device_id):
            yield f"data: {json.dumps(evenement, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        _evenements(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/rapports/{identifiant}/export")
async def exporter_rapport(
    identifiant: str,
    format: str = Query(default="md", max_length=8),
    device_id: str = Depends(get_device_id_obligatoire),
    cache: CachePort = Depends(get_cache_client),
    service: ServiceRapports = Depends(get_service_rapports),
) -> Response:
    """Exporte un rapport terminé.

    Raises:
        HTTPException: 422 si le format est inconnu, 404 si le rapport est inconnu,
            d'un autre appareil, ou pas encore terminé, 429 si le quota est dépassé.
    """
    await _garde_export(cache, device_id)
    try:
        octets, nom, type_mime = await service.exporter(identifiant, device_id, format)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Format inconnu : md, docx, xlsx ou pptx.",
        ) from exc
    except RapportIntrouvable as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Rapport inconnu ou non terminé."
        ) from exc
    return Response(
        content=octets,
        media_type=type_mime,
        headers={"Content-Disposition": f'attachment; filename="{nom}"'},
    )
