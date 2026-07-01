"""Agent Normes : référentiels de durabilité et de qualité du cacao.

Agent n°6 du socle — deuxième ajout par la « recette » (AgentBase + mots-clés +
enregistrement), sur le même moule que l'agent Réglementation/EUDR. Informationnel
et ancré : il récupère le contexte RAG sur la question de certification/norme et y
préfixe un cadrage factuel des référentiels, puis génère.

Périmètre : les certifications volontaires (Rainforest Alliance, Fairtrade / commerce
équitable, agriculture biologique) et les normes de qualité (ISO, norme régionale
africaine ARS 1000 sur le cacao durable). Ces référentiels conditionnent l'accès à
certains marchés et primes — sujet cacao, donc admis par les garde-fous.

Frontière avec l'agent Réglementation : celui-ci couvre l'accès marché *contraignant*
(EUDR, loi UE, export, douane) ; Normes couvre les référentiels *volontaires* et de
qualité. Les mots-clés `certification`/`durabilité` sont confiés à Normes (retirés de
Réglementation) pour un routage sans ambiguïté.
"""

from __future__ import annotations

from app.domain.agents import AgentRequete
from app.domain.ports import InferencePort
from app.services.agents.base import AgentBase, compter_mots_cles
from app.services.rag import RagRecuperateur

# Cadrage factuel et général (les détails proviennent du contexte RAG, jamais
# inventés) : situe la question dans les référentiels de durabilité et de qualité.
_CADRE_NORMES = (
    "Cadre : référentiels de durabilité et de qualité du cacao — certifications "
    "volontaires (Rainforest Alliance, Fairtrade / commerce équitable, agriculture "
    "biologique), normes de qualité (ISO) et la norme régionale africaine ARS 1000 "
    "sur le cacao durable. Ces référentiels conditionnent l'accès à certains marchés "
    "et primes."
)

# Sans document RAG, on interdit d'inventer les détails : critères, seuils, montants de
# prime, exigences d'audit et dates de validité varient selon l'organisme certificateur
# et la campagne. Une valeur mémorisée par le modèle serait probablement fausse ou périmée
# (violation de souveraineté sur une donnée officielle, et les primes ne sont pas garanties).
_CADRE_NORMES_SANS_DOC = (
    _CADRE_NORMES + "\n\n"
    "Aucun document détaillé sur ces référentiels n'est disponible ici. N'avance AUCUN "
    "critère précis, seuil, montant de prime, exigence d'audit ni date de validité : ces "
    "éléments varient selon l'organisme et la campagne. Explique le principe général puis "
    "oriente le producteur vers l'organisme certificateur, sa coopérative ou son agent "
    "ANADER pour les modalités officielles à jour."
)

_MOTS_NORMES = (
    "certification",
    "certifications",
    "certifie",
    "certifié",
    "certifier",
    "certifiee",
    "certifiée",
    "label",
    "labels",
    "labellisation",
    "rainforest",
    "fairtrade",
    "utz",
    "bio",
    "biologique",
    "organic",
    "iso",
    "norme",
    "normes",
    "referentiel",
    "référentiel",
    "referentiels",
    "référentiels",
    "durabilite",
    "durabilité",
    "durable",
    "durables",
    "equitable",
    "équitable",
    "ars 1000",
    "commerce equitable",
    "commerce équitable",
    "rainforest alliance",
)


class AgentNormes(AgentBase):
    """Conseil sur les référentiels de durabilité et de qualité du cacao."""

    nom = "normes"
    description = "Référentiels de durabilité/qualité du cacao (Rainforest, Fairtrade, ARS 1000)."
    mots_cles = _MOTS_NORMES

    def __init__(self, inference: InferencePort, rag: RagRecuperateur | None = None) -> None:
        """Initialise l'agent Normes.

        Args:
            inference: Port d'inférence.
            rag: Récupérateur de contexte documentaire, ou None (cadrage seul).
        """
        super().__init__(inference)
        self._rag = rag

    async def peut_traiter(self, requete: AgentRequete) -> float:
        """Score élevé si la question évoque une certification/norme (mot entier)."""
        touches = compter_mots_cles(requete.fil_ancre, self.mots_cles)
        if touches == 0:
            return 0.0
        return min(0.7 + 0.1 * touches, 1.0)

    async def _contexte(self, requete: AgentRequete) -> str | None:
        """Préfixe le contexte RAG du cadrage référentiels (ou le cadrage seul)."""
        contexte_rag = await self._rag.contexte_pour(requete.fil_ancre) if self._rag else None
        return f"{_CADRE_NORMES}\n\n{contexte_rag}" if contexte_rag else _CADRE_NORMES_SANS_DOC
