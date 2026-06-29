"""Squelette commun aux agents : appel inférence + post-traitement (DRY).

Chaque agent concret ne définit que sa spécificité (contexte à injecter, score
d'aptitude). La mécanique « appeler le LLM → extraire les sources → estimer la
confiance → attribuer la réponse » est mutualisée ici (pattern Template Method).
"""

from __future__ import annotations

import re

from app.domain.agents import AgentReponse, AgentRequete
from app.domain.ports import InferencePort
from app.services import postprocess

_TOKEN = re.compile(r"\w+", re.UNICODE)


def compter_mots_cles(texte: str, mots_cles: tuple[str, ...]) -> int:
    """Compte les mots-clés présents dans le texte, par MOT ENTIER (pas sous-chaîne).

    Le matching par sous-chaîne (``mot in texte``) produit des faux positifs :
    « cours » ∈ « discours », « temps » ∈ « printemps », « vend » ∈ « vendredi ».
    On tokenise donc le texte et on compare mot à mot. Les déclencheurs multi-mots
    (« tableau de bord », « bord champ ») sont cherchés comme expression littérale.

    Args:
        texte: Texte à analyser (typiquement le fil ancré de la requête).
        mots_cles: Termes déclencheurs de l'agent.

    Returns:
        Le nombre de mots-clés distincts trouvés.
    """
    bas = texte.lower()
    tokens = set(_TOKEN.findall(bas))
    touches = 0
    for mot in mots_cles:
        if " " in mot or "-" in mot:
            if mot in bas:
                touches += 1
        elif mot in tokens:
            touches += 1
    return touches


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
