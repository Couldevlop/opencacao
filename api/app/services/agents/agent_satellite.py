"""Agent Satellite : alertes de déforestation autour de la position (contexte EUDR).

Tool use : récupère les alertes intégrées GFW via OutilSatellite et les injecte
comme faits datés dans le prompt. L'agent CONSTATE des alertes, il ne certifie
JAMAIS une conformité EUDR (le cutoff réglementaire est le 31/12/2020).

Localisation, par priorité : coordonnées GPS trouvées dans le fil (bornées à la
Côte d'Ivoire), sinon localité nommée (module partagé localites), sinon consigne
demandant la position — jamais de statut inventé.
"""

from __future__ import annotations

import re

from app.domain.agents import AgentRequete
from app.domain.ports import InferencePort
from app.services import localites
from app.services.agents.base import AgentBase, compter_mots_cles
from app.services.outils.satellite import OutilSatellite
from app.services.rag import RagRecuperateur

# Déclencheurs SATELLITAIRES/fonciers. « déforestation » et « géolocalisation »
# migrent de l'agent Réglementation (frontière nette, comme certification -> Normes) ;
# « eudr », « traçabilité », « conformité » restent réglementaires. PAS « alerte »
# seul (happerait « alerte pluie », domaine météo). Routage par MOT ENTIER.
#
# « parcelle »/« forêt » seuls sont des mots FAIBLES : ils ne comptent que si un mot
# FORT (déforestation/satellite/géolocalisation) est aussi présent dans le fil.
# Sinon « parcelle » seul détournait des questions hors sujet (ex. « traiter les
# chenilles sur ma parcelle ») du RAG phytosanitaire vers un appel GFW inutile —
# revue finale 05/07.
_MOTS_FORTS = (
    "deforestation",
    "déforestation",
    "satellite",
    "geolocalisation",
    "géolocalisation",
)
_MOTS_FAIBLES = (
    "parcelle",
    "parcelles",
    "foret",
    "forêt",
    "forets",
    "forêts",
)
_MOTS_SATELLITE = _MOTS_FORTS + _MOTS_FAIBLES

# Coordonnées décimales « lat, lon » bornées Côte d'Ivoire (lat 4..11, lon -9..-2).
_COORDONNEES = re.compile(r"(-?\d{1,2}[.,]\d+)\s*[,;]\s*(-?\d{1,2}[.,]\d+)")

_CONSIGNE_POSITION = (
    "Aucune position n'a été précisée : aucune vérification satellitaire n'est "
    "possible. N'avance AUCUN constat de déforestation et n'invente aucun statut. "
    "Demande poliment au producteur sa commune (zone cacaoyère) ou ses coordonnées "
    "GPS (affichées par son téléphone), afin de vérifier les alertes au prochain "
    "échange."
)

_CONSIGNE_INDISPONIBLE = (
    "La vérification satellitaire est momentanément indisponible. N'avance AUCUN "
    "constat de déforestation, n'invente aucun statut et ne certifie rien : invite "
    "le producteur à se rapprocher du Conseil du Café-Cacao ou de son agent ANADER "
    "pour la vérification de sa parcelle."
)

_RESERVE = (
    "IMPORTANT : ce constat porte sur une zone d'environ 1 km autour du point "
    "fourni, PAS sur la parcelle cadastrée du producteur. Ne certifie JAMAIS une "
    "conformité ou non-conformité EUDR : constate les faits, explique ce qu'ils "
    "impliquent et oriente vers le Conseil du Café-Cacao ou l'agent ANADER pour "
    "toute démarche officielle."
)


