"""Outil Météo : récupère des prévisions pour une localité.

L'outil isole l'accès à la source météo (port mockable). Aucun LLM tiers : une
source de données factuelles uniquement (souveraineté). En production, brancher un
``MeteoPort`` httpx vers une API météo ; en test, un double factice.
"""

from __future__ import annotations

import json
from typing import Protocol, runtime_checkable

from app.core.logging import get_logger
from app.services.outils.cache_outils import CacheOutilPort

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

    def __init__(
        self, meteo: MeteoPort, cache: CacheOutilPort | None = None, ttl_s: int = 1800
    ) -> None:
        """Initialise l'outil avec sa source de prévisions.

        Args:
            meteo: Source de prévisions.
            cache: Cache de résultats optionnel (Redis en prod, fail-soft).
            ttl_s: Durée de vie d'un résultat en cache ; 0 = cache coupé.
        """
        self._meteo = meteo
        self._cache = cache if ttl_s > 0 else None
        self._ttl_s = ttl_s

    async def invoquer(self, **kwargs: object) -> dict[str, object]:
        """Récupère les prévisions pour la localité (cache d'abord, TTL court)."""
        localite = str(kwargs.get("localite", ""))
        cle = f"meteo:{localite.strip().lower()}" if localite.strip() else ""
        if self._cache and cle:
            brut = await self._cache.get_outil(cle)
            if brut:
                return json.loads(brut)
        try:
            previsions = await self._meteo.previsions(localite)
        except Exception:  # noqa: BLE001 — best-effort, l'agent dégrade proprement
            logger.warning("outil_meteo_echec", localite=localite)
            return {}
        # Un résultat VIDE (source en panne / localité inconnue) n'est jamais mis en
        # cache : un échec ne doit pas coller pendant tout le TTL.
        if self._cache and cle and previsions:
            await self._cache.set_outil(cle, json.dumps(previsions), self._ttl_s)
        return previsions
