"""Agent Prix : aide à la commercialisation (prix/marché/change du cacao).

Tool use : récupère le cours via OutilPrix et l'injecte comme contexte factuel.
"""

from __future__ import annotations

from app.domain.agents import AgentRequete
from app.domain.ports import InferencePort
from app.services.agents.base import AgentBase, compter_mots_cles
from app.services.outils.prix import OutilPrix
from app.services.rag import RagRecuperateur

# Déclencheurs MARCHÉ. Routage par mot entier : on liste donc les formes verbales
# (« vend », « vendre »…) plutôt qu'un radical, et on évite « marche » (≠ « marché »)
# qui matcherait « il marche ». Les expressions multi-mots sont gérées telles quelles.
_MOTS_PRIX = (
    "prix",
    "vend",
    "vends",
    "vendre",
    "vendu",
    "vendez",
    "vente",
    "ventes",
    "marché",
    "fcfa",
    "cours",
    "kilo",
    "kg",
    "bord-champ",
    "bord champ",
    "campagne",
    "acheteur",
    "acheteurs",
    "commercialisation",
)


class AgentPrix(AgentBase):
    """Synthèses et alertes d'aide à la commercialisation du cacao."""

    nom = "prix"
    description = "Prix/marché/change du cacao : aide à la commercialisation."
    mots_cles = _MOTS_PRIX

    def __init__(
        self,
        inference: InferencePort,
        outil: OutilPrix,
        rag: RagRecuperateur | None = None,
    ) -> None:
        """Initialise l'agent Prix.

        Args:
            inference: Port d'inférence.
            outil: Outil de récupération du cours.
            rag: Récupérateur documentaire optionnel : apporte les prix qui ne sont
                pas la valeur administrée unique (mise à marché, historique, café…),
                déjà indexés dans le corpus. None = agent limité au seul cours.
        """
        super().__init__(inference)
        self._outil = outil
        self._rag = rag

    async def peut_traiter(self, requete: AgentRequete) -> float:
        """Score élevé si la question évoque le prix, la vente ou le marché (mot entier)."""
        touches = compter_mots_cles(requete.fil_ancre, self.mots_cles)
        if touches == 0:
            return 0.0
        return min(0.7 + 0.1 * touches, 1.0)

    async def _contexte(self, requete: AgentRequete) -> str | None:
        """Combine le prix bord-champ officiel et le contexte documentaire RAG.

        Le cours administré (autoritaire) ancre la réponse ; le RAG complète avec les
        autres prix (mise à marché, historique, café). Ainsi l'agent n'est pas plus
        pauvre que le chemin RAG sur les questions de marché.
        """
        cours = _formater_cours(await self._outil.invoquer())
        documentaire = await self._rag.contexte_pour(requete.fil_ancre) if self._rag else None
        return "\n\n".join(part for part in (cours, documentaire) if part)


def _formater_cours(cours: dict[str, object]) -> str:
    """Met en forme le cours en contexte injectable.

    Garde-fou souveraineté (non négociable) : si aucun prix officiel n'est
    disponible, on n'injecte PAS un contexte vide (qui laisserait le modèle
    fabriquer un chiffre — bug observé en prod). On injecte une consigne explicite
    interdisant d'avancer un prix et redirigeant vers la source officielle.
    """
    if not cours:
        return (
            "Aucun prix bord-champ officiel n'est disponible dans le système. "
            "N'avance AUCUN chiffre de prix et n'en invente sous aucun prétexte : "
            "explique au producteur que le prix officiel est fixé par le Conseil du "
            "Café-Cacao et invite-le à le vérifier auprès du Conseil du Café-Cacao "
            "ou de son agent ANADER local."
        )
    prix = cours.get("prix_bord_champ_fcfa_kg", "?")
    campagne = cours.get("campagne", "")
    # Le prix officiel doit PRIMER sur d'éventuels prix historiques présents dans le
    # contexte RAG (mise à marché, campagnes passées). Sans cette autorité explicite,
    # le modèle attrapait parfois un montant périmé du RAG (bug prod : « 850 FCFA »).
    return (
        f"PRIX BORD-CHAMP OFFICIEL ACTUEL (autoritaire) : {prix} FCFA/kg "
        f"(campagne {campagne}). C'est LE prix actuel : utilise CE chiffre pour toute "
        "question sur le prix actuel du cacao. Tout autre montant en FCFA présent dans "
        "le contexte ci-dessous est un prix HISTORIQUE ou indicatif (campagnes passées, "
        "mise à marché, café) : ne le présente JAMAIS comme le prix actuel."
    )
