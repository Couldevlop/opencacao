"""Source prix « campagne » : prix bord-champ officiel, configuré (jamais inventé).

Il n'existe pas d'API libre fiable pour le prix bord-champ ivoirien : c'est une
valeur ADMINISTRÉE, fixée par le Conseil du Café-Cacao pour chaque campagne. La
source réelle honnête est donc cette valeur officielle, renseignée en configuration
et mise à jour à chaque campagne. Tant qu'elle n'est pas renseignée, la source ne
renvoie RIEN — l'agent dégrade vers le RAG plutôt que d'énoncer un prix fabriqué.
"""

from __future__ import annotations


class PrixCampagne:
    """Source de prix adossée au prix bord-champ officiel de la campagne (PrixPort)."""

    def __init__(self, prix_fcfa_kg: int, campagne: str) -> None:
        """Initialise la source.

        Args:
            prix_fcfa_kg: Prix bord-champ garanti (FCFA/kg). 0 = non renseigné.
            campagne: Libellé de la campagne (ex. « 2025-2026 »).
        """
        self._prix_fcfa_kg = prix_fcfa_kg
        self._campagne = campagne

    async def cours(self) -> dict[str, object]:
        """Retourne le prix officiel configuré, ou ``{}`` s'il n'est pas renseigné."""
        if not self._prix_fcfa_kg:
            return {}
        return {
            "prix_bord_champ_fcfa_kg": self._prix_fcfa_kg,
            "campagne": self._campagne,
        }
