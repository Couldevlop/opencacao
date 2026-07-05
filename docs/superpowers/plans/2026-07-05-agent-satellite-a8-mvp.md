# Agent Satellite (A8) MVP — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal :** Agent Satellite qui interroge les alertes de déforestation GFW (`gfw_integrated_alerts`) autour de la position du producteur et répond avec des faits datés croisés au RAG EUDR — sans jamais certifier une conformité.

**Architecture :** Moule Open-Meteo à l'identique : `SatellitePort` (Protocol mockable) + `OutilSatellite` (fail-soft `{}`) + adaptateur réel `SatelliteGfw` (httpx, suivi 307, clé `x-api-key`) + `AgentSatellite` (AgentBase, recette 4 étapes). Frontière de routage : `déforestation`/`géolocalisation` migrent de Réglementation vers Satellite.

**Tech Stack :** Python 3.11+, httpx (MockTransport en test), pytest + pytest-asyncio, ruff, structlog. Aucune nouvelle dépendance.

## Global Constraints

- Cacao uniquement ; garde-fous DANS l'orchestrateur, jamais par agent.
- Aucun LLM tiers : GFW = source de DONNÉES factuelles ; port mockable, aucun appel réseau en CI.
- L'agent CONSTATE des alertes, il ne certifie JAMAIS « conforme/non conforme EUDR ».
- Jamais de statut inventé : sans clé/donnée → consigne explicite (pattern prix/météo).
- `from __future__ import annotations`, docstrings Google, structlog (jamais print), ruff format+check.
- TDD strict : test rouge → code minimal → vert → commit. Couverture ≥ 97 % maintenue.
- Spec de référence : `docs/superpowers/specs/2026-07-05-agent-satellite-a8-mvp-design.md`.

---

### Task 1 : Port + OutilSatellite + source indisponible

**Files:**
- Create: `api/app/services/outils/satellite.py`
- Modify: `api/app/services/outils/indisponible.py` (ajouter `SatelliteIndisponible`)
- Test: `api/tests/agents/test_agent_satellite.py` (créer, section outil)

**Interfaces:**
- Produces : `SatellitePort` (Protocol) : `async alertes(localite: str = "", lat: float | None = None, lon: float | None = None) -> dict[str, object]` ; `OutilSatellite(source: SatellitePort)` : `nom = "satellite"`, `async invoquer(**kwargs) -> dict` (fail-soft `{}`) ; `SatelliteIndisponible` : `alertes(...) -> {}`.

- [ ] **Step 1 : Test rouge**

```python
# api/tests/agents/test_agent_satellite.py
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
```

- [ ] **Step 2 : Vérifier l'échec** — `cd api && python -m pytest tests/agents/test_agent_satellite.py -q --no-cov` → FAIL `ModuleNotFoundError: app.services.outils.satellite`.

- [ ] **Step 3 : Code minimal**

```python
# api/app/services/outils/satellite.py
"""Outil Satellite : alertes de déforestation autour d'une position.

Isole l'accès à la source satellitaire (port mockable). Aucun LLM tiers : une
source de données factuelles uniquement (souveraineté). En production, brancher
``SatelliteGfw`` (Global Forest Watch) ; en test, un double factice.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.core.logging import get_logger

logger = get_logger(__name__)


@runtime_checkable
class SatellitePort(Protocol):
    """Contrat d'une source d'alertes satellitaires de déforestation."""

    async def alertes(
        self, localite: str = "", lat: float | None = None, lon: float | None = None
    ) -> dict[str, object]:
        """Alertes autour d'un point (coordonnées prioritaires sur la localité)."""
        ...


class OutilSatellite:
    """Outil agent : enveloppe une source satellitaire derrière le contrat Outil."""

    nom = "satellite"

    def __init__(self, source: SatellitePort) -> None:
        """Initialise l'outil avec sa source d'alertes."""
        self._source = source

    async def invoquer(self, **kwargs: object) -> dict[str, object]:
        """Récupère les alertes pour la position passée en argument."""
        localite = str(kwargs.get("localite", ""))
        lat = kwargs.get("lat")
        lon = kwargs.get("lon")
        try:
            return await self._source.alertes(
                localite=localite,
                lat=float(lat) if lat is not None else None,  # type: ignore[arg-type]
                lon=float(lon) if lon is not None else None,  # type: ignore[arg-type]
            )
        except Exception:  # noqa: BLE001 — best-effort, l'agent dégrade proprement
            logger.warning("outil_satellite_echec", localite=localite)
            return {}
```

