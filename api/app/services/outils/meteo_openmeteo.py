"""Source météo réelle : Open-Meteo (API publique libre, sans clé).

Open-Meteo est une source de DONNÉES factuelles (pas un LLM tiers) : compatible
souveraineté. Deux appels : géocodage de la localité (nom -> lat/lon) puis prévision
journalière de précipitations. Les erreurs réseau remontent et sont absorbées par
``OutilMeteo`` (fail-soft -> {}), donc l'agent dégrade proprement. Le client httpx
est injectable pour les tests (aucun appel réseau en CI).
"""

from __future__ import annotations

import httpx

_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


def _resume_pluie(pluie_mm: float) -> str:
    """Résumé textuel de l'intensité de pluie attendue sur 24 h."""
    if pluie_mm >= 10.0:
        return "fortes pluies attendues"
    if pluie_mm >= 2.0:
        return "pluie modérée possible"
    return "temps majoritairement sec"


class MeteoOpenMeteo:
    """Source de prévisions météo adossée à Open-Meteo (implémente MeteoPort)."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_s: float = 10.0,
        geocoding_url: str = _GEOCODING_URL,
        forecast_url: str = _FORECAST_URL,
    ) -> None:
        """Initialise la source.

        Args:
            client: Client httpx injectable (les tests fournissent un MockTransport).
            timeout_s: Délai maximal par requête si aucun client n'est fourni.
            geocoding_url: Endpoint de géocodage (nom de localité -> coordonnées).
            forecast_url: Endpoint de prévision journalière.
        """
        self._client = client
        self._timeout_s = timeout_s
        self._geocoding_url = geocoding_url
        self._forecast_url = forecast_url

    async def previsions(self, localite: str) -> dict[str, object]:
        """Retourne ``{"resume", "pluie_mm_24h"}`` pour la localité, ou ``{}``.

        Args:
            localite: Nom de la localité (ville/région cacaoyère).

        Returns:
            Les prévisions, ou un dictionnaire vide si la localité est vide/inconnue.
        """
        if not localite.strip():
            return {}

        # Client injecté (tests) : on le réutilise sans le fermer (cycle de vie externe).
        # Sinon : un client éphémère fermé à la sortie -> aucune fuite (orchestrateur
        # construit par requête).
        if self._client is not None:
            return await self._previsions(self._client, localite)
        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            return await self._previsions(client, localite)

    async def _previsions(self, client: httpx.AsyncClient, localite: str) -> dict[str, object]:
        """Effectue le géocodage puis la prévision via le client httpx fourni."""
        reponse_geo = await client.get(
            self._geocoding_url,
            params={"name": localite, "count": 1, "language": "fr", "format": "json"},
        )
        reponse_geo.raise_for_status()
        resultats = reponse_geo.json().get("results") or []
        if not resultats:
            return {}
        lieu = resultats[0]

        reponse_prev = await client.get(
            self._forecast_url,
            params={
                "latitude": lieu["latitude"],
                "longitude": lieu["longitude"],
                "daily": "precipitation_sum",
                "forecast_days": 1,
                "timezone": "auto",
            },
        )
        reponse_prev.raise_for_status()
        sommes = reponse_prev.json().get("daily", {}).get("precipitation_sum") or []
        if not sommes:
            return {}
        pluie = float(sommes[0] or 0.0)
        return {"resume": _resume_pluie(pluie), "pluie_mm_24h": round(pluie, 1)}
