"""Adaptateur GFW : requêtes d'alertes intégrées, redirection 307, géocodage."""

from __future__ import annotations

import httpx
import pytest

from app.services.outils.satellite_gfw import SatelliteGfw

_API = "https://gfw.test/dataset/gfw_integrated_alerts/latest/query/json"
_GEO = "https://geo.test/v1/search"


def _client(reponses: list[dict], geocode: dict | None = None) -> httpx.AsyncClient:
    """Client mocké : file de réponses pour l'API GFW + géocodage optionnel."""
    file_gfw = list(reponses)

    def repondre(requete: httpx.Request) -> httpx.Response:
        if requete.url.host == "geo.test":
            return httpx.Response(200, json=geocode or {"results": []})
        # Simule la redirection 307 de « latest » vers la version concrète.
        if "/latest/" in str(requete.url):
            cible = str(requete.url).replace("/latest/", "/v20260703/")
            return httpx.Response(307, headers={"location": cible})
        return httpx.Response(200, json=file_gfw.pop(0))

    return httpx.AsyncClient(transport=httpx.MockTransport(repondre))


def _corps(requete_json: list[dict]) -> SatelliteGfw:
    return SatelliteGfw(
        cle="cle-test", client=_client(requete_json), api_url=_API, geocoding_url=_GEO
    )


@pytest.mark.asyncio
async def test_alertes_par_coordonnees_avec_dates() -> None:
    gfw = _corps(
        [
            {"data": [{"count": 216}]},
            {"data": [{"gfw_integrated_alerts__date": "2026-06-18", "count": 18}]},
        ]
    )
    resultat = await gfw.alertes(lat=5.72, lon=-6.68)
    assert resultat["alertes_depuis_2021"] == 216
    assert resultat["dates_recentes"] == ["2026-06-18"]
    assert resultat["tampon_km"] == 1


@pytest.mark.asyncio
async def test_zero_alerte_ne_fait_pas_de_seconde_requete() -> None:
    gfw = _corps([{"data": [{"count": 0}]}])  # une seule réponse en file
    resultat = await gfw.alertes(lat=5.78, lon=-6.59)
    assert resultat == {"alertes_depuis_2021": 0, "dates_recentes": [], "tampon_km": 1}


@pytest.mark.asyncio
async def test_localite_geocodee_puis_interrogee() -> None:
    gfw = SatelliteGfw(
        cle="cle-test",
        client=_client(
            [{"data": [{"count": 0}]}],
            geocode={"results": [{"latitude": 5.78, "longitude": -6.59}]},
        ),
        api_url=_API,
        geocoding_url=_GEO,
    )
    resultat = await gfw.alertes(localite="Soubré")
    assert resultat["alertes_depuis_2021"] == 0


@pytest.mark.asyncio
async def test_localite_inconnue_renvoie_vide() -> None:
    gfw = _corps([])  # géocodage sans résultat, aucune requête GFW ne doit partir
    assert await gfw.alertes(localite="VilleInconnue") == {}


@pytest.mark.asyncio
async def test_sans_cle_renvoie_vide_sans_appel() -> None:
    gfw = SatelliteGfw(cle="", client=_client([]), api_url=_API, geocoding_url=_GEO)
    assert await gfw.alertes(lat=5.78, lon=-6.59) == {}