Dans `api/app/services/outils/indisponible.py`, ajouter à la fin (et mentionner Satellite dans le docstring de module) :

```python
class SatelliteIndisponible:
    """Source satellitaire neutre (aucune donnée — clé GFW non configurée)."""

    async def alertes(
        self, localite: str = "", lat: float | None = None, lon: float | None = None
    ) -> dict[str, object]:
        """Retourne un dictionnaire vide (pas d'alertes disponibles)."""
        return {}
```

- [ ] **Step 4 : Vérifier le vert** — même commande → 3 PASS.
- [ ] **Step 5 : Lint + commit**

```bash
python -m ruff format app/services/outils/ tests/agents/test_agent_satellite.py && python -m ruff check app/services/outils/ tests/agents/test_agent_satellite.py
git add api/app/services/outils/satellite.py api/app/services/outils/indisponible.py api/tests/agents/test_agent_satellite.py
git commit -m "feat(agents): outil Satellite (port mockable + fail-soft + source indisponible)"
```

---

### Task 2 : Adaptateur réel SatelliteGfw

**Files:**
- Create: `api/app/services/outils/satellite_gfw.py`
- Test: `api/tests/agents/test_satellite_gfw.py`

**Interfaces:**
- Consumes : `SatellitePort` (Task 1 — l'adaptateur l'implémente structurellement).
- Produces : `SatelliteGfw(cle: str, *, client: httpx.AsyncClient | None = None, timeout_s: float = 15.0, api_url: str = _API_URL, geocoding_url: str = _GEOCODING_URL)` avec `async alertes(localite="", lat=None, lon=None) -> dict` retournant `{"alertes_depuis_2021": int, "dates_recentes": list[str], "tampon_km": 1}` ou `{}`.

**Particularités GFW validées en prod (05/07/2026)** : l'endpoint `latest` répond 307 → `follow_redirects=True` obligatoire (POST préservé) ; `MAX(date)` nu plante côté tuiles → `COUNT(*)` filtré puis `GROUP BY date` ; les alias SQL sont ignorés (champ `count`) ; clé dans l'en-tête `x-api-key`.

- [ ] **Step 1 : Test rouge**

```python
# api/tests/agents/test_satellite_gfw.py
"""Adaptateur GFW : requêtes d'alertes intégrées, redirection 307, géocodage."""

from __future__ import annotations

import json

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
    gfw = _corps([
        {"data": [{"count": 216}]},
        {"data": [{"gfw_integrated_alerts__date": "2026-06-18", "count": 18}]},
    ])
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
```

- [ ] **Step 2 : Vérifier l'échec** — `python -m pytest tests/agents/test_satellite_gfw.py -q --no-cov` → FAIL `ModuleNotFoundError`.

- [ ] **Step 3 : Code minimal**

```python
# api/app/services/outils/satellite_gfw.py
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

    async def _compter(
        self, client: httpx.AsyncClient, geometrie: dict[str, object]
    ) -> int | None:
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
```

**Note `dt.date.today()`** : borne glissante « année courante-1 » pour les dates récentes — pas de gel d'horloge nécessaire en test (les réponses sont mockées, l'assertion porte sur le SQL exécuté seulement via le résultat).

- [ ] **Step 4 : Vérifier le vert** — `python -m pytest tests/agents/test_satellite_gfw.py -q --no-cov` → 5 PASS.
- [ ] **Step 5 : Lint + commit**

