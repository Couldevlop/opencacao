"""Agent Reporting : synthèse narrative de contributions multi-agents.

Premier pas vers l'orchestration multi-agents : compose la sortie de plusieurs
agents (RAG, Météo, Prix) en un rapport décisionnel cohérent.
"""

from __future__ import annotations

from app.domain.agents import AgentReponse, AgentRequete
from app.domain.ports import InferencePort
from app.models.domain import Confiance
from app.services.agents.base import AgentBase, compter_mots_cles

_MOTS_REPORTING = (
    "rapport",
    "synthese",
    "synthèse",
    "bilan",
    "resume",
    "résumé",
    "tableau de bord",
    "recapitulatif",
    "récapitulatif",
    "point complet",
)


class AgentReporting(AgentBase):
    """Compose une synthèse narrative à partir d'autres réponses d'agents."""

    nom = "reporting"
    description = "Rapports et synthèses narratives multi-agents."
    mots_cles = _MOTS_REPORTING

    def __init__(self, inference: InferencePort) -> None:
        """Initialise l'agent Reporting."""
        super().__init__(inference)

    async def peut_traiter(self, requete: AgentRequete) -> float:
        """Score élevé si l'utilisateur demande une synthèse ou un bilan (mot entier)."""
        touches = compter_mots_cles(requete.fil_ancre, self.mots_cles)
        if touches == 0:
            return 0.0
        return min(0.7 + 0.1 * touches, 1.0)

    async def traiter(self, requete: AgentRequete) -> AgentReponse:
        """Sans contributions fournies, se comporte comme une synthèse simple."""
        return await self._generer(requete, contexte=None)

    async def synthetiser(
        self, requete: AgentRequete, contributions: list[AgentReponse]
    ) -> AgentReponse:
        """Fusionne plusieurs réponses d'agents en une synthèse narrative.

        Args:
            requete: Requête originale.
            contributions: Réponses produites par d'autres agents.

        Returns:
            Une réponse de synthèse attribuée à l'agent reporting, dont les
            sources agrègent celles des contributions.
        """
        contexte = _formater_contributions(contributions)
        base = await self._generer(requete, contexte)
        sources = _agreger_sources(contributions)
        return AgentReponse(
            texte=base.texte,
            sources=sources,
            confiance=_confiance_min(contributions) or base.confiance,
            agent=self.nom,
        )


def _formater_contributions(contributions: list[AgentReponse]) -> str | None:
    """Met en forme les contributions en contexte de synthèse, ou None si vide."""
    if not contributions:
        return None
    lignes = [f"[{c.agent}] {c.texte}" for c in contributions]
    return "Éléments à synthétiser :\n" + "\n".join(lignes)


def _agreger_sources(contributions: list[AgentReponse]) -> list[str]:
    """Union ordonnée des sources de toutes les contributions (sans doublon)."""
    vues: list[str] = []
    for contribution in contributions:
        for source in contribution.sources:
            if source not in vues:
                vues.append(source)
    return vues


def _confiance_min(contributions: list[AgentReponse]) -> Confiance | None:
    """Confiance la plus basse parmi les contributions (prudence), ou None."""
    if not contributions:
        return None
    ordre = {Confiance.FAIBLE: 0, Confiance.MOYENNE: 1, Confiance.ELEVEE: 2}
    return min((c.confiance for c in contributions), key=lambda c: ordre[c])
