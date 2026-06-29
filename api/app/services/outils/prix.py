"""Outil Prix : récupère le cours/prix de référence du cacao.

Source de données factuelle (CCC, marché), jamais un LLM tiers. Port mockable.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.core.logging import get_logger

logger = get_logger(__name__)


@runtime_checkable
class PrixPort(Protocol):
    """Contrat d'une source de prix/marché du cacao."""

    async def cours(self) -> dict[str, object]:
        """Retourne le cours courant (prix bord-champ, campagne, change…)."""
        ...


class OutilPrix:
    """Outil agent : enveloppe une source de prix derrière le contrat Outil."""

    nom = "prix"

    def __init__(self, prix: PrixPort) -> None:
        """Initialise l'outil avec sa source de prix."""
        self._prix = prix

    async def invoquer(self, **kwargs: object) -> dict[str, object]:
        """Récupère le cours courant (best-effort : {} si la source échoue)."""
        try:
            return await self._prix.cours()
        except Exception:  # noqa: BLE001
            logger.warning("outil_prix_echec")
            return {}
