"""Agent Météo : conseil sensible au climat (fenêtres de traitement/récolte).

Tool use : récupère des prévisions via OutilMeteo puis les injecte comme contexte
factuel dans le prompt. Le modèle raisonne sur des données fraîches, pas sa mémoire.

Trois cas, évalués sur tout le fil (historique + dernier tour) :
- localité cacaoyère détectée -> prévisions Open-Meteo ;
- localité non cacaoyère du Nord -> consigne de redirection (pas une zone cacao) ;
- aucune localité -> consigne demandant la commune (jamais de météo inventée).
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.domain.agents import AgentRequete
from app.domain.ports import InferencePort
from app.services import localites
from app.services.agents.base import AgentBase, compter_mots_cles
from app.services.outils.meteo import OutilMeteo

logger = get_logger(__name__)

# Déclencheurs CLIMATIQUES uniquement. On exclut volontairement les termes
# d'agronomie générale (« traiter », « récolte », « temps ») : ambigus, ils
# détournaient des questions ancrées sur le RAG vers la météo. En l'absence de mot
# climatique, le conseil revient à l'agent RAG (généraliste). Routage par MOT ENTIER.
_MOTS_METEO = (
    "pluie",
    "pluies",
    "pleuvoir",
    "pleut",
    "precipitation",
    "precipitations",
    "précipitation",
    "précipitations",
    "prevision",
    "previsions",
    "prévision",
    "prévisions",
    "averse",
    "averses",
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

# Consigne quand aucune commune n'est précisée : on ne fabrique JAMAIS de météo, on
# demande la localité (même pattern de souveraineté que l'agent Prix sans cours).
_CONSIGNE_COMMUNE = (
    "Aucune commune n'a été précisée : aucune prévision météo locale fiable n'est "
    "disponible. N'avance AUCUNE donnée météo et n'en invente sous aucun prétexte. "
    "Demande poliment au producteur dans quelle commune (zone cacaoyère) il se "
    "trouve, afin de lui fournir une prévision locale au prochain échange."
)


def _consigne_nord(localite: str) -> str:
    """Consigne pour une localité de savane du Nord (non cacaoyère)."""
    return (
        f"La localité {localite} se situe dans la zone de savane du nord de la Côte "
        "d'Ivoire, au climat trop sec et à la saison des pluies trop courte pour le "
        "cacaoyer : ce n'est pas une zone cacaoyère. N'avance AUCUNE prévision ni "
        "conseil de culture du cacao pour cette localité. Explique avec tact au "
        "producteur qu'elle n'est pas concernée par la culture du cacao et oriente-le "
        "vers l'ANADER pour les cultures adaptées à sa région."
    )


class AgentMeteo(AgentBase):
    """Conseil agronomique tenant compte des prévisions météo locales."""

    nom = "meteo"
    description = "Conseil sensible au climat : fenêtres de traitement et de récolte."
    mots_cles = _MOTS_METEO

    def __init__(self, inference: InferencePort, outil: OutilMeteo) -> None:
        """Initialise l'agent Météo.

        Args:
            inference: Port d'inférence.
            outil: Outil de récupération des prévisions.
        """
        super().__init__(inference)
        self._outil = outil

    async def peut_traiter(self, requete: AgentRequete) -> float:
        """Score élevé si la question évoque le climat (mot entier)."""
        touches = compter_mots_cles(requete.fil_ancre, self.mots_cles)
        if touches == 0:
            return 0.0
        return min(0.7 + 0.1 * touches, 1.0)

    async def _contexte(self, requete: AgentRequete) -> str | None:
        """Construit le contexte selon la localité détectée sur tout le fil."""
        texte = _fil_complet(requete)
        localite = localites.detecter(texte)
        if localite is not None:
            previsions = await self._outil.invoquer(localite=localite)
            if not previsions:
                # Localité valide mais source météo indisponible : on le signale
                # (observabilité) ; le contexte sera None -> dégradation propre.
                logger.warning("meteo_previsions_vides", localite=localite)
            return _formater_previsions(localite, previsions)
        nord = localites.detecter_nord(texte)
        if nord is not None:
            return _consigne_nord(nord)
        return _CONSIGNE_COMMUNE


def _fil_complet(requete: AgentRequete) -> str:
    """Concatène les tours utilisateur de l'historique et le dernier tour ancré.

    Une ville citée plus tôt dans la conversation reste ainsi connue au tour suivant.
    """
    tours = [t.get("content", "") for t in requete.historique if t.get("role") == "user"]
    return " ".join([*tours, requete.fil_ancre])


def _formater_previsions(localite: str, previsions: dict[str, object]) -> str | None:
    """Met en forme les prévisions en contexte injectable, ou None si vide."""
    if not previsions:
        return None
    resume = previsions.get("resume", "")
    pluie = previsions.get("pluie_mm_24h", "?")
    return f"Prévisions météo pour {localite} : {resume} (pluie 24h : {pluie} mm)."
