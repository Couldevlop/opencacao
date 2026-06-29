"""La source météo réelle (Open-Meteo) : géocodage puis prévisions, via httpx mocké."""

from __future__ import annotations

import httpx
import pytest

from app.services.outils.meteo_openmeteo import MeteoOpenMeteo


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _handler_ok(req: httpx.Request) -> httpx.Response:
    if "search" in req.url.path:
        return httpx.Response(
            200, json={"results": [{"latitude": 6.87, "longitude": -6.45, "name": "Daloa"}]}
        )
    return httpx.Response(200, json={"daily": {"precipitation_sum": [12.5]}})


@pytest.mark.asyncio
async def test_previsions_retourne_resume_et_pluie() -> None:
    meteo = MeteoOpenMeteo(client=_client(_handler_ok))
    previsions = await meteo.previsions("Daloa")
    assert previsions["pluie_mm_24h"] == 12.5
    assert previsions["resume"]  # résumé non vide


@pytest.mark.asyncio
async def test_previsions_localite_inconnue_retourne_vide() -> None:
    # Géocodage sans résultat → pas de prévision inventée.
    meteo = MeteoOpenMeteo(client=_client(lambda r: httpx.Response(200, json={"results": []})))
    assert await meteo.previsions("Atlantide") == {}


@pytest.mark.asyncio
async def test_previsions_localite_vide_retourne_vide() -> None:
    # On n'interroge même pas l'API pour une localité vide.
    def _interdit(req: httpx.Request) -> httpx.Response:
        raise AssertionError("aucun appel réseau attendu pour une localité vide")

    meteo = MeteoOpenMeteo(client=_client(_interdit))
    assert await meteo.previsions("  ") == {}


@pytest.mark.asyncio
async def test_resume_reflete_l_intensite_de_pluie() -> None:
    def _sec(req: httpx.Request) -> httpx.Response:
        if "search" in req.url.path:
            return httpx.Response(200, json={"results": [{"latitude": 5.3, "longitude": -4.0}]})
        return httpx.Response(200, json={"daily": {"precipitation_sum": [0.0]}})

    meteo = MeteoOpenMeteo(client=_client(_sec))
    previsions = await meteo.previsions("Abidjan")
    assert previsions["pluie_mm_24h"] == 0.0
    assert "sec" in previsions["resume"].lower()
