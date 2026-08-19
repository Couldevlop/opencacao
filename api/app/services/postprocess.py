"""Post-traitement de la réponse du modèle : sources citées et confiance."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

from app.models.domain import Confiance

_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "sources_agro.yaml"


@lru_cache
def _sources_connues() -> list[str]:
    """Charge la liste des noms de sources fiables depuis le référentiel."""
    with _DATA_PATH.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return list(data.get("sources_fiables", []))


def extraire_sources(reponse: str, contexte: str | None = None) -> list[str]:
    """Extrait les sources fiables citées dans le texte de la réponse.

    Souveraineté : si ``contexte`` est fourni, on ne retient que les sources
    **ancrées** — présentes AUSSI dans le contexte documentaire injecté. Cela évite
    qu'une source citée « de mémoire » par le modèle, sans appui documentaire, ne
    gonfle artificiellement la confiance (une réponse non ancrée qui mentionne
    « CNRA » et « ANADER » ne doit pas être créditée d'une confiance élevée). Sans
    contexte (None) : extraction textuelle seule (compatibilité des chemins legacy).

    Args:
        reponse: Texte généré par le modèle.
        contexte: Contexte documentaire injecté (passages RAG/consigne), ou None.

    Returns:
        Liste, sans doublon et dans l'ordre du référentiel, des sources reconnues
        (ancrées dans le contexte si celui-ci est fourni).
    """
    texte = reponse.lower()
    trouvees = [source for source in _sources_connues() if source.lower() in texte]
    if contexte is None:
        return trouvees
    ctx = contexte.lower()
    return [source for source in trouvees if source.lower() in ctx]


def estimer_confiance(sources: list[str]) -> Confiance:
    """Estime la confiance à partir du nombre de sources citées.

    Args:
        sources: Sources reconnues citées dans la réponse.

    Returns:
        Niveau de confiance : élevée (>= 2 sources), moyenne (1), faible (0).
    """
    if len(sources) >= 2:
        return Confiance.ELEVEE
    if len(sources) == 1:
        return Confiance.MOYENNE
    return Confiance.FAIBLE


# Tiret cadratin et demi-cadratin employés comme PONCTUATION : entourés d'espaces.
# C'est cette forme-là qui signe un texte généré ; le trait d'union interne aux mots
# (« San-Pédro », « Café-Cacao », « peut-être ») n'a rien à voir et doit survivre.
_TIRET_PONCTUATION = re.compile(r"\s+[—–]\s+")
# Cadratin résiduel, collé à ses voisins : « 2020—2025 ». On le ramène au trait
# d'union, qui reste lisible et n'évoque pas la génération automatique.
_TIRET_COLLE = re.compile(r"(?<=\w)[—–](?=\w)")


def nettoyer_tirets(texte: str) -> str:
    """Remplace les tirets cadratins de ponctuation, sans toucher aux traits d'union.

    Le tiret cadratin employé comme incise (« la taille — surtout la première — … »)
    est une signature de texte généré. Devant un public qui juge la crédibilité d'un
    outil souverain, cette signature travaille contre le propos.

    Ce qui est délibérément PRÉSERVÉ : les traits d'union internes aux mots (les
    supprimer produirait « SanPédro » ou « CaféCacao », c'est-à-dire une faute
    d'orthographe à l'écran), les plages de nombres, et les tirets de liste en début
    de ligne — une liste d'étapes aide réellement un producteur à suivre.

    Une consigne de prompt ne suffit pas : un modèle l'oublie une fois sur dix, et
    c'est cette fois-là qui sera projetée. D'où ce nettoyage déterministe.

    Args:
        texte: Texte généré par le modèle.

    Returns:
        Le texte, tirets de ponctuation convertis.
    """
    return _TIRET_COLLE.sub("-", _TIRET_PONCTUATION.sub(", ", texte))
