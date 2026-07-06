"""Outil Satellite : alertes de déforestation autour d'une position.

Isole l'accès à la source satellitaire (port mockable). Aucun LLM tiers : une
source de données factuelles uniquement (souveraineté). En production, brancher
``SatelliteGfw`` (Global Forest Watch) ; en test, un double factice.
"""

from __future__ import annotations

import json
from typing import Protocol, runtime_checkable

from app.core.logging import get_logger
from app.services.outils.cache_outils import CacheOutilPort

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

    def __init__(
        self, source: SatellitePort, cache: CacheOutilPort | None = None, ttl_s: int = 86_400
    ) -> None:
        """Initialise l'outil avec sa source d'alertes.

        Args:
            source: Source d'alertes satellitaires.
            cache: Cache de résultats optionnel (les alertes GFW sont hebdomadaires,
                un TTL d'un jour économise l'appel ET le quota de la clé).
            ttl_s: Durée de vie d'un résultat en cache ; 0 = cache coupé.
        """
        self._source = source
        self._cache = cache if ttl_s > 0 else None
        self._ttl_s = ttl_s

    async def invoquer(self, **kwargs: object) -> dict[str, object]:
        """Récupère les alertes pour la position (cache d'abord, TTL d'un jour)."""
        localite = str(kwargs.get("localite", ""))
        lat = kwargs.get("lat")
        lon = kwargs.get("lon")
        cle = _cle_cache(localite, lat, lon)
        if self._cache and cle:
            brut = await self._cache.get_outil(cle)
            if brut:
                return json.loads(brut)
        try:
            resultat = await self._source.alertes(
                localite=localite,
                lat=float(lat) if lat is not None else None,  # type: ignore[arg-type]
                lon=float(lon) if lon is not None else None,  # type: ignore[arg-type]
            )
        except Exception:  # noqa: BLE001 — best-effort, l'agent dégrade proprement
            logger.warning("outil_satellite_echec", localite=localite)
            return {}
        # Un résultat VIDE (clé absente / API en panne) n'est jamais mis en cache.
        if self._cache and cle and resultat:
            await self._cache.set_outil(cle, json.dumps(resultat), self._ttl_s)
        return resultat


def _cle_cache(localite: str, lat: object, lon: object) -> str:
    """Clé de cache : coordonnées arrondies (~100 m) prioritaires, sinon localité."""
    if lat is not None and lon is not None:
        return f"satellite:{round(float(lat), 3)},{round(float(lon), 3)}"  # type: ignore[arg-type]
    if localite.strip():
        return f"satellite:{localite.strip().lower()}"
    return ""
