"""Squelette commun aux agents : appel inférence + post-traitement (DRY).

Chaque agent concret ne définit que sa spécificité (contexte à injecter, score
d'aptitude). La mécanique « appeler le LLM → extraire les sources → estimer la
confiance → attribuer la réponse » est mutualisée ici (pattern Template Method).
"""

from __future__ import annotations

from app.domain.agents import AgentReponse, AgentRequete
from app.domain.ports import InferencePort
from app.services import postprocess


class AgentBase:
    """Base optionnelle : génère une réponse ancrée via le port d'inférence."""

    nom: str = "base"
    description: str = ""
    mots_cles: tuple[str, ...] = ()

    def __init__(self, inference: InferencePort) -> None:
        """Initialise l'agent avec son port d'inférence."""
        self._inference = inference

    async def _generer(self, requete: AgentRequete, contexte: str | None) -> AgentReponse:
        """Appelle l'inférence avec un contexte donné et post-traite la sortie."""
        texte = await self._inference.generer(
            requete.question, contexte=contexte, historique=requete.historique
        )
        sources = postprocess.extraire_sources(texte)
        return AgentReponse(
            texte=texte,
            sources=sources,
            confiance=postprocess.estimer_confiance(sources),
            agent=self.nom,
        )
