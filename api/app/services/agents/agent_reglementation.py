"""Agent Réglementation : accès au marché du cacao (EUDR, conformité export).

Agent n°5 du socle — premier ajout par la « recette » (AgentBase + mots-clés +
enregistrement). Informationnel et ancré : il récupère le contexte RAG sur la
question réglementaire et y préfixe un cadrage EUDR factuel, puis génère. Il se
distingue de l'agent RAG généraliste par son routage (déclencheurs réglementaires)
et son cadrage spécialisé.

Périmètre : l'EUDR (règlement UE contre la déforestation) conditionne l'accès du
cacao ivoirien au marché européen — sujet cacao, donc admis par les garde-fous.
"""

from __future__ import annotations

from app.domain.agents import AgentReponse, AgentRequete
from app.domain.ports import InferencePort
from app.services.agents.base import AgentBase, compter_mots_cles
from app.services.rag import RagRecuperateur

# Cadrage factuel et général (les détails proviennent du contexte RAG, jamais
# inventés) : situe la question dans la réglementation d'accès au marché du cacao.
_CADRE_EUDR = (
    "Cadre : réglementation d'accès au marché du cacao, dont le règlement de l'Union "
    "européenne contre la déforestation (EUDR) — traçabilité à la parcelle "
    "(géolocalisation) et diligence raisonnée pour l'export du cacao vers l'UE."
)

_MOTS_REGLEMENTATION = (
    "eudr",
    "deforestation",
    "déforestation",
    "tracabilite",
    "traçabilité",
    "geolocalisation",
    "géolocalisation",
    "reglementation",
    "réglementation",
    "reglement",
    "règlement",
    "conformite",
    "conformité",
    "diligence",
    "export",
    "exporter",
    "exportation",
    "exportateur",
    "douane",
    "certification",
    "europeen",
    "européen",
    "europeenne",
    "européenne",
    "ue",
    "durabilite",
    "durabilité",
    "due diligence",
    "diligence raisonnee",
    "diligence raisonnée",
    "union europeenne",
    "union européenne",
)


class AgentReglementation(AgentBase):
    """Conseil sur la réglementation d'accès au marché du cacao (EUDR en tête)."""

    nom = "reglementation"
    description = "Réglementation d'accès au marché du cacao (EUDR, traçabilité, export UE)."
    mots_cles = _MOTS_REGLEMENTATION

    def __init__(self, inference: InferencePort, rag: RagRecuperateur | None = None) -> None:
        """Initialise l'agent Réglementation.

        Args:
            inference: Port d'inférence.
            rag: Récupérateur de contexte documentaire, ou None (cadrage seul).
        """
        super().__init__(inference)
        self._rag = rag

    async def peut_traiter(self, requete: AgentRequete) -> float:
        """Score élevé si la question évoque la réglementation/EUDR (mot entier)."""
        touches = compter_mots_cles(requete.fil_ancre, self.mots_cles)
        if touches == 0:
            return 0.0
        return min(0.7 + 0.1 * touches, 1.0)

    async def traiter(self, requete: AgentRequete) -> AgentReponse:
        """Récupère le contexte RAG, le préfixe du cadre EUDR, puis génère."""
        contexte_rag = await self._rag.contexte_pour(requete.fil_ancre) if self._rag else None
        contexte = f"{_CADRE_EUDR}\n\n{contexte_rag}" if contexte_rag else _CADRE_EUDR
        return await self._generer(requete, contexte)
