"""Registre dynamique des agents de la plateforme V3.

Annuaire ouvert à l'extension : enregistrer un agent suffit à le rendre routable.
Aucune brique d'orchestration ne change quand on ajoute l'agent n°5..n°11.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.domain.agents import AgentPort

logger = get_logger(__name__)


class RegistreAgents:
    """Collecte et expose les agents spécialisés disponibles."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentPort] = {}

    def enregistrer(self, agent: AgentPort) -> None:
        """Enregistre un agent. Lève ValueError si le nom est déjà pris."""
        if agent.nom in self._agents:
            raise ValueError(f"Agent « {agent.nom} » déjà enregistré")
        self._agents[agent.nom] = agent
        logger.info("agent_enregistre", agent=agent.nom)

    def obtenir(self, nom: str) -> AgentPort | None:
        """Retourne l'agent de nom donné, ou None s'il est inconnu."""
        return self._agents.get(nom)

    def tous(self) -> list[AgentPort]:
        """Retourne tous les agents enregistrés."""
        return list(self._agents.values())

    def noms(self) -> list[str]:
        """Retourne les noms des agents enregistrés."""
        return list(self._agents)