```bash
python -m ruff format app/services/outils/satellite_gfw.py tests/agents/test_satellite_gfw.py && python -m ruff check app/services/outils/satellite_gfw.py tests/agents/test_satellite_gfw.py
git add api/app/services/outils/satellite_gfw.py api/tests/agents/test_satellite_gfw.py
git commit -m "feat(agents): adaptateur GFW réel (alertes intégrées, 307, géocodage)"
```

---

### Task 3 : AgentSatellite

**Files:**
- Create: `api/app/services/agents/agent_satellite.py`
- Test: `api/tests/agents/test_agent_satellite.py` (compléter, section agent)

**Interfaces:**
- Consumes : `AgentBase`/`compter_mots_cles` (`services/agents/base.py`), `OutilSatellite` (Task 1), `localites.detecter` (`services/localites.py`), `RagRecuperateur` (`services/rag.py`).
- Produces : `AgentSatellite(inference, outil: OutilSatellite, rag: RagRecuperateur | None = None)`, `nom = "satellite"`.

- [ ] **Step 1 : Tests rouges (ajouter à `test_agent_satellite.py`)**

```python
from app.domain.agents import AgentRequete
from app.models.domain import Langue
from app.services.agents.agent_satellite import AgentSatellite


class _InferenceFactice:
    def __init__(self) -> None:
        self.contexte_recu: str | None = None

    async def generer(self, question, *, contexte=None, historique=None, **kw) -> str:
        self.contexte_recu = contexte
        return "Réponse satellite."

    def generer_stream(self, *a, **k): ...

    async def ready(self) -> bool:
        return True


def _requete(q: str, historique: list[dict[str, str]] | None = None) -> AgentRequete:
    return AgentRequete(q, Langue.FR, q, "ip", historique or [])


def _agent(resultat: dict | None = None) -> tuple[AgentSatellite, _InferenceFactice]:
    inf = _InferenceFactice()
    return AgentSatellite(inf, OutilSatellite(_SourceFactice(resultat))), inf


@pytest.mark.asyncio
async def test_score_eleve_sur_question_deforestation() -> None:
    agent, _ = _agent()
    assert await agent.peut_traiter(_requete("y a-t-il de la déforestation près de ma parcelle ?")) >= 0.7
    assert await agent.peut_traiter(_requete("quel est le prix du cacao ?")) == 0.0


@pytest.mark.asyncio
async def test_gps_dans_le_fil_prioritaire_sur_la_localite() -> None:
    agent, inf = _agent({"alertes_depuis_2021": 0, "dates_recentes": [], "tampon_km": 1})
    await agent.traiter(_requete("déforestation autour de 5.72, -6.68 près de Soubré ?"))
    source = agent._outil._source  # type: ignore[attr-defined]
    assert source.appels[0]["lat"] == pytest.approx(5.72)
    assert source.appels[0]["lon"] == pytest.approx(-6.68)


@pytest.mark.asyncio
async def test_localite_seule_transmise_a_l_outil() -> None:
    agent, inf = _agent({"alertes_depuis_2021": 0, "dates_recentes": [], "tampon_km": 1})
    await agent.traiter(_requete("des alertes de déforestation à Soubré ?"))
    source = agent._outil._source  # type: ignore[attr-defined]
    assert source.appels[0]["localite"] == "Soubré"
    assert source.appels[0]["lat"] is None


@pytest.mark.asyncio
async def test_sans_localisation_demande_la_position() -> None:
    agent, inf = _agent()
    await agent.traiter(_requete("y a-t-il de la déforestation vers chez moi ?"))
    assert "position" in (inf.contexte_recu or "").lower()
    assert "n'avance aucun" in (inf.contexte_recu or "").lower()


@pytest.mark.asyncio
async def test_zero_alerte_reste_prudent() -> None:
    agent, inf = _agent({"alertes_depuis_2021": 0, "dates_recentes": [], "tampon_km": 1})
    await agent.traiter(_requete("déforestation à Soubré ?"))
    contexte = inf.contexte_recu or ""
    assert "0 alerte" in contexte or "aucune alerte" in contexte.lower()
    assert "jamais" in contexte.lower()  # interdiction de certifier la conformité


@pytest.mark.asyncio
async def test_alertes_detectees_injectees_avec_dates() -> None:
    agent, inf = _agent({"alertes_depuis_2021": 216, "dates_recentes": ["2026-06-18"], "tampon_km": 1})
    await agent.traiter(_requete("déforestation à Soubré ?"))
    contexte = inf.contexte_recu or ""
    assert "216" in contexte and "2026-06-18" in contexte


@pytest.mark.asyncio
async def test_source_indisponible_consigne_explicite() -> None:
    agent, inf = _agent({})  # outil renvoie {}
    await agent.traiter(_requete("déforestation à Soubré ?"))
    assert "indisponible" in (inf.contexte_recu or "").lower()
```

