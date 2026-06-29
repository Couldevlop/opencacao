"""Outil Météo : récupère des prévisions pour une localité.

L'outil isole l'accès à la source météo (port mockable). Aucun LLM tiers : une
source de données factuelles uniquement (souveraineté). En production, brancher un
``MeteoPort`` httpx vers une API météo ; en test, un double factice.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.core.logging import get_logger

logger = get_logger(__name__)


@runtime_checkable
class MeteoPort(Protocol):
    """Contrat d'une source de prévisions météo."""

    async def previsions(self, localite: str) -> dict[str, object]:
        """Retourne les prévisions pour une localité (résumé + indicateurs)."""
        ...


class OutilMeteo:
    """Outil agent : enveloppe une source météo derrière le contrat Outil."""

    nom = "meteo"

    def __init__(self, meteo: MeteoPort) -> None:
        """Initialise l'outil avec sa source de prévisions."""
        self._meteo = meteo

    async def invoquer(self, **kwargs: object) -> dict[str, object]:
        """Récupère les prévisions pour la localité passée en argument."""
        localite = str(kwargs.get("localite", ""))
        try:
            return await self._meteo.previsions(localite)
        except Exception:  # noqa: BLE001 — best-effort, l'agent dégrade proprement
            logger.warning("outil_meteo_echec", localite=localite)
            return {}
