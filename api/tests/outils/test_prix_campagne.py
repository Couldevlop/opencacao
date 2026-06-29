"""La source prix « campagne » : valeur officielle configurée, jamais inventée."""

from __future__ import annotations

import pytest

from app.services.outils.prix_campagne import PrixCampagne


@pytest.mark.asyncio
async def test_cours_configure_retourne_le_prix_officiel() -> None:
    prix = PrixCampagne(prix_fcfa_kg=1500, campagne="2025-2026")
    cours = await prix.cours()
    assert cours["prix_bord_champ_fcfa_kg"] == 1500
    assert cours["campagne"] == "2025-2026"


@pytest.mark.asyncio
async def test_cours_non_configure_ne_fabrique_pas_de_prix() -> None:
    # Sécurité : tant que le prix officiel n'est pas renseigné, on ne renvoie RIEN
    # (l'agent dégrade vers le RAG plutôt que d'énoncer un prix fabriqué).
    assert await PrixCampagne(prix_fcfa_kg=0, campagne="").cours() == {}
