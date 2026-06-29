"""Adaptateur : expose l'orchestrateur V3 sous l'interface de ConseilService.

Permet de brancher la plateforme agentique derrière le flag ``agents_enabled`` sans
modifier ``DialogueSessionService`` ni le router : mêmes signatures
(``conseiller`` / ``conseiller_stream``), même entité ``Conseil`` en retour, même
contrat d'événements de flux. La V3 s'insère dans la V2, elle ne la remplace pas.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.application.orchestrateur import Orchestrateur
from app.domain.entities import Conseil
from app.models.chat import DISCLAIMER
from app.models.domain import Langue


class ConseilAgentique:
    """Fait passer l'orchestrateur pour un ``ConseilService`` (duck typing)."""

    def __init__(self, orchestrateur: Orchestrateur) -> None:
        """Initialise l'adaptateur avec l'orchestrateur à envelopper."""
        self._orchestrateur = orchestrateur

    async def conseiller(
        self,
        question: str,
        langue: Langue,
        client_ip: str,
        historique: list[dict[str, str]] | None = None,
    ) -> Conseil:
        """Délègue à l'orchestrateur (même signature que ConseilService.conseiller)."""
        return await self._orchestrateur.traiter(question, langue, client_ip, historique)

    async def conseiller_stream(
        self,
        question: str,
        langue: Langue,
        client_ip: str,
        historique: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[dict]:
        """Variante « flux » : l'orchestrateur ne streame pas, on émet en un bloc.

        Reproduit le contrat d'événements de la V2 : un ``token`` (texte complet)
        puis un ``done`` (métadonnées finales). Les exceptions métier
        (RateLimitDepasse, InferenceUnavailable) se propagent comme en V2.
        """
        conseil = await self._orchestrateur.traiter(question, langue, client_ip, historique)
        if conseil.reponse:
            yield {"type": "token", "text": conseil.reponse}
        yield {
            "type": "done",
            "sources": conseil.sources,
            "confiance": conseil.confiance.value,
            "redirection_anader": conseil.redirection_anader,
            "disclaimer": DISCLAIMER,
            "interaction_id": conseil.interaction_id,
        }
