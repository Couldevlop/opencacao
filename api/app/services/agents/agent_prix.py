"""Agent Prix : aide à la commercialisation (prix/marché/change du cacao).

Tool use : récupère le cours via OutilPrix et l'injecte comme contexte factuel.
"""

from __future__ import annotations

from app.domain.agents import AgentReponse, AgentRequete
from app.domain.ports import InferencePort
from app.services.agents.base import AgentBase, compter_mots_cles
from app.services.outils.prix import OutilPrix

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

    def __init__(self, inference: InferencePort, outil: OutilPrix) -> None:
        """Initialise l'agent Prix.

        Args:
            inference: Port d'inférence.
            outil: Outil de récupération du cours.
        """
        super().__init__(inference)
        self._outil = outil

    async def peut_traiter(self, requete: AgentRequete) -> float:
        """Score élevé si la question évoque le prix, la vente ou le marché (mot entier)."""
        touches = compter_mots_cles(requete.fil_ancre, self.mots_cles)
        if touches == 0:
            return 0.0
        return min(0.7 + 0.1 * touches, 1.0)

    async def traiter(self, requete: AgentRequete) -> AgentReponse:
        """Récupère le cours et génère une synthèse de commercialisation."""
        cours = await self._outil.invoquer()
        contexte = _formater_cours(cours)
        return await self._generer(requete, contexte)


def _formater_cours(cours: dict[str, object]) -> str | None:
    """Met en forme le cours en contexte injectable, ou None si vide."""
    if not cours:
        return None
    prix = cours.get("prix_bord_champ_fcfa_kg", "?")
    campagne = cours.get("campagne", "")
    return f"Prix bord-champ de référence : {prix} FCFA/kg (campagne {campagne})."
