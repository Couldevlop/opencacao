"""Source satellitaire réelle : Global Forest Watch Data API (alertes intégrées).

GFW est une source de DONNÉES factuelles (pas un LLM tiers) : compatible
souveraineté. Les alertes intégrées (GLAD+RADD, dataset ``gfw_integrated_alerts``)
sont interrogées sur un polygone tampon (~1 km) autour du point, depuis 2021
(période pertinente post-cutoff EUDR du 31/12/2020).

Particularités d'API (validées en prod le 05/07/2026) : l'endpoint ``latest``
répond 307 (suivre la redirection, POST préservé) ; ``MAX(date)`` nu plante côté
tuiles (on filtre par date puis GROUP BY) ; les alias SQL sont ignorés (champ
``count``). Clé requise en en-tête ``x-api-key`` (expire après un an).
"""

from __future__ import annotations

import datetime as dt

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

_API_URL = "https://data-api.globalforestwatch.org/dataset/gfw_integrated_alerts/latest/query/json"
_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
_TAMPON_DEG = 0.009  # ≈ 1 km
_DEPUIS = "2021-01-01"  # cutoff EUDR : 31/12/2020
_MAX_DATES = 3


def _polygone(lat: float, lon: float) -> dict[str, object]:
    """Polygone GeoJSON carré (~2 km de côté) centré sur le point."""
    d = _TAMPON_DEG
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [lon - d, lat - d],
                [lon + d, lat - d],
                [lon + d, lat + d],
                [lon - d, lat + d],
                [lon - d, lat - d],
            ]
        ],
    }


class SatelliteGfw:
    """Source d'alertes de déforestation adossée à GFW (implémente SatellitePort)."""

    def __init__(
        self,
        cle: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_s: float = 15.0,
        api_url: str = _API_URL,
        geocoding_url: str = _GEOCODING_URL,
    ) -> None:
        """Initialise la source.

        Args:
            cle: Clé GFW Data API (en-tête ``x-api-key``). Vide = source inactive.
            client: Client httpx injectable (les tests fournissent un MockTransport).
            timeout_s: Délai maximal par requête si aucun client n'est fourni.
            api_url: Endpoint de requête du dataset d'alertes intégrées.
            geocoding_url: Endpoint de géocodage (nom de localité -> coordonnées).
        """
        self._cle = cle.strip()
        self._client = client
        self._timeout_s = timeout_s
        self._api_url = api_url
        self._geocoding_url = geocoding_url

    async def alertes(
        self, localite: str = "", lat: float | None = None, lon: float | None = None
    ) -> dict[str, object]:
        """Alertes de déforestation depuis 2021 autour du point, ou ``{}``.

        Les coordonnées priment sur la localité nommée (plus précises). Sans clé
        configurée, retourne ``{}`` sans appel réseau : l'agent donnera une consigne
        d'indisponibilité, jamais un statut inventé.
        """
        if not self._cle:
            return {}
        if self._client is not None:
            return await self._alertes(self._client, localite, lat, lon)
        async with httpx.AsyncClient(timeout=self._timeout_s, follow_redirects=True) as client:
            return await self._alertes(client, localite, lat, lon)

    async def _alertes(
        self, client: httpx.AsyncClient, localite: str, lat: float | None, lon: float | None
    ) -> dict[str, object]:
        """Géocode si nécessaire puis interroge le dataset d'alertes."""
        if lat is None or lon is None:
            if not localite.strip():
                return {}
            point = await self._geocoder(client, localite)
            if point is None:
                return {}
            lat, lon = point

        geometrie = _polygone(lat, lon)
        total = await self._compter(client, geometrie)
        if total is None:
            return {}
        dates: list[str] = []
        if total > 0:
            dates = await self._dates_recentes(client, geometrie)
        return {"alertes_depuis_2021": total, "dates_recentes": dates, "tampon_km": 1}

    async def _geocoder(
        self, client: httpx.AsyncClient, localite: str
    ) -> tuple[float, float] | None:
        """Nom de localité -> (lat, lon), ou None si inconnue (même source que la météo)."""
        reponse = await client.get(
            self._geocoding_url,
            params={"name": localite, "count": 1, "language": "fr", "format": "json"},
        )
        reponse.raise_for_status()
        resultats = reponse.json().get("results") or []
        if not resultats:
            return None
        return float(resultats[0]["latitude"]), float(resultats[0]["longitude"])

    async def _requeter(
        self, client: httpx.AsyncClient, sql: str, geometrie: dict[str, object]
    ) -> list[dict[str, object]]:
        """POST la requête SQL+géométrie et retourne les lignes de résultat."""
        reponse = await client.post(
            self._api_url,
            headers={"x-api-key": self._cle},
            json={"sql": sql, "geometry": geometrie},
            follow_redirects=True,  # « latest » répond 307 vers la version concrète
        )
        reponse.raise_for_status()
        return reponse.json().get("data") or []

    async def _compter(self, client: httpx.AsyncClient, geometrie: dict[str, object]) -> int | None:
        """Nombre d'alertes depuis 2021, ou None si la réponse est inexploitable."""
        lignes = await self._requeter(
            client,
            f"SELECT COUNT(*) FROM results WHERE gfw_integrated_alerts__date >= '{_DEPUIS}'",
            geometrie,
        )
        if not lignes or "count" not in lignes[0]:
            return None
        return int(lignes[0]["count"])  # type: ignore[arg-type]

    async def _dates_recentes(
        self, client: httpx.AsyncClient, geometrie: dict[str, object]
    ) -> list[str]:
        """Dernières dates d'alerte (GROUP BY — MAX() nu plante côté GFW)."""
        depuis = f"{dt.date.today().year - 1}-01-01"
        lignes = await self._requeter(
            client,
            "SELECT gfw_integrated_alerts__date, COUNT(*) FROM results "
            f"WHERE gfw_integrated_alerts__date >= '{depuis}' "
            "GROUP BY gfw_integrated_alerts__date",
            geometrie,
        )
        dates = sorted(str(ligne.get("gfw_integrated_alerts__date", "")) for ligne in lignes)
        return [d for d in dates if d][-_MAX_DATES:]
