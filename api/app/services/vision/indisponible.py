"""Source de vision neutre — profil matériel CPU, ou VLM non configuré.

Même parti pris que ``services/outils/indisponible.py`` pour la météo et les prix :
une source absente **rend une valeur neutre**, jamais une erreur brute, et surtout
jamais une donnée inventée. Le pattern « contexte vide → fabrication » a déjà coûté
un correctif sur les agents (v0.6.48) ; il ne revient pas par la vision.
"""

from __future__ import annotations

CONSIGNE_INDISPONIBLE = (
    "L'analyse d'image n'est pas disponible en ce moment. Vos photos sont bien "
    "enregistrées et rattachées à votre parcelle. Pour un avis sur ce que vous "
    "observez, montrez-les à votre agent ANADER."
)


class VisionIndisponible:
    """Source de vision neutre : ne décrit rien, et le dit."""

    async def decrire(self, images: tuple[bytes, ...], consigne: str) -> str | None:
        """Retourne ``None`` — aucune description, jamais une invention."""
        return None

    async def disponible(self) -> bool:
        """Indique que la vision est indisponible."""
        return False
