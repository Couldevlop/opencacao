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
        """Variante « flux » : délègue au streaming réel de l'orchestrateur.

        Émet les événements ``token`` (phrase par phrase, garde-fou de sortie
        appliqué AVANT diffusion) puis ``done``. Les exceptions métier
        (RateLimitDepasse, InferenceUnavailable) se propagent comme en V2.
        """
        async for evenement in self._orchestrateur.traiter_stream(
            question, langue, client_ip, historique
        ):
            yield evenement