- [ ] **Step 2 : Vérifier l'échec** — `python -m pytest tests/agents/test_agent_satellite.py -q --no-cov` → FAIL `ImportError: AgentSatellite`.

- [ ] **Step 3 : Code minimal**

```python
# api/app/services/agents/agent_satellite.py
"""Agent Satellite : alertes de déforestation autour de la position (contexte EUDR).

Tool use : récupère les alertes intégrées GFW via OutilSatellite et les injecte
comme faits datés dans le prompt. L'agent CONSTATE des alertes, il ne certifie
JAMAIS une conformité EUDR (le cutoff réglementaire est le 31/12/2020).

Localisation, par priorité : coordonnées GPS trouvées dans le fil (bornées à la
Côte d'Ivoire), sinon localité nommée (module partagé localites), sinon consigne
demandant la position — jamais de statut inventé.
"""

from __future__ import annotations

import re

from app.domain.agents import AgentRequete
from app.domain.ports import InferencePort
from app.services import localites
from app.services.agents.base import AgentBase, compter_mots_cles
from app.services.outils.satellite import OutilSatellite
from app.services.rag import RagRecuperateur

# Déclencheurs SATELLITAIRES/fonciers. « déforestation » et « géolocalisation »
# migrent de l'agent Réglementation (frontière nette, comme certification -> Normes) ;
# « eudr », « traçabilité », « conformité » restent réglementaires. PAS « alerte »
# seul (happerait « alerte pluie », domaine météo). Routage par MOT ENTIER.
_MOTS_SATELLITE = (
    "deforestation",
    "déforestation",
    "geolocalisation",
    "géolocalisation",
    "satellite",
    "parcelle",
    "parcelles",
    "foret",
    "forêt",
    "forets",
    "forêts",
)

# Coordonnées décimales « lat, lon » bornées Côte d'Ivoire (lat 4..11, lon -9..-2).
_COORDONNEES = re.compile(r"(-?\d{1,2}[.,]\d+)\s*[,;]\s*(-?\d{1,2}[.,]\d+)")

_CONSIGNE_POSITION = (
    "Aucune position n'a été précisée : aucune vérification satellitaire n'est "
    "possible. N'avance AUCUN constat de déforestation et n'invente aucun statut. "
    "Demande poliment au producteur sa commune (zone cacaoyère) ou ses coordonnées "
    "GPS (affichées par son téléphone), afin de vérifier les alertes au prochain "
    "échange."
)

_CONSIGNE_INDISPONIBLE = (
    "La vérification satellitaire est momentanément indisponible. N'avance AUCUN "
    "constat de déforestation, n'invente aucun statut et ne certifie rien : invite "
    "le producteur à se rapprocher du Conseil du Café-Cacao ou de son agent ANADER "
    "pour la vérification de sa parcelle."
)

_RESERVE = (
    "IMPORTANT : ce constat porte sur une zone d'environ 1 km autour du point "
    "fourni, PAS sur la parcelle cadastrée du producteur. Ne certifie JAMAIS une "
    "conformité ou non-conformité EUDR : constate les faits, explique ce qu'ils "
    "impliquent et oriente vers le Conseil du Café-Cacao ou l'agent ANADER pour "
    "toute démarche officielle."
)


class AgentSatellite(AgentBase):
    """Constats satellitaires de déforestation autour de la position du producteur."""

    nom = "satellite"
    description = "Alertes satellitaires de déforestation (contexte EUDR) autour d'un point."
    mots_cles = _MOTS_SATELLITE

    def __init__(
        self,
        inference: InferencePort,
        outil: OutilSatellite,
        rag: RagRecuperateur | None = None,
    ) -> None:
        """Initialise l'agent Satellite.

        Args:
            inference: Port d'inférence.
            outil: Outil de récupération des alertes satellitaires.
            rag: Récupérateur documentaire optionnel (contexte réglementaire EUDR).
        """
        super().__init__(inference)
        self._outil = outil
        self._rag = rag

    async def peut_traiter(self, requete: AgentRequete) -> float:
        """Score élevé si la question évoque la déforestation/parcelle (mot entier)."""
        touches = compter_mots_cles(requete.fil_ancre, self.mots_cles)
        if touches == 0:
            return 0.0
        return min(0.7 + 0.1 * touches, 1.0)

    async def _contexte(self, requete: AgentRequete) -> str | None:
        """Faits satellitaires (ou consigne) + contexte réglementaire RAG."""
        faits = await self._faits(requete)
        documentaire = await self._rag.contexte_pour(requete.fil_ancre) if self._rag else None
        return "\n\n".join(part for part in (faits, documentaire) if part)

    async def _faits(self, requete: AgentRequete) -> str:
        """Interroge l'outil selon la localisation détectée sur tout le fil."""
        texte = _fil_complet(requete)
        point = _coordonnees(texte)
        if point is not None:
            resultat = await self._outil.invoquer(lat=point[0], lon=point[1])
        else:
            localite = localites.detecter(texte)
            if localite is None:
                return _CONSIGNE_POSITION
            resultat = await self._outil.invoquer(localite=localite)
        return _formater_alertes(resultat)


def _fil_complet(requete: AgentRequete) -> str:
    """Concatène les tours utilisateur et le dernier tour ancré (mémoire du fil)."""
    tours = [t.get("content", "") for t in requete.historique if t.get("role") == "user"]
    return " ".join([*tours, requete.fil_ancre])


def _coordonnees(texte: str) -> tuple[float, float] | None:
    """Première paire « lat, lon » plausible en Côte d'Ivoire, ou None."""
    for correspondance in _COORDONNEES.finditer(texte):
        lat = float(correspondance.group(1).replace(",", "."))
        lon = float(correspondance.group(2).replace(",", "."))
        if 4.0 <= lat <= 11.0 and -9.0 <= lon <= -2.0:
            return lat, lon
    return None


def _formater_alertes(resultat: dict[str, object]) -> str:
    """Met en forme les alertes en contexte injectable, avec la réserve de portée."""
    if not resultat:
        return _CONSIGNE_INDISPONIBLE
    total = resultat.get("alertes_depuis_2021", 0)
    if not total:
        return (
            "Constat satellitaire (Global Forest Watch) : 0 alerte de déforestation "
            f"détectée depuis 2021 dans la zone (~{resultat.get('tampon_km', 1)} km "
            f"autour du point fourni). {_RESERVE}"
        )
    dates = ", ".join(str(d) for d in resultat.get("dates_recentes", []))  # type: ignore[union-attr]
    return (
        f"Constat satellitaire (Global Forest Watch) : {total} alertes de "
        "déforestation détectées depuis 2021 dans la zone "
        f"(~{resultat.get('tampon_km', 1)} km autour du point fourni). Dernières "
        f"dates d'alerte : {dates}. Explique les implications au regard du règlement "
        f"EUDR (déforestation zéro après le 31/12/2020). {_RESERVE}"
    )
```

