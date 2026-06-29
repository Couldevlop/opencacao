"""Routeur d'intention : choisit le(s) agent(s) pertinent(s) pour une requête.

Routage déterministe : chaque agent s'auto-évalue (``peut_traiter``) et le routeur
classe par score. Explicable, testable, souverain (aucun appel LLM). L'interface
restera stable si on bascule plus tard vers un routage sémantique.
"""

from __future__ import annotations

from app.application.registre import RegistreAgents
from app.core.logging import get_logger
from app.domain.agents import AgentPort, AgentRequete

logger = get_logger(__name__)


class RouteurIntention:
    """Classe les agents enregistrés par pertinence pour une requête."""

    def __init__(self, registre: RegistreAgents, seuil: float = 0.3) -> None:
        """Initialise le routeur.

        Args:
            registre: Registre des agents disponibles.
            seuil: Score minimal pour qu'un agent soit retenu.
        """
        self._registre = registre
        self._seuil = seuil

    async def classer(self, requete: AgentRequete) -> list[tuple[AgentPort, float]]:
        """Retourne les agents dont le score >= seuil, du plus pertinent au moins."""
        scores: list[tuple[AgentPort, float]] = []
        for agent in self._registre.tous():
            score = await agent.peut_traiter(requete)
            if score >= self._seuil:
                scores.append((agent, score))
        scores.sort(key=lambda paire: paire[1], reverse=True)
        logger.info("routage", classement=[(a.nom, round(s, 2)) for a, s in scores])
        return scores

    async def meilleur(self, requete: AgentRequete) -> AgentPort | None:
        """Retourne l'agent le plus pertinent, ou None si aucun n'atteint le seuil."""
        classement = await self.classer(requete)
        return classement[0][0] if classement else None
