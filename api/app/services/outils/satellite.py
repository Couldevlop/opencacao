"""Outil Satellite : alertes de déforestation autour d'une position.

Isole l'accès à la source satellitaire (port mockable). Aucun LLM tiers : une
source de données factuelles uniquement (souveraineté). En production, brancher
``SatelliteGfw`` (Global Forest Watch) ; en test, un double factice.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.core.logging import get_logger

logger = get_logger(__name__)


@runtime_checkable
class SatellitePort(Protocol):
    """Contrat d'une source d'alertes satellitaires de déforestation."""

    async def alertes(
        self, localite: str = "", lat: float | None = None, lon: float | None = None
    ) -> dict[str, object]:
        """Alertes autour d'un point (coordonnées prioritaires sur la localité)."""
        ...


class OutilSatellite:
    """Outil agent : enveloppe une source satellitaire derrière le contrat Outil."""

    nom = "satellite"

    def __init__(self, source: SatellitePort) -> None:
        """Initialise l'outil avec sa source d'alertes."""
        self._source = source

    async def invoquer(self, **kwargs: object) -> dict[str, object]:
        """Récupère les alertes pour la position passée en argument."""
        localite = str(kwargs.get("localite", ""))
        lat = kwargs.get("lat")
        lon = kwargs.get("lon")
        try:
            return await self._source.alertes(
                localite=localite,
                lat=float(lat) if lat is not None else None,  # type: ignore[arg-type]
                lon=float(lon) if lon is not None else None,  # type: ignore[arg-type]
            )
        except Exception:  # noqa: BLE001 — best-effort, l'agent dégrade proprement
            logger.warning("outil_satellite_echec", localite=localite)
            return {}