- [ ] **Step 4 : Vérifier le vert** — `python -m pytest tests/agents/test_agent_satellite.py -q --no-cov` → 10 PASS.
- [ ] **Step 5 : Lint + commit**

```bash
python -m ruff format app/services/agents/agent_satellite.py tests/agents/test_agent_satellite.py && python -m ruff check app/services/agents/agent_satellite.py tests/agents/test_agent_satellite.py
git add api/app/services/agents/agent_satellite.py api/tests/agents/test_agent_satellite.py
git commit -m "feat(agents): agent Satellite (GPS/localité, faits datés, jamais de certification)"
```

---

### Task 4 : Frontière de routage avec l'agent Réglementation

**Files:**
- Modify: `api/app/services/agents/agent_reglementation.py` (retirer 4 mots-clés)
- Test: `api/tests/agents/test_agent_satellite.py` (section frontière)

- [ ] **Step 1 : Tests rouges**

```python
from app.services.agents.agent_reglementation import AgentReglementation


@pytest.mark.asyncio
async def test_frontiere_deforestation_va_au_satellite() -> None:
    satellite, _ = _agent()
    reglementation = AgentReglementation(_InferenceFactice())
    question = _requete("y a-t-il des alertes de déforestation sur ma parcelle ?")
    assert await satellite.peut_traiter(question) > await reglementation.peut_traiter(question)


@pytest.mark.asyncio
async def test_frontiere_eudr_reste_reglementaire() -> None:
    satellite, _ = _agent()
    reglementation = AgentReglementation(_InferenceFactice())
    question = _requete("que demande la réglementation EUDR pour exporter ?")
    assert await reglementation.peut_traiter(question) > await satellite.peut_traiter(question)
```

