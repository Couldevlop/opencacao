"""Port de cache des résultats d'outils (météo, alertes satellite).

Les données d'outils sont stables à l'échelle de leur TTL (la pluie sous 30 min
ne change pas de conseil, les alertes GFW sont hebdomadaires) et il n'y a que
~60 zones : mettre les résultats en cache économise 1 à 3 s d'appels HTTP sur
les questions répétées, et préserve le quota de la clé GFW. Implémenté par
``core.cache.CacheClient`` (fail-soft : Redis en panne = cache transparent).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class CacheOutilPort(Protocol):
    """Contrat minimal d'un cache de résultats d'outils."""

    async def get_outil(self, cle: str) -> str | None:
        """Retourne le résultat sérialisé en cache pour la clé, ou None."""
        ...

    async def set_outil(self, cle: str, payload: str, ttl_s: int) -> None:
        """Met en cache un résultat sérialisé, avec son TTL propre."""
        ...
