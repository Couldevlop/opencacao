"""Agent RAG : conseil agronomique ancré sur sources officielles.

Refonte du RAG V2 en agent. Sert aussi d'agent par défaut (repli généraliste)
quand le routeur ne sait pas trancher.
"""

from __future__ import annotations

from app.domain.agents import AgentReponse, AgentRequete
from app.domain.ports import InferencePort
from app.services.agents.base import AgentBase
from app.services.rag import RagRecuperateur


class AgentRag(AgentBase):
    """Conseil agronomique cacao ancré sur CNRA/ANADER/CCC/FIRCA."""

    nom = "rag"
    description = "Conseil agronomique cacao ancré sur les sources officielles."
    mots_cles = ()  # agent généraliste : pas de déclencheur spécifique

    def __init__(self, inference: InferencePort, rag: RagRecuperateur | None = None) -> None:
        """Initialise l'agent RAG.

        Args:
            inference: Port d'inférence.
            rag: Récupérateur de contexte documentaire, ou None (sans contexte).
        """
        super().__init__(inference)
        self._rag = rag

    async def peut_traiter(self, requete: AgentRequete) -> float:
        """Score de repli : l'agronomie générale couvre toute question cacao."""
        return 0.4

    async def traiter(self, requete: AgentRequete) -> AgentReponse:
        """Récupère le contexte RAG (best-effort) puis génère une réponse ancrée."""
        contexte = await self._rag.contexte_pour(requete.fil_ancre) if self._rag else None
        return await self._generer(requete, contexte)