- [ ] **Step 2 : Vérifier l'échec** — le premier test échoue (Réglementation score aussi sur « déforestation »).

- [ ] **Step 3 : Retirer de `_MOTS_REGLEMENTATION` dans `agent_reglementation.py`** les 4 entrées `"deforestation"`, `"déforestation"`, `"geolocalisation"`, `"géolocalisation"`, et noter dans le commentaire du tuple : `# « déforestation »/« géolocalisation » sont confiés à l'agent Satellite (constat terrain) ; l'EUDR réglementaire (due diligence, conformité) reste ici.`

- [ ] **Step 4 : Vérifier le vert** — `python -m pytest tests/agents/test_agent_satellite.py tests/agents/test_agent_reglementation.py -q --no-cov` → PASS (adapter tout test Réglementation qui reposait sur « déforestation » : le remplacer par « eudr » ou « conformité »).
- [ ] **Step 5 : Commit**

```bash
git add api/app/services/agents/agent_reglementation.py api/tests/agents/
git commit -m "feat(agents): frontière Satellite/Réglementation (déforestation -> constat satellite)"
```

---

### Task 5 : Config + câblage derrière le registre

**Files:**
- Modify: `api/app/core/config.py` (2 champs)
- Modify: `api/app/api_deps.py` (`_construire_orchestrateur`)
- Test: `api/tests/agents/test_cablage_orchestrateur.py`

- [ ] **Step 1 : Test rouge (ajouter à `test_cablage_orchestrateur.py`, suivre les fixtures existantes du fichier)**

```python
def test_satellite_enregistre_dans_l_orchestrateur(orchestrateur_construit) -> None:
    """L'agent satellite est enregistré ; sans GFW_API_KEY l'app démarre (source neutre)."""
    noms = orchestrateur_construit._routeur.registre.noms()
    assert "satellite" in noms
```

(Adapter le nom exact de la fixture à celles déjà présentes dans ce fichier de test — il construit déjà l'orchestrateur complet pour vérifier les autres agents.)

- [ ] **Step 2 : Vérifier l'échec** — `python -m pytest tests/agents/test_cablage_orchestrateur.py -q --no-cov` → FAIL (`"satellite" not in [...]`).

- [ ] **Step 3 : Config + câblage**

Dans `api/app/core/config.py`, à côté des réglages météo/prix :

```python
    # Global Forest Watch (agent Satellite) : clé Data API (Secret K8s, jamais en
    # ConfigMap). Vide = agent actif mais source indisponible (consigne explicite).
    # ⚠ La clé expire après UN AN (créée le 05/07/2026 -> renouveler avant 30/06/2027).
    gfw_api_key: str = ""
    gfw_timeout_s: float = 15.0
```

Dans `api/app/api_deps.py` (`_construire_orchestrateur`), avec les autres imports locaux puis l'enregistrement APRÈS `AgentNormes` (l'ordre stable du tri fait gagner Réglementation sur une égalité de score, comme pour Normes) :

