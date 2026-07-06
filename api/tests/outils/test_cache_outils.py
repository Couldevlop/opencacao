"""Cache Redis des résultats d'outils : hit/miss/vide, TTL propre, clés stables."""

from __future__ import annotations

import json

import pytest

from app.services.outils.meteo import OutilMeteo
from app.services.outils.satellite import OutilSatellite


class _CacheFactice:
    def __init__(self, seed: dict[str, str] | None = None) -> None:
        self.donnees = dict(seed or {})
        self.ecrits: list[tuple[str, str, int]] = []

    async def get_outil(self, cle: str) -> str | None:
        return self.donnees.get(cle)

    async def set_outil(self, cle: str, payload: str, ttl_s: int) -> None:
        self.donnees[cle] = payload
        self.ecrits.append((cle, payload, ttl_s))


class _MeteoFactice:
    def __init__(self, resultat: dict | None = None) -> None:
        self._resultat = resultat if resultat is not None else {}
        self.appels = 0

    async def previsions(self, localite: str) -> dict:
        self.appels += 1
        return self._resultat


class _SatelliteFactice:
    def __init__(self, resultat: dict | None = None) -> None:
        self._resultat = resultat if resultat is not None else {}
        self.appels = 0

    async def alertes(self, localite="", lat=None, lon=None) -> dict:
        self.appels += 1
        return self._resultat


@pytest.mark.asyncio
async def test_meteo_hit_sert_le_cache_sans_appeler_la_source() -> None:
    cache = _CacheFactice({"meteo:daloa": json.dumps({"resume": "sec", "pluie_mm_24h": 0.0})})
    source = _MeteoFactice()
    outil = OutilMeteo(source, cache=cache, ttl_s=1800)
    assert (await outil.invoquer(localite="Daloa"))["resume"] == "sec"
    assert source.appels == 0


@pytest.mark.asyncio
async def test_meteo_miss_appelle_puis_stocke_avec_ttl() -> None:
    cache = _CacheFactice()
    source = _MeteoFactice({"resume": "pluie", "pluie_mm_24h": 7.0})
    outil = OutilMeteo(source, cache=cache, ttl_s=1800)
    assert (await outil.invoquer(localite="Daloa"))["resume"] == "pluie"
    assert source.appels == 1
    assert cache.ecrits and cache.ecrits[0][0] == "meteo:daloa" and cache.ecrits[0][2] == 1800


@pytest.mark.asyncio
async def test_meteo_resultat_vide_jamais_mis_en_cache() -> None:
    # Un échec de source ({}) ne doit pas coller 30 minutes.
    cache = _CacheFactice()
    outil = OutilMeteo(_MeteoFactice({}), cache=cache, ttl_s=1800)
    assert await outil.invoquer(localite="Daloa") == {}
    assert cache.ecrits == []


@pytest.mark.asyncio
async def test_meteo_ttl_zero_court_circuite_le_cache() -> None:
    cache = _CacheFactice({"meteo:daloa": json.dumps({"resume": "périmé"})})
    source = _MeteoFactice({"resume": "frais", "pluie_mm_24h": 1.0})
    outil = OutilMeteo(source, cache=cache, ttl_s=0)
    assert (await outil.invoquer(localite="Daloa"))["resume"] == "frais"
    assert cache.ecrits == []


@pytest.mark.asyncio
async def test_satellite_cle_par_coordonnees_arrondies() -> None:
    cache = _CacheFactice()
    resultat = {"alertes_depuis_2021": 3, "dates_recentes": [], "tampon_km": 1}
    outil = OutilSatellite(_SatelliteFactice(resultat), cache=cache, ttl_s=86400)
    await outil.invoquer(lat=5.78349, lon=-6.59321)
    assert cache.ecrits[0][0] == "satellite:5.783,-6.593"
    assert cache.ecrits[0][2] == 86400


@pytest.mark.asyncio
async def test_satellite_hit_par_localite() -> None:
    paquet = json.dumps({"alertes_depuis_2021": 0, "dates_recentes": [], "tampon_km": 1})
    cache = _CacheFactice({"satellite:soubré": paquet})
    source = _SatelliteFactice()
    outil = OutilSatellite(source, cache=cache, ttl_s=86400)
    assert (await outil.invoquer(localite="Soubré"))["alertes_depuis_2021"] == 0
    assert source.appels == 0
