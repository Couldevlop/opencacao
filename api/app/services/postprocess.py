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


def extraire_sources(reponse: str) -> list[str]:
    """Extrait les sources fiables citées dans le texte de la réponse.

    Args:
        reponse: Texte généré par le modèle.

    Returns:
        Liste, sans doublon et dans l'ordre du référentiel, des sources reconnues
        mentionnées dans la réponse.
    """
    texte = reponse.lower()
    return [source for source in _sources_connues() if source.lower() in texte]


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
