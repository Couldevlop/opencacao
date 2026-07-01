"""Squelette commun aux agents : appel inférence + post-traitement (DRY).

Chaque agent concret ne définit que sa spécificité (contexte à injecter, score
d'aptitude). La mécanique « appeler le LLM → extraire les sources → estimer la
confiance → attribuer la réponse » est mutualisée ici (pattern Template Method).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

from app.domain.agents import AgentReponse, AgentRequete
from app.domain.ports import InferencePort
from app.services import postprocess

_TOKEN = re.compile(r"\w+", re.UNICODE)

# Sentinelle : « contexte non fourni » (à calculer par l'agent). Distincte de None,
# qui est un contexte valide (aucun extrait).
_A_CALCULER: object = object()


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

    async def _contexte(self, requete: AgentRequete) -> str | None:
        """Contexte à injecter au prompt — spécifique à chaque agent.

        La base ne fournit aucun contexte (un agent généraliste sans ancrage). Chaque
        agent concret l'override pour construire SON contexte (RAG, prévisions, cours…).
        ``traiter`` et ``traiter_stream`` partagent cette préparation : la spécificité
        d'un agent tient dans ``_contexte`` (et son score ``peut_traiter``).
        """
        return None

    async def traiter(self, requete: AgentRequete) -> AgentReponse:
        """Construit le contexte propre à l'agent puis génère une réponse ancrée."""
        return await self._generer(requete, await self._contexte(requete))

    async def contexte_pour(self, requete: AgentRequete) -> str | None:
        """Contexte que l'agent injecterait pour cette requête.

        Exposé pour que l'appelant (orchestrateur) puisse ANCRER les sources en
        streaming — croiser les sources citées avec le contexte réellement injecté —
        sans recalculer le contexte (donc sans double récupération RAG).
        """
        return await self._contexte(requete)

    async def traiter_stream(
        self, requete: AgentRequete, contexte: str | None | object = _A_CALCULER
    ) -> AsyncIterator[str]:
        """Variante flux : streame les fragments de génération (même contexte).

        ``contexte`` peut être pré-calculé par l'appelant (via ``contexte_pour``) et
        passé ici — il sert alors aussi à ancrer les sources après le stream. Sinon il
        est calculé ici (comportement autonome).
        """
        if contexte is _A_CALCULER:
            contexte = await self._contexte(requete)
        async for fragment in self._inference.generer_stream(
            requete.question, contexte=contexte, historique=requete.historique
        ):
            yield fragment

    async def _generer(self, requete: AgentRequete, contexte: str | None) -> AgentReponse:
        """Appelle l'inférence avec un contexte donné et post-traite la sortie."""
        texte = await self._inference.generer(
            requete.question, contexte=contexte, historique=requete.historique
        )
        # Sources ANCRÉES : on croise le texte avec le contexte injecté -> la confiance
        # ne peut être élevée que si les sources citées sont réellement dans le contexte.
        sources = postprocess.extraire_sources(texte, contexte)
        return AgentReponse(
            texte=texte,
            sources=sources,
            confiance=postprocess.estimer_confiance(sources),
            agent=self.nom,
        )