```python
    from app.services.agents.agent_satellite import AgentSatellite
    from app.services.outils.indisponible import SatelliteIndisponible
    from app.services.outils.satellite import OutilSatellite
    from app.services.outils.satellite_gfw import SatelliteGfw

    satellite = (
        SatelliteGfw(settings.gfw_api_key, timeout_s=settings.gfw_timeout_s)
        if settings.gfw_api_key
        else SatelliteIndisponible()
    )
    ...
    registre.enregistrer(AgentSatellite(inference, OutilSatellite(satellite), rag=rag))  # type: ignore[arg-type]
```

Mettre à jour le docstring de `_construire_orchestrateur` (« rag/meteo/prix/reglementation/normes/satellite/reporting »).

- [ ] **Step 4 : Vérifier le vert** — `python -m pytest tests/agents/test_cablage_orchestrateur.py -q --no-cov` → PASS.
- [ ] **Step 5 : Commit**

```bash
git add api/app/core/config.py api/app/api_deps.py api/tests/agents/test_cablage_orchestrateur.py
git commit -m "feat(agents): câblage agent Satellite (GFW_API_KEY, source neutre sans clé)"
```

---

### Task 6 : Suite complète, docs, livraison

**Files:**
- Modify: `docs/agents_v3.md` (agent n°7 dans la section recette)
- Modify: `scripts/build_doc_agentique.py` (mention agent n°7 Satellite + regénérer le .docx)
- Modify: `deploy/k8s/api.yaml` (si les Secrets y sont référencés : entrée GFW_API_KEY — sinon documenter la création du Secret dans le commit)

- [ ] **Step 1 : Suite complète + lint** — `cd api && python -m pytest -q` → tout vert, couverture ≥ 97 %. `python -m ruff format api scripts && python -m ruff check api scripts`.
- [ ] **Step 2 : Docs** — dans `docs/agents_v3.md`, après le paragraphe agent n°6 Normes, ajouter : « **Agent n°7 — Satellite** (`agent_satellite.py`) : constats d'alertes de déforestation GFW (GLAD+RADD) autour de la position (GPS du fil ou localité géocodée, tampon ~1 km), croisés au RAG EUDR. Jamais de certification de conformité — constat + orientation Conseil CC/ANADER. Source validée le 05/07/2026 (couverture CI, fraîcheur ~2 semaines) ; clé `GFW_API_KEY` en Secret K8s, expire le 30/06/2027. » Reporter la même mention dans `build_doc_agentique.py` (section recette, après le paragraphe Normes) et regénérer : `python scripts/build_doc_agentique.py`.
- [ ] **Step 3 : Commit + livraison sprint** — `git add -A docs scripts api && git commit -m "feat(agents): agent Satellite A8 MVP (alertes déforestation GFW)"`, push develop, PR → main, release auto, `roll-image.sh <tag>`, puis créer le Secret K8s : `kubectl -n opencacao create secret generic gfw --from-literal=GFW_API_KEY=<clé du .env>` + le référencer dans le déploiement api (`envFrom`/`secretKeyRef`) selon le pattern des secrets existants (ZeptoMail).
- [ ] **Step 4 : Vérification prod** — question « Y a-t-il de la déforestation à Soubré ? » sur `/v1/chat` : routage `satellite`, réponse avec constat chiffré + réserve + orientation ; logs `dispatch agent=satellite`.

## Auto-revue

- Spec couverte : port/outil (T1), adaptateur+307+GROUP BY (T2), agent+3 localisations+consignes+réserve (T3), frontière (T4), config/câblage/secret (T5), docs+livraison+vérif prod (T6). Cache GFW et polygones réels : hors périmètre (spec).
- Types cohérents : `alertes(localite="", lat=None, lon=None)` partout ; `invoquer(**kwargs)` conforme au Protocol `Outil` ; `_contexte` conforme à `AgentBase` (comme météo/prix).
- Pas de placeholder : chaque étape porte son code ou sa commande exacte.
