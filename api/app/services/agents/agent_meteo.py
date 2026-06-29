"""Agent Météo : conseil sensible au climat (fenêtres de traitement/récolte).

Tool use : récupère des prévisions via OutilMeteo puis les injecte comme contexte
factuel dans le prompt. Le modèle raisonne sur des données fraîches, pas sa mémoire.
"""

from __future__ import annotations

import re

from app.domain.agents import AgentReponse, AgentRequete
from app.domain.ports import InferencePort
from app.services.agents.base import AgentBase
from app.services.outils.meteo import OutilMeteo

_MOTS_METEO = (
    "pluie",
    "pleuvoir",
    "meteo",
    "météo",
    "temps",
    "traiter",
    "traitement",
    "secher",
    "sécher",
    "sechage",
    "séchage",
    "recolte",
    "récolte",
    "saison",
    "fenetre",
    "fenêtre",
    "humidite",
    "humidité",
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
        """Score élevé si la question évoque la météo ou une fenêtre d'action."""
        texte = requete.fil_ancre.lower()
        touches = sum(1 for mot in self.mots_cles if mot in texte)
        if touches == 0:
            return 0.0
        return min(0.7 + 0.1 * touches, 1.0)

    async def traiter(self, requete: AgentRequete) -> AgentReponse:
        """Récupère les prévisions et génère un conseil sensible au climat."""
        localite = _detecter_localite(requete.fil_ancre) or self._geo_defaut
        previsions = await self._outil.invoquer(localite=localite)
        contexte = _formater_previsions(localite, previsions)
        return await self._generer(requete, contexte)


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