class AgentSatellite(AgentBase):
    """Constats satellitaires de déforestation autour de la position du producteur."""

    nom = "satellite"
    description = "Alertes satellitaires de déforestation (contexte EUDR) autour d'un point."
    mots_cles = _MOTS_SATELLITE

    def __init__(
        self,
        inference: InferencePort,
        outil: OutilSatellite,
        rag: RagRecuperateur | None = None,
    ) -> None:
        """Initialise l'agent Satellite.

        Args:
            inference: Port d'inférence.
            outil: Outil de récupération des alertes satellitaires.
            rag: Récupérateur documentaire optionnel (contexte réglementaire EUDR).
        """
        super().__init__(inference)
        self._outil = outil
        self._rag = rag

    async def peut_traiter(self, requete: AgentRequete) -> float:
        """Score élevé si un mot FORT est présent ; les mots faibles ne font que
        renforcer un mot fort déjà détecté (voir commentaire sur ``_MOTS_FAIBLES``)."""
        forts = compter_mots_cles(requete.fil_ancre, _MOTS_FORTS)
        if forts == 0:
            return 0.0
        faibles = compter_mots_cles(requete.fil_ancre, _MOTS_FAIBLES)
        return min(0.7 + 0.1 * (forts + faibles), 1.0)

    async def _contexte(self, requete: AgentRequete) -> str | None:
        """Faits satellitaires (ou consigne) + contexte réglementaire RAG."""
        faits = await self._faits(requete)
        documentaire = await self._rag.contexte_pour(requete.fil_ancre) if self._rag else None
        return "\n\n".join(part for part in (faits, documentaire) if part)

    async def _faits(self, requete: AgentRequete) -> str:
        """Interroge l'outil selon la localisation détectée sur tout le fil."""
        texte = _fil_complet(requete)
        point = _coordonnees(texte)
        if point is not None:
            resultat = await self._outil.invoquer(lat=point[0], lon=point[1])
        else:
            localite = localites.detecter(texte)
            if localite is None:
                return _CONSIGNE_POSITION
            resultat = await self._outil.invoquer(localite=localite)
        return _formater_alertes(resultat)


def _fil_complet(requete: AgentRequete) -> str:
    """Concatène les tours utilisateur et le dernier tour ancré (mémoire du fil)."""
    tours = [t.get("content", "") for t in requete.historique if t.get("role") == "user"]
    return " ".join([*tours, requete.fil_ancre])


def _coordonnees(texte: str) -> tuple[float, float] | None:
    """Première paire « lat, lon » plausible en Côte d'Ivoire, ou None."""
    for correspondance in _COORDONNEES.finditer(texte):
        lat = float(correspondance.group(1).replace(",", "."))
        lon = float(correspondance.group(2).replace(",", "."))
        if 4.0 <= lat <= 11.0 and -9.0 <= lon <= -2.0:
            return lat, lon
    return None


def _formater_alertes(resultat: dict[str, object]) -> str:
    """Met en forme les alertes en contexte injectable, avec la réserve de portée."""
    if not resultat:
        return _CONSIGNE_INDISPONIBLE
    total = resultat.get("alertes_depuis_2021", 0)
    if not total:
        return (
            "Constat satellitaire (Global Forest Watch) : 0 alerte de déforestation "
            f"détectée depuis 2021 dans la zone (~{resultat.get('tampon_km', 1)} km "
            f"autour du point fourni). {_RESERVE}"
        )
    dates_recentes = resultat.get("dates_recentes", [])  # type: ignore[union-attr]
    tampon = resultat.get("tampon_km", 1)
    if not dates_recentes:
        # Alertes anciennes uniquement (aucune dans la fenêtre « dates récentes ») :
        # ne JAMAIS clore sur « Dernières dates d'alerte : . », une chaîne tronquée
        # qui invite le LLM à inventer une date — revue finale 05/07.
        return (
            f"Constat satellitaire (Global Forest Watch) : {total} alertes de "
            "déforestation détectées depuis 2021 dans la zone "
            f"(~{tampon} km autour du point fourni). Aucune alerte récente n'est "
            "datée ; les alertes détectées datent d'avant l'an dernier. N'invente "
            "AUCUNE date. Explique les implications au regard du règlement EUDR "
            f"(déforestation zéro après le 31/12/2020). {_RESERVE}"
        )
    dates = ", ".join(str(d) for d in dates_recentes)
    return (
        f"Constat satellitaire (Global Forest Watch) : {total} alertes de "
        "déforestation détectées depuis 2021 dans la zone "
        f"(~{tampon} km autour du point fourni). Dernières "
        f"dates d'alerte : {dates}. Explique les implications au regard du règlement "
        f"EUDR (déforestation zéro après le 31/12/2020). {_RESERVE}"
    )
