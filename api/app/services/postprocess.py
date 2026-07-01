"""Post-traitement de la réponse du modèle : sources citées et confiance."""

from __future__ import annotations

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
