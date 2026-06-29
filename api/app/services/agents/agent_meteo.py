"""Agent Météo : conseil sensible au climat (fenêtres de traitement/récolte).

Tool use : récupère des prévisions via OutilMeteo puis les injecte comme contexte
factuel dans le prompt. Le modèle raisonne sur des données fraîches, pas sa mémoire.
"""

from __future__ import annotations

import re

from app.domain.agents import AgentRequete
from app.domain.ports import InferencePort
from app.services.agents.base import AgentBase, compter_mots_cles
from app.services.outils.meteo import OutilMeteo

# Déclencheurs CLIMATIQUES uniquement. On exclut volontairement les termes
# d'agronomie générale (« traiter », « récolte », « temps ») : ambigus, ils
# détournaient des questions ancrées sur le RAG vers la météo. En l'absence de mot
# climatique, le conseil revient à l'agent RAG (généraliste). Routage par MOT ENTIER.
_MOTS_METEO = (
    "pluie",
    "pluies",
    "pleuvoir",
    "pleut",
    "meteo",
    "météo",
    "climat",
    "climatique",
    "saison",
    "saisons",
    "secher",
    "sécher",
    "sechage",
    "séchage",
    "ensoleillement",
    "soleil",
    "humidite",
    "humidité",
    "fenetre",
    "fenêtre",
    "irrigation",
    "arrosage",
)


class AgentMeteo(AgentBase):
    """Conseil agronomique tenant compte des prévisions météo locales."""

    nom = "meteo"
    description = "Conseil sensible au climat : fenêtres de traitement et de récolte."
    mots_cles = _MOTS_METEO

    def __init__(
        self,
        inference: InferencePort,
        outil: OutilMeteo,
        geo_defaut: str = "Côte d'Ivoire",
    ) -> None:
        """Initialise l'agent Météo.

        Args:
            inference: Port d'inférence.
            outil: Outil de récupération des prévisions.
            geo_defaut: Localité par défaut si aucune n'est détectée.
        """
        super().__init__(inference)
        self._outil = outil
        self._geo_defaut = geo_defaut

    async def peut_traiter(self, requete: AgentRequete) -> float:
        """Score élevé si la question évoque le climat (mot entier)."""
        touches = compter_mots_cles(requete.fil_ancre, self.mots_cles)
        if touches == 0:
            return 0.0
        return min(0.7 + 0.1 * touches, 1.0)

    async def _contexte(self, requete: AgentRequete) -> str | None:
        """Récupère les prévisions de la localité détectée et les met en contexte."""
        localite = _detecter_localite(requete.fil_ancre) or self._geo_defaut
        previsions = await self._outil.invoquer(localite=localite)
        return _formater_previsions(localite, previsions)


def _detecter_localite(texte: str) -> str | None:
    """Détection minimale de localité (préfixe « à <Ville> »). Heuristique simple.

    Note : une détection robuste réutilisera l'annuaire ``services/contacts.py``
    (60 zones connues). Pour le socle, on reste minimal et testable.
    """
    match = re.search(r"\bà\s+([A-ZÉÈÀ][\wÀ-ÿ-]+)", texte)
    return match.group(1) if match else None


def _formater_previsions(localite: str, previsions: dict[str, object]) -> str | None:
    """Met en forme les prévisions en contexte injectable, ou None si vide."""
    if not previsions:
        return None
    resume = previsions.get("resume", "")
    pluie = previsions.get("pluie_mm_24h", "?")
    return f"Prévisions météo pour {localite} : {resume} (pluie 24h : {pluie} mm)."
