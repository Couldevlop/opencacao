"""Agent Satellite : alertes de déforestation GFW autour de la position (tool use)."""

from __future__ import annotations

import pytest

from app.services.outils.indisponible import SatelliteIndisponible
from app.services.outils.satellite import OutilSatellite


class _SourceFactice:
    def __init__(self, resultat: dict | None = None, erreur: bool = False) -> None:
        self._resultat = resultat or {}
        self._erreur = erreur
        self.appels: list[dict] = []

    async def alertes(self, localite="", lat=None, lon=None) -> dict:
        self.appels.append({"localite": localite, "lat": lat, "lon": lon})
        if self._erreur:
            raise RuntimeError("api en panne")
        return self._resultat


@pytest.mark.asyncio
async def test_outil_satellite_transmet_les_arguments() -> None:
    source = _SourceFactice({"alertes_depuis_2021": 3})
    outil = OutilSatellite(source)
    resultat = await outil.invoquer(lat=5.78, lon=-6.59)
    assert resultat == {"alertes_depuis_2021": 3}
    assert source.appels == [{"localite": "", "lat": 5.78, "lon": -6.59}]


@pytest.mark.asyncio
async def test_outil_satellite_fail_soft_sur_exception() -> None:
    outil = OutilSatellite(_SourceFactice(erreur=True))
    assert await outil.invoquer(localite="Soubré") == {}


@pytest.mark.asyncio
async def test_source_indisponible_renvoie_vide() -> None:
    assert await SatelliteIndisponible().alertes(localite="Soubré") == {}
