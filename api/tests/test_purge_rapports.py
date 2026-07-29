"""Tests de la purge périodique des rapports (rétention)."""

from __future__ import annotations

import asyncio

from app.core.config import Settings
from app.main import _lancer_purge_rapports


class _AppFactice:
    """Application réduite à ce que la purge consomme."""

    def __init__(self, depot: object) -> None:
        self.state = type("Etat", (), {"rapports": depot})()


class _DepotCompteur:
    def __init__(self) -> None:
        self.appels = 0

    async def purger_anciens(self, jours: int) -> int:
        self.appels += 1
        return 3


class _DepotCasse:
    async def purger_anciens(self, jours: int) -> int:
        raise RuntimeError("disque plein")


def test_une_retention_nulle_ne_lance_aucune_tache():
    """0 = conservation indefinie, comme pour les sessions."""
    settings = Settings(_env_file=None, rapports_retention_jours=0)
    assert _lancer_purge_rapports(_AppFactice(_DepotCompteur()), settings) is None


async def test_la_purge_s_execute_au_demarrage():
    depot = _DepotCompteur()
    settings = Settings(_env_file=None, rapports_retention_jours=30)
    tache = _lancer_purge_rapports(_AppFactice(depot), settings)
    await asyncio.sleep(0)  # laisse la boucle atteindre son premier tour
    tache.cancel()
    assert depot.appels == 1


async def test_une_purge_qui_echoue_ne_tue_pas_l_application():
    """Un disque plein ne doit pas emporter le service avec lui."""
    settings = Settings(_env_file=None, rapports_retention_jours=30)
    tache = _lancer_purge_rapports(_AppFactice(_DepotCasse()), settings)
    await asyncio.sleep(0)
    assert not tache.done() or tache.cancelled()
    tache.cancel()
