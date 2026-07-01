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

from app.domain.agents import AgentRequete
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

# Sans document RAG, on interdit d'inventer les détails réglementaires : les dates
# d'entrée en vigueur de l'EUDR ont connu plusieurs reports, une date mémorisée par le
# modèle serait probablement périmée (violation de souveraineté sur une donnée officielle).
_CADRE_EUDR_SANS_DOC = (
    _CADRE_EUDR + "\n\n"
    "Aucun document réglementaire détaillé n'est disponible ici. N'avance AUCUNE date "
    "d'entrée en vigueur, aucun seuil, article ou procédure précis : ces éléments "
    "évoluent (l'EUDR a connu plusieurs reports). Explique le principe général puis "
    "oriente le producteur vers le Conseil du Café-Cacao, un exportateur certifié ou "
    "son agent ANADER pour les détails officiels à jour."
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

    async def _contexte(self, requete: AgentRequete) -> str | None:
        """Préfixe le contexte RAG du cadrage EUDR (ou le cadrage seul)."""
        contexte_rag = await self._rag.contexte_pour(requete.fil_ancre) if self._rag else None
        return f"{_CADRE_EUDR}\n\n{contexte_rag}" if contexte_rag else _CADRE_EUDR_SANS_DOC
