"""Adaptateurs d'outils « indisponibles » : renvoient un résultat vide.

Permettent d'enregistrer les agents Météo/Prix dans le socle sans dépendance
externe. L'agent dégrade alors proprement en conseil générique. À remplacer par
des adaptateurs httpx réels (tâche de données ultérieure).
"""

from __future__ import annotations


class MeteoIndisponible:
    """Source météo neutre (aucune donnée)."""

    async def previsions(self, localite: str) -> dict[str, object]:
        """Retourne un dictionnaire vide (pas de prévisions)."""
        return {}


class PrixIndisponible:
    """Source prix neutre (aucune donnée)."""

    async def cours(self) -> dict[str, object]:
        """Retourne un dictionnaire vide (pas de cours)."""
        return {}
