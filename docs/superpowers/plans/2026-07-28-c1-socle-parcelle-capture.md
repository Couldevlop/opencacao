# C1 — Socle parcelle & capture : plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Doter OpenCacao d'un objet métier `Parcelle` persistant et de quatre modalités de capture terrain (photos, vidéo échantillonnée, parcours GPS, parcours + vidéo), avec validation géographique et contrôle de recevabilité des images.

**Architecture:** La parcelle devient l'objet central de la V3. Le serveur n'expose que **deux contrats** — un jeu d'images géoréférencées, une trace de points — et le navigateur fait le travail coûteux (échantillonnage vidéo depuis `canvas`, `watchPosition`, calcul des métriques de netteté sur les pixels qu'il possède déjà). Persistance SQLite de la bibliothèque standard, sur le moule exact de `api/app/core/sessions.py`. Les images vont sur le disque (`/data/captures/<sha256>.jpg`), la base ne stocke que l'empreinte et les métadonnées.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, `sqlite3` (stdlib), `structlog`, pytest + pytest-asyncio. **Aucune dépendance nouvelle.** Front : JavaScript natif dans `web/`.

**Spec de référence :** `docs/superpowers/specs/2026-07-28-v3-operationnelle-design.md` §5 et §6.

---

## Global Constraints

Ces contraintes s'appliquent à **toutes** les tâches. Une tâche qui les viole est à refaire.

- **Python 3.11+**, `from __future__ import annotations` en tête de chaque module.
- **Typage systématique.** Aucune variable globale mutable.
- **`ruff format` + `ruff check`** doivent passer. `line-length = 100`, règles `E, F, I, UP, B, C4, SIM` (`api/pyproject.toml`).
- **Couverture ≥ 97 %** — le seuil `--cov-fail-under=97` fait échouer `pytest` sinon.
- **Logging par `structlog`** via `from app.core.logging import get_logger`. **Jamais `print()`.**
- **Docstrings format Google**, en français, sur chaque module, classe et fonction publique.
- **Aucune dépendance nouvelle.** Ni Pillow, ni `python-multipart`, ni bibliothèque géospatiale.
- **Aucune logique métier dans les routeurs** — tout passe par `api/app/services/`.
- **Aucun appel réseau dans les tests.**
- **Aucun dosage phytosanitaire nulle part**, même en donnée de test.
- **Tolérance aux pannes du stockage** : si `/data` est inaccessible, l'API démarre quand même et les parcelles sont marquées indisponibles. Le chat ne doit jamais tomber à cause des parcelles.
- **Nommage en français** pour le métier (`parcelle`, `superficie_ha`, `recevabilite`), conformément au code existant.
- **Cloisonnement par `proprietaire`** : identifiant anonyme d'appareil issu de l'en-tête `X-Device-Id`, exactement comme les sessions V2 (`api/app/api_deps.py::get_device_id`). Un appareil ne voit jamais les parcelles d'un autre.

### Écart assumé par rapport à la spec

La spec §5.1 nomme le champ de rattachement `compte` (compte magic-link). **Ce plan utilise `proprietaire`** (identifiant d'appareil), pour trois raisons : l'authentification est désactivée par défaut en production, les sessions V2 emploient déjà ce mécanisme, et le cloisonnement fonctionne sans obliger le producteur à créer un compte. Quand l'authentification est active, l'identifiant de compte peut être fourni comme `proprietaire` sans changement de schéma.

La spec §6.2 prévoit le calcul de netteté **côté serveur** avec `numpy`. C'est impossible sans décodeur d'image : `numpy` ne décode pas le JPEG. Répartition retenue :

| Responsabilité | Où | Pourquoi |
|---|---|---|
| Calcul de netteté et d'exposition | **navigateur** (Tâche 8) | il possède déjà les pixels décodés dans `canvas` ; refuse avant téléversement, ce qui économise la bande passante sur réseau faible |
| Validation du format, des dimensions, de la taille | **serveur** (Tâche 3) | contrôle de sécurité sur un fichier écrit sur disque ; lecture d'en-tête en Python pur, sans dépendance |
| Plausibilité des métriques déclarées | **serveur** (Tâche 3) | le client peut mentir ; on borne, on ne fait pas confiance aveuglément |

---

## Structure des fichiers

| Fichier | Responsabilité | Tâche |
|---|---|---|
| `api/app/services/geometrie.py` | **créé** — bornes de la Côte d'Ivoire, superficie, auto-intersection | 1 |
| `api/app/services/agents/agent_satellite.py` | **modifié** — consomme les bornes partagées au lieu de les redéfinir | 1 |
| `api/app/models/parcelle.py` | **créé** — types de domaine (`frozen`) et schémas Pydantic d'API | 2 |
| `api/app/services/vision/__init__.py` | **créé** — paquet vision | 3 |
| `api/app/services/vision/recevabilite.py` | **créé** — format, dimensions, plausibilité des métriques | 3 |
| `api/app/domain/ports.py` | **modifié** — ajout de `ParcelleStorePort` | 4 |
| `api/app/core/parcelles_store.py` | **créé** — dépôt SQLite, moule `sessions.py` | 4 |
| `api/app/services/parcelles.py` | **créé** — service métier, écriture disque, orchestration | 5 |
| `api/app/core/config.py` | **modifié** — réglages parcelles et `profil_materiel` | 6 |
| `api/app/api_deps.py` | **modifié** — `get_parcelle_store`, `get_service_parcelles` | 6 |
| `api/app/main.py` | **modifié** — état applicatif, cycle de vie, purge, routeur | 6 |
| `api/app/routers/parcelles.py` | **créé** — endpoints `/v1/parcelles` | 6 |
| `web/parcelle.html`, `web/parcelle.js` | **créés** — écran « Ma parcelle », 4 modalités | 8 |
| `deploy/k8s/api.yaml` | **modifié** — variables d'environnement des parcelles | 7 |

---

## Task 1: Géométrie partagée — bornes CI, superficie, auto-intersection

Les bornes de la Côte d'Ivoire sont aujourd'hui **codées en dur** dans `api/app/services/agents/agent_satellite.py:141` (`4.0 <= lat <= 11.0 and -9.0 <= lon <= -2.0`). Deux consommateurs vont en avoir besoin : cette tâche les extrait dans un module partagé, comme `localites.py` l'a fait pour les localités.

**Files:**
- Create: `api/app/services/geometrie.py`
- Modify: `api/app/services/agents/agent_satellite.py` (fonction `_coordonnees`, ~ligne 136-143)
- Test: `api/tests/test_geometrie.py`

**Interfaces:**
- Consomme : rien.
- Produit :
  - `dans_cote_ivoire(latitude: float, longitude: float) -> bool`
  - `superficie_ha(points: Sequence[tuple[float, float]]) -> float` — points en `(lat, lon)`
  - `anneau_auto_intersecte(points: Sequence[tuple[float, float]]) -> bool`
  - `LAT_MIN, LAT_MAX, LON_MIN, LON_MAX` (constantes publiques)

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `api/tests/test_geometrie.py` :

```python
"""Tests de la géométrie partagée (bornes CI, superficie, auto-intersection)."""

from __future__ import annotations

import pytest

from app.services.geometrie import (
    anneau_auto_intersecte,
    dans_cote_ivoire,
    superficie_ha,
)


@pytest.mark.parametrize(
    "latitude,longitude",
    [
        (6.85, -5.28),   # Yamoussoukro
        (5.35, -4.02),   # Abidjan
        (6.13, -6.60),   # Daloa, zone cacaoyère
    ],
)
def test_dans_cote_ivoire_accepte_les_villes_ivoiriennes(latitude, longitude):
    assert dans_cote_ivoire(latitude, longitude) is True


@pytest.mark.parametrize(
    "latitude,longitude",
    [
        (48.86, 2.35),    # Paris
        (5.56, 0.20),     # Accra, Ghana — juste à l'est
        (12.37, -1.52),   # Ouagadougou — au nord
        (0.0, 0.0),       # golfe de Guinée
    ],
)
def test_dans_cote_ivoire_refuse_hors_bornes(latitude, longitude):
    assert dans_cote_ivoire(latitude, longitude) is False


def test_superficie_un_hectare_environ():
    """Un carré de 100 m de côté vaut ~1 ha. 100 m ≈ 0,000899° de latitude."""
    cote = 0.000899
    lat, lon = 6.85, -5.28
    carre = [
        (lat, lon),
        (lat, lon + cote),
        (lat + cote, lon + cote),
        (lat + cote, lon),
    ]
    assert superficie_ha(carre) == pytest.approx(1.0, abs=0.05)


def test_superficie_ignore_le_sens_de_parcours():
    """Un anneau parcouru en sens inverse a la même superficie (valeur absolue)."""
    cote = 0.000899
    lat, lon = 6.85, -5.28
    carre = [
        (lat, lon),
        (lat, lon + cote),
        (lat + cote, lon + cote),
        (lat + cote, lon),
    ]
    assert superficie_ha(carre) == pytest.approx(superficie_ha(list(reversed(carre))))


def test_superficie_nulle_si_moins_de_trois_points():
    assert superficie_ha([(6.85, -5.28), (6.86, -5.28)]) == 0.0


def test_anneau_simple_ne_s_auto_intersecte_pas():
    carre = [(6.85, -5.28), (6.85, -5.27), (6.86, -5.27), (6.86, -5.28)]
    assert anneau_auto_intersecte(carre) is False


def test_anneau_en_huit_s_auto_intersecte():
    """Deux côtés opposés croisés : le tracé se coupe lui-même."""
    huit = [(6.85, -5.28), (6.86, -5.27), (6.85, -5.27), (6.86, -5.28)]
    assert anneau_auto_intersecte(huit) is True


def test_anneau_de_moins_de_quatre_points_ne_s_auto_intersecte_pas():
    assert anneau_auto_intersecte([(6.85, -5.28), (6.86, -5.27)]) is False
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `cd api && python -m pytest tests/test_geometrie.py -v --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.geometrie'`

- [ ] **Step 3: Écrire l'implémentation minimale**

Créer `api/app/services/geometrie.py` :

```python
"""Géométrie des parcelles — bornes de la Côte d'Ivoire, superficie, validité d'anneau.

Module partagé, sans dépendance externe (ni bibliothèque géospatiale, ni ``numpy``).
Les bornes vivaient auparavant en dur dans ``services/agents/agent_satellite.py`` ;
elles sont désormais ici, comme les localités le sont dans ``services/localites.py``.

Les superficies se calculent sur une **projection équirectangulaire locale** centrée
sur le barycentre de la parcelle : à l'échelle d'une plantation (quelques hectares),
l'erreur est négligeable, et l'on évite l'absurdité d'un calcul en degrés bruts —
un degré de longitude ne vaut pas un degré de latitude.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

# Bornes de l'enveloppe de la Côte d'Ivoire, volontairement larges (le pays s'inscrit
# dans ce rectangle). Un point hors de ces bornes n'est certainement pas ivoirien ;
# un point dedans est plausible, ce qui suffit à écarter les saisies aberrantes.
LAT_MIN = 4.0
LAT_MAX = 11.0
LON_MIN = -9.0
LON_MAX = -2.0

# Rayon moyen de la Terre (IUGG), en mètres.
_RAYON_TERRE_M = 6_371_008.8

_METRES_CARRES_PAR_HECTARE = 10_000.0


def dans_cote_ivoire(latitude: float, longitude: float) -> bool:
    """Indique si un point tombe dans l'enveloppe de la Côte d'Ivoire.

    Args:
        latitude: Latitude en degrés décimaux.
        longitude: Longitude en degrés décimaux.

    Returns:
        ``True`` si le point est plausible en Côte d'Ivoire.
    """
    return LAT_MIN <= latitude <= LAT_MAX and LON_MIN <= longitude <= LON_MAX


def _projeter(points: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """Projette des points (lat, lon) en mètres, autour de leur barycentre."""
    lat_0 = sum(lat for lat, _ in points) / len(points)
    lon_0 = sum(lon for _, lon in points) / len(points)
    facteur = math.pi / 180.0 * _RAYON_TERRE_M
    cos_lat = math.cos(math.radians(lat_0))
    return [
        ((lon - lon_0) * facteur * cos_lat, (lat - lat_0) * facteur)
        for lat, lon in points
    ]


def superficie_ha(points: Sequence[tuple[float, float]]) -> float:
    """Calcule la superficie d'un anneau fermé, en hectares.

    L'anneau est implicitement fermé (le dernier point est relié au premier). Le sens
    de parcours est indifférent : on retourne une valeur absolue.

    Args:
        points: Sommets ``(latitude, longitude)``, au moins trois.

    Returns:
        La superficie en hectares, ``0.0`` si moins de trois points.
    """
    if len(points) < 3:
        return 0.0
    projetes = _projeter(points)
    somme = 0.0
    for indice, (x_1, y_1) in enumerate(projetes):
        x_2, y_2 = projetes[(indice + 1) % len(projetes)]
        somme += x_1 * y_2 - x_2 * y_1
    return abs(somme) / 2.0 / _METRES_CARRES_PAR_HECTARE


def _segments_se_croisent(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> bool:
    """Indique si les segments [a,b] et [c,d] se croisent proprement."""

    def orientation(p: tuple[float, float], q: tuple[float, float], r: tuple[float, float]) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    d_1 = orientation(c, d, a)
    d_2 = orientation(c, d, b)
    d_3 = orientation(a, b, c)
    d_4 = orientation(a, b, d)
    return ((d_1 > 0) != (d_2 > 0)) and ((d_3 > 0) != (d_4 > 0))


def anneau_auto_intersecte(points: Sequence[tuple[float, float]]) -> bool:
    """Indique si le tracé se coupe lui-même.

    Un parcours GPS qui s'auto-intersecte ne délimite pas une parcelle : la superficie
    calculée n'aurait aucun sens. Comparaison exhaustive des paires de côtés — en
    O(n²), acceptable car un parcours de parcelle compte quelques dizaines de points.

    Args:
        points: Sommets ``(latitude, longitude)`` de l'anneau.

    Returns:
        ``True`` si deux côtés non adjacents se croisent.
    """
    if len(points) < 4:
        return False
    projetes = _projeter(points)
    nombre = len(projetes)
    for i in range(nombre):
        a, b = projetes[i], projetes[(i + 1) % nombre]
        for j in range(i + 1, nombre):
            # On saute les côtés adjacents : ils partagent un sommet par construction.
            if j == i or (j + 1) % nombre == i or j == (i + 1) % nombre:
                continue
            c, d = projetes[j], projetes[(j + 1) % nombre]
            if _segments_se_croisent(a, b, c, d):
                return True
    return False
```

- [ ] **Step 4: Lancer les tests pour vérifier qu'ils passent**

Run: `cd api && python -m pytest tests/test_geometrie.py -v --no-cov`
Expected: PASS — 13 tests (8 fonctions, dont deux `parametrize` qui donnent 3 + 4 cas)

- [ ] **Step 5: Faire consommer les bornes partagées par l'agent Satellite**

Dans `api/app/services/agents/agent_satellite.py`, ajouter l'import :

```python
from app.services.geometrie import dans_cote_ivoire
```

Puis remplacer le corps de `_coordonnees` (la comparaison en dur) :

```python
def _coordonnees(texte: str) -> tuple[float, float] | None:
    """Première paire « lat, lon » plausible en Côte d'Ivoire, ou None."""
    for correspondance in _COORDONNEES.finditer(texte):
        lat = float(correspondance.group(1).replace(",", "."))
        lon = float(correspondance.group(2).replace(",", "."))
        if dans_cote_ivoire(lat, lon):
            return lat, lon
    return None
```

- [ ] **Step 6: Vérifier que l'agent Satellite ne régresse pas**

Run: `cd api && python -m pytest tests/agents/test_agent_satellite.py -v --no-cov`
Expected: PASS — aucun test modifié, comportement identique (les bornes extraites sont les mêmes valeurs)

- [ ] **Step 7: Lint et commit**

```bash
cd api && python -m ruff format app/services/geometrie.py tests/test_geometrie.py app/services/agents/agent_satellite.py && python -m ruff check app/services/geometrie.py tests/test_geometrie.py app/services/agents/agent_satellite.py
cd .. && git add api/app/services/geometrie.py api/tests/test_geometrie.py api/app/services/agents/agent_satellite.py
git commit -m "feat(geometrie): module partage - bornes CI, superficie, auto-intersection

Les bornes de la Cote d Ivoire etaient codees en dur dans agent_satellite ;
elles sont desormais partagees, comme les localites le sont dans localites.py.
Superficie par projection equirectangulaire locale (jamais en degres bruts)."
```

---

## Task 2: Types de domaine de la parcelle

**Files:**
- Create: `api/app/models/parcelle.py`
- Test: `api/tests/test_models_parcelle.py`

**Interfaces:**
- Consomme : `dans_cote_ivoire`, `superficie_ha`, `anneau_auto_intersecte` (Tâche 1).
- Produit :
  - `dataclass(frozen=True)` : `Coordonnee`, `Geometrie`, `Recevabilite`, `Image`, `Capture`, `Parcelle`
  - Enums : `Modalite`, `MotifRecevabilite`, `TypeGeometrie`, `SourceGeometrie`
  - Pydantic : `CreerParcelleRequest`, `ParcelleReponse`, `GeometrieRequest`, `CaptureRequest`, `ImageRequest`, `CoordonneeRequest`, `CaptureReponse`
  - Constantes : `SUPERFICIE_MIN_HA = 0.1`, `SUPERFICIE_MAX_HA = 50.0`, `POINTS_MIN_POLYGONE = 4`

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `api/tests/test_models_parcelle.py` :

```python
"""Tests des types de domaine de la parcelle."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models.parcelle import (
    CaptureRequest,
    Coordonnee,
    CreerParcelleRequest,
    Geometrie,
    ImageRequest,
    Modalite,
    MotifRecevabilite,
    Recevabilite,
    SourceGeometrie,
    TypeGeometrie,
)


def test_coordonnee_est_immuable():
    point = Coordonnee(latitude=6.85, longitude=-5.28)
    with pytest.raises(dataclasses.FrozenInstanceError):
        point.latitude = 7.0  # type: ignore[misc]


def test_geometrie_polygone_calcule_sa_superficie():
    cote = 0.000899
    lat, lon = 6.85, -5.28
    points = tuple(
        Coordonnee(latitude=a, longitude=b)
        for a, b in [
            (lat, lon),
            (lat, lon + cote),
            (lat + cote, lon + cote),
            (lat + cote, lon),
        ]
    )
    geometrie = Geometrie.depuis_points(points, source=SourceGeometrie.PARCOURS_GPS)
    assert geometrie.type is TypeGeometrie.POLYGONE
    assert geometrie.superficie_ha == pytest.approx(1.0, abs=0.05)


def test_geometrie_point_unique_na_pas_de_superficie():
    geometrie = Geometrie.depuis_points(
        (Coordonnee(latitude=6.85, longitude=-5.28),),
        source=SourceGeometrie.SAISIE_MANUELLE,
    )
    assert geometrie.type is TypeGeometrie.POINT
    assert geometrie.superficie_ha is None


def test_recevabilite_refusee_porte_un_conseil():
    verdict = Recevabilite(
        recevable=False,
        motif=MotifRecevabilite.FLOU,
        conseil="Approchez-vous de la cabosse et refaites la photo.",
        score_nettete=12.0,
    )
    assert verdict.recevable is False
    assert verdict.conseil


def test_creer_parcelle_request_exige_un_nom():
    with pytest.raises(ValidationError):
        CreerParcelleRequest(nom="", localite="Daloa")


def test_creer_parcelle_request_borne_la_longueur_du_nom():
    with pytest.raises(ValidationError):
        CreerParcelleRequest(nom="x" * 121, localite="Daloa")


def test_image_request_borne_le_score_de_nettete():
    """Le score vient du client : il doit rester dans un intervalle plausible."""
    with pytest.raises(ValidationError):
        ImageRequest(contenu_base64="AAAA", largeur=800, hauteur=600, score_nettete=-1.0)


def test_capture_request_plafonne_le_nombre_d_images():
    images = [
        ImageRequest(contenu_base64="AAAA", largeur=800, hauteur=600, score_nettete=50.0)
        for _ in range(13)
    ]
    with pytest.raises(ValidationError):
        CaptureRequest(modalite=Modalite.PHOTOS, images=images)


def test_capture_request_refuse_une_capture_totalement_vide():
    with pytest.raises(ValidationError):
        CaptureRequest(modalite=Modalite.PHOTOS, images=[], trace=[])


def test_capture_request_accepte_une_trace_seule():
    requete = CaptureRequest(
        modalite=Modalite.PARCOURS,
        images=[],
        trace=[
            {"latitude": 6.85, "longitude": -5.28},
            {"latitude": 6.85, "longitude": -5.27},
            {"latitude": 6.86, "longitude": -5.27},
            {"latitude": 6.86, "longitude": -5.28},
        ],
    )
    assert len(requete.trace) == 4


def test_modalite_couvre_les_quatre_cas():
    assert {m.value for m in Modalite} == {"photos", "video", "parcours", "parcours_video"}


def test_capture_horodatee_conserve_son_instant():
    instant = datetime(2026, 7, 28, 10, 30, tzinfo=UTC)
    point = Coordonnee(latitude=6.85, longitude=-5.28, horodatage=instant)
    assert point.horodatage == instant
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `cd api && python -m pytest tests/test_models_parcelle.py -v --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.parcelle'`

- [ ] **Step 3: Écrire l'implémentation**

Créer `api/app/models/parcelle.py` :

```python
"""Types métier de la parcelle cacaoyère et de ses captures terrain.

Deux familles cohabitent, comme dans ``app/models/session.py`` :

* les **types de domaine** (``dataclass(frozen=True)``) — immuables, sans dépendance
  framework, manipulés par les services et le dépôt ;
* les **schémas d'API** (Pydantic v2) — validation des entrées et des sorties HTTP.

L'immuabilité n'est pas un ornement : une capture qui traverse plusieurs couches
(recevabilité, écriture disque, persistance) ne doit pas pouvoir être modifiée en
route par surprise.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from app.services.geometrie import superficie_ha

# Bornes de plausibilité d'une parcelle cacaoyère ivoirienne. En dessous de 0,1 ha le
# tracé est une erreur de manipulation ; au-delà de 50 ha ce n'est plus une parcelle
# de producteur mais un domaine, qui relève d'un autre traitement.
SUPERFICIE_MIN_HA = 0.1
SUPERFICIE_MAX_HA = 50.0

# Un anneau exploitable demande au moins quatre sommets distincts (un triangle fermé
# par le parcours GPS est presque toujours un tracé abandonné).
POINTS_MIN_POLYGONE = 4

# Plafond d'images par capture, aligné sur l'échantillonnage vidéo du navigateur
# (1 image / 2 s). Douze vues suffisent à un constat et bornent le téléversement.
IMAGES_MAX_PAR_CAPTURE = 12

# Bornes du score de netteté (variance du laplacien) calculé par le navigateur.
SCORE_NETTETE_MIN = 0.0
SCORE_NETTETE_MAX = 100_000.0

NOM_MAX = 120
LOCALITE_MAX = 120


class TypeGeometrie(str, Enum):
    """Nature géométrique d'une parcelle."""

    POINT = "point"
    POLYGONE = "polygone"


class SourceGeometrie(str, Enum):
    """Provenance de la géométrie."""

    PARCOURS_GPS = "parcours_gps"
    SAISIE_MANUELLE = "saisie_manuelle"


class Modalite(str, Enum):
    """Modalité de capture terrain."""

    PHOTOS = "photos"
    VIDEO = "video"
    PARCOURS = "parcours"
    PARCOURS_VIDEO = "parcours_video"


class MotifRecevabilite(str, Enum):
    """Motif du verdict de recevabilité d'une image."""

    OK = "ok"
    FLOU = "flou"
    SOUS_EXPOSE = "sous_expose"
    SUR_EXPOSE = "sur_expose"
    TROP_PETITE = "trop_petite"
    FORMAT_REFUSE = "format_refuse"


@dataclass(frozen=True)
class Coordonnee:
    """Point géographique horodaté, tel que rendu par le navigateur."""

    latitude: float
    longitude: float
    precision_m: float | None = None
    horodatage: datetime | None = None


@dataclass(frozen=True)
class Geometrie:
    """Géométrie d'une parcelle : un point, ou un anneau et sa superficie."""

    type: TypeGeometrie
    points: tuple[Coordonnee, ...]
    source: SourceGeometrie
    superficie_ha: float | None = None

    @classmethod
    def depuis_points(
        cls, points: tuple[Coordonnee, ...], source: SourceGeometrie
    ) -> Geometrie:
        """Construit une géométrie et calcule sa superficie si c'est un anneau.

        Args:
            points: Sommets relevés, dans l'ordre du parcours.
            source: Provenance des points.

        Returns:
            Une géométrie de type ``POINT`` si un seul point, ``POLYGONE`` sinon.
        """
        if len(points) < POINTS_MIN_POLYGONE:
            return cls(type=TypeGeometrie.POINT, points=points, source=source)
        surface = superficie_ha([(p.latitude, p.longitude) for p in points])
        return cls(
            type=TypeGeometrie.POLYGONE,
            points=points,
            source=source,
            superficie_ha=surface,
        )


@dataclass(frozen=True)
class Recevabilite:
    """Verdict de recevabilité d'une image, avec conseil de reprise."""

    recevable: bool
    motif: MotifRecevabilite
    conseil: str
    score_nettete: float


@dataclass(frozen=True)
class Image:
    """Image persistée : identifiée par l'empreinte de son contenu."""

    empreinte_sha256: str
    largeur: int
    hauteur: int
    recevabilite: Recevabilite
    coordonnee: Coordonnee | None = None


@dataclass(frozen=True)
class Capture:
    """Une session de capture terrain rattachée à une parcelle."""

    identifiant: str
    parcelle: str
    proprietaire: str
    modalite: Modalite
    cree_le: datetime
    images: tuple[Image, ...] = ()
    trace: tuple[Coordonnee, ...] = ()


@dataclass(frozen=True)
class Parcelle:
    """Une parcelle cacaoyère déclarée par un producteur."""

    identifiant: str
    proprietaire: str
    nom: str
    localite: str
    direction_regionale: str
    cree_le: datetime
    maj_le: datetime
    geometrie: Geometrie | None = None


# --------------------------------------------------------------- schémas d'API


class CoordonneeRequest(BaseModel):
    """Point géographique reçu du navigateur."""

    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    precision_m: float | None = Field(default=None, ge=0.0, le=10_000.0)
    horodatage: datetime | None = None


class ImageRequest(BaseModel):
    """Image téléversée, encodée en base64.

    L'encodage base64 évite la dépendance ``python-multipart``, conformément au choix
    déjà retenu pour la console de curation (``app/curation/models.py``).

    Les métriques ``score_nettete`` et ``luminance_moyenne`` sont calculées par le
    navigateur, qui possède les pixels décodés. Le serveur les borne sans leur faire
    confiance : ce sont des indications de confort, pas un contrôle de sécurité.
    """

    contenu_base64: str = Field(min_length=1)
    largeur: int = Field(ge=1, le=20_000)
    hauteur: int = Field(ge=1, le=20_000)
    score_nettete: float = Field(ge=SCORE_NETTETE_MIN, le=SCORE_NETTETE_MAX)
    luminance_moyenne: float = Field(default=128.0, ge=0.0, le=255.0)
    coordonnee: CoordonneeRequest | None = None


class CaptureRequest(BaseModel):
    """Dépôt d'une capture : des images, une trace, ou les deux."""

    modalite: Modalite
    images: list[ImageRequest] = Field(default_factory=list, max_length=IMAGES_MAX_PAR_CAPTURE)
    trace: list[CoordonneeRequest] = Field(default_factory=list, max_length=2_000)

    @model_validator(mode="after")
    def _au_moins_une_donnee(self) -> CaptureRequest:
        """Refuse une capture qui n'apporte ni image ni trace."""
        if not self.images and not self.trace:
            raise ValueError("Une capture doit contenir au moins une image ou une trace.")
        return self


class GeometrieRequest(BaseModel):
    """Enregistrement d'une géométrie de parcelle (parcours ou saisie)."""

    points: list[CoordonneeRequest] = Field(min_length=1, max_length=2_000)
    source: SourceGeometrie = SourceGeometrie.PARCOURS_GPS


class CreerParcelleRequest(BaseModel):
    """Création d'une parcelle."""

    nom: str = Field(min_length=1, max_length=NOM_MAX)
    localite: str = Field(min_length=1, max_length=LOCALITE_MAX)


class RecevabiliteReponse(BaseModel):
    """Verdict de recevabilité exposé au client."""

    recevable: bool
    motif: MotifRecevabilite
    conseil: str
    score_nettete: float


class ImageReponse(BaseModel):
    """Image telle que persistée, exposée au client."""

    empreinte_sha256: str
    largeur: int
    hauteur: int
    recevabilite: RecevabiliteReponse
    coordonnee: CoordonneeRequest | None = None


class GeometrieReponse(BaseModel):
    """Géométrie exposée au client, superficie comprise."""

    type: TypeGeometrie
    points: list[CoordonneeRequest]
    source: SourceGeometrie
    superficie_ha: float | None = None


class ParcelleReponse(BaseModel):
    """Parcelle exposée au client."""

    identifiant: str
    nom: str
    localite: str
    direction_regionale: str
    cree_le: datetime
    maj_le: datetime
    geometrie: GeometrieReponse | None = None


class CaptureReponse(BaseModel):
    """Capture exposée au client."""

    identifiant: str
    parcelle: str
    modalite: Modalite
    cree_le: datetime
    images: list[ImageReponse] = Field(default_factory=list)
    trace: list[CoordonneeRequest] = Field(default_factory=list)
```

- [ ] **Step 4: Lancer les tests pour vérifier qu'ils passent**

Run: `cd api && python -m pytest tests/test_models_parcelle.py -v --no-cov`
Expected: PASS — 11 tests

- [ ] **Step 5: Lint et commit**

```bash
cd api && python -m ruff format app/models/parcelle.py tests/test_models_parcelle.py && python -m ruff check app/models/parcelle.py tests/test_models_parcelle.py
cd .. && git add api/app/models/parcelle.py api/tests/test_models_parcelle.py
git commit -m "feat(parcelle): types de domaine et schemas d API

Domaine immuable (frozen dataclasses) + schemas Pydantic. Quatre modalites de
capture, superficie calculee jamais saisie, bornes de plausibilite 0,1-50 ha."
```

---

## Task 3: Recevabilité — format, dimensions, plausibilité

Étage 0 de la cascade de vision. **Aucun modèle d'apprentissage, aucune dépendance.** Le serveur ne décode pas les pixels : il lit les en-têtes de fichier en Python pur, ce qui valide le format *et* fournit les dimensions réelles — indispensable puisqu'on écrit ces fichiers sur disque.

**Files:**
- Create: `api/app/services/vision/__init__.py`
- Create: `api/app/services/vision/recevabilite.py`
- Test: `api/tests/test_recevabilite.py`

**Interfaces:**
- Consomme : `MotifRecevabilite`, `Recevabilite`, `ImageRequest` (Tâche 2).
- Produit :
  - `SEUIL_NETTETE = 60.0`, `LUMINANCE_MIN = 30.0`, `LUMINANCE_MAX = 225.0`, `COTE_MIN_PX = 320`, `TAILLE_MAX_OCTETS = 3_000_000`
  - `dimensions_depuis_entete(donnees: bytes) -> tuple[int, int] | None`
  - `evaluer(image: ImageRequest, donnees: bytes) -> Recevabilite`

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `api/tests/test_recevabilite.py` :

```python
"""Tests de l'étage 0 — recevabilité d'une image de plantation."""

from __future__ import annotations

import struct

from app.models.parcelle import ImageRequest, MotifRecevabilite
from app.services.vision.recevabilite import (
    COTE_MIN_PX,
    SEUIL_NETTETE,
    dimensions_depuis_entete,
    evaluer,
)


def _png(largeur: int, hauteur: int) -> bytes:
    """Fabrique un en-tête PNG minimal mais valide (signature + IHDR)."""
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", largeur, hauteur)
    return signature + ihdr + b"\x08\x02\x00\x00\x00" + b"\x00" * 4


def _jpeg(largeur: int, hauteur: int) -> bytes:
    """Fabrique un en-tête JPEG minimal mais valide (SOI + APP0 + SOF0)."""
    soi = b"\xff\xd8"
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
    sof0 = (
        b"\xff\xc0"
        + struct.pack(">H", 17)
        + b"\x08"
        + struct.pack(">HH", hauteur, largeur)
        + b"\x03"
        + b"\x00" * 9
    )
    return soi + app0 + sof0


def _image(**surcharges) -> ImageRequest:
    defauts = {
        "contenu_base64": "AAAA",
        "largeur": 1024,
        "hauteur": 768,
        "score_nettete": 400.0,
        "luminance_moyenne": 128.0,
    }
    return ImageRequest(**{**defauts, **surcharges})


def test_dimensions_lues_dans_un_entete_png():
    assert dimensions_depuis_entete(_png(1024, 768)) == (1024, 768)


def test_dimensions_lues_dans_un_entete_jpeg():
    assert dimensions_depuis_entete(_jpeg(1600, 1200)) == (1600, 1200)


def test_dimensions_none_si_le_format_est_inconnu():
    assert dimensions_depuis_entete(b"ceci n'est pas une image") is None


def test_image_nette_et_bien_exposee_est_recevable():
    verdict = evaluer(_image(), _jpeg(1024, 768))
    assert verdict.recevable is True
    assert verdict.motif is MotifRecevabilite.OK


def test_format_non_image_est_refuse():
    verdict = evaluer(_image(), b"MZ\x90\x00 un executable")
    assert verdict.recevable is False
    assert verdict.motif is MotifRecevabilite.FORMAT_REFUSE


def test_image_floue_est_refusee_avec_conseil():
    verdict = evaluer(_image(score_nettete=SEUIL_NETTETE - 1.0), _jpeg(1024, 768))
    assert verdict.recevable is False
    assert verdict.motif is MotifRecevabilite.FLOU
    assert "approchez" in verdict.conseil.lower()


def test_image_sous_exposee_est_refusee():
    verdict = evaluer(_image(luminance_moyenne=10.0), _jpeg(1024, 768))
    assert verdict.recevable is False
    assert verdict.motif is MotifRecevabilite.SOUS_EXPOSE


def test_image_sur_exposee_est_refusee_avec_conseil_de_contre_jour():
    verdict = evaluer(_image(luminance_moyenne=250.0), _jpeg(1024, 768))
    assert verdict.recevable is False
    assert verdict.motif is MotifRecevabilite.SUR_EXPOSE
    assert "soleil" in verdict.conseil.lower()


def test_image_trop_petite_est_refusee():
    petite = COTE_MIN_PX - 1
    verdict = evaluer(
        _image(largeur=petite, hauteur=petite), _jpeg(petite, petite)
    )
    assert verdict.recevable is False
    assert verdict.motif is MotifRecevabilite.TROP_PETITE


def test_les_dimensions_de_l_entete_priment_sur_celles_declarees():
    """Le client annonce 1024x768, l'en-tête dit 100x100 : on croit l'en-tête."""
    verdict = evaluer(_image(largeur=1024, hauteur=768), _jpeg(100, 100))
    assert verdict.recevable is False
    assert verdict.motif is MotifRecevabilite.TROP_PETITE
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `cd api && python -m pytest tests/test_recevabilite.py -v --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.vision'`

- [ ] **Step 3: Écrire l'implémentation**

Créer `api/app/services/vision/__init__.py` :

```python
"""Analyse visuelle des parcelles — cascade d'étages indépendants.

Étage 0 (``recevabilite``) est le seul étage livré par le chantier C1 : il ne mobilise
aucun modèle d'apprentissage. Les étages suivants (tri d'organe, localisation des
lésions, étiologie) relèvent du chantier C2.
"""

from __future__ import annotations
```

Créer `api/app/services/vision/recevabilite.py` :

```python
"""Étage 0 de la cascade de vision — recevabilité d'une image de plantation.

C'est le composant le moins spectaculaire et le plus rentable de la chaîne : sans lui,
tout l'aval analyse du bruit. Une image refusée renvoie un **conseil de reprise en
français simple**, jamais un code d'erreur.

Répartition des responsabilités (voir le plan C1, « écart assumé ») :

* Le **navigateur** calcule la netteté (variance du laplacien) et la luminance moyenne
  sur les pixels qu'il possède déjà, et refuse localement avant tout téléversement —
  ce qui économise la bande passante sur un réseau mobile faible.
* Le **serveur** valide le format et les dimensions en lisant les **en-têtes** de
  fichier, en Python pur. Ce n'est pas une redondance : on écrit ces fichiers sur
  disque, et rien ne garantit qu'un client envoie bien une image.

Les métriques déclarées par le client sont bornées, jamais crues sur parole ; les
dimensions lues dans l'en-tête **priment** sur celles annoncées.
"""

from __future__ import annotations

import struct

from app.core.logging import get_logger
from app.models.parcelle import ImageRequest, MotifRecevabilite, Recevabilite

logger = get_logger(__name__)

# Variance du laplacien en dessous de laquelle une photo de cabosse est trop floue
# pour un constat. Valeur empirique sur images de téléphone en 1024 px de large.
SEUIL_NETTETE = 60.0

# Luminance moyenne (0-255) hors de laquelle l'exposition compromet le constat.
# Le contre-jour est le défaut dominant en plantation : soleil zénithal, sous-bois.
LUMINANCE_MIN = 30.0
LUMINANCE_MAX = 225.0

# Côté minimal en pixels : en dessous, une lésion de cabosse n'est plus discernable.
COTE_MIN_PX = 320

# Plafond de taille par image, après décodage base64.
TAILLE_MAX_OCTETS = 3_000_000

_CONSEILS: dict[MotifRecevabilite, str] = {
    MotifRecevabilite.OK: "Image exploitable.",
    MotifRecevabilite.FLOU: (
        "La photo est floue. Approchez-vous de la cabosse, tenez le téléphone bien "
        "immobile, et refaites la photo."
    ),
    MotifRecevabilite.SOUS_EXPOSE: (
        "La photo est trop sombre. Sortez de l'ombre ou attendez un moment plus "
        "clair, puis refaites la photo."
    ),
    MotifRecevabilite.SUR_EXPOSE: (
        "La photo est éblouie. Tournez-vous dos au soleil pour éviter le contre-jour, "
        "puis refaites la photo."
    ),
    MotifRecevabilite.TROP_PETITE: (
        "L'image est trop petite pour être examinée. Utilisez l'appareil photo du "
        "téléphone plutôt qu'une capture d'écran."
    ),
    MotifRecevabilite.FORMAT_REFUSE: (
        "Ce fichier n'est pas une photo reconnue. Envoyez une image JPEG ou PNG."
    ),
}

_SIGNATURE_PNG = b"\x89PNG\r\n\x1a\n"

# Marqueurs JPEG « Start Of Frame » portant les dimensions. On saute SOF4 (0xC4,
# tables de Huffman) et les marqueurs de redémarrage, qui ne sont pas des SOF.
_MARQUEURS_SOF = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)


def _dimensions_png(donnees: bytes) -> tuple[int, int] | None:
    """Lit largeur et hauteur dans le bloc IHDR d'un PNG."""
    if len(donnees) < 24 or donnees[12:16] != b"IHDR":
        return None
    largeur, hauteur = struct.unpack(">II", donnees[16:24])
    return (largeur, hauteur) if largeur and hauteur else None


def _dimensions_jpeg(donnees: bytes) -> tuple[int, int] | None:
    """Parcourt les segments JPEG jusqu'au marqueur SOF portant les dimensions."""
    position = 2
    taille = len(donnees)
    while position + 3 < taille:
        if donnees[position] != 0xFF:
            position += 1
            continue
        marqueur = donnees[position + 1]
        if marqueur in _MARQUEURS_SOF:
            if position + 9 > taille:
                return None
            hauteur, largeur = struct.unpack(">HH", donnees[position + 5 : position + 9])
            return (largeur, hauteur) if largeur and hauteur else None
        longueur = struct.unpack(">H", donnees[position + 2 : position + 4])[0]
        if longueur < 2:
            return None
        position += 2 + longueur
    return None


def dimensions_depuis_entete(donnees: bytes) -> tuple[int, int] | None:
    """Extrait les dimensions d'une image depuis son en-tête, sans la décoder.

    Sert aussi de validation de format : un fichier dont on ne peut lire l'en-tête
    n'est pas une image PNG ou JPEG, et n'a rien à faire sur le disque.

    Args:
        donnees: Contenu binaire du fichier.

    Returns:
        ``(largeur, hauteur)`` en pixels, ou ``None`` si le format est inconnu.
    """
    if donnees.startswith(_SIGNATURE_PNG):
        return _dimensions_png(donnees)
    if donnees.startswith(b"\xff\xd8"):
        return _dimensions_jpeg(donnees)
    return None


def _verdict(motif: MotifRecevabilite, score_nettete: float) -> Recevabilite:
    """Assemble un verdict avec le conseil de reprise associé au motif."""
    return Recevabilite(
        recevable=motif is MotifRecevabilite.OK,
        motif=motif,
        conseil=_CONSEILS[motif],
        score_nettete=score_nettete,
    )


def evaluer(image: ImageRequest, donnees: bytes) -> Recevabilite:
    """Rend le verdict de recevabilité d'une image.

    Ordre de priorité : format, dimensions, netteté, exposition. Le format passe en
    premier parce qu'un fichier non reconnu ne doit jamais être écrit sur disque.

    Args:
        image: Métadonnées déclarées par le client (dimensions, métriques).
        donnees: Contenu binaire décodé de l'image.

    Returns:
        Le verdict, avec son motif et un conseil de reprise en français simple.
    """
    dimensions = dimensions_depuis_entete(donnees)
    if dimensions is None:
        logger.info("recevabilite_format_refuse", octets=len(donnees))
        return _verdict(MotifRecevabilite.FORMAT_REFUSE, image.score_nettete)

    largeur, hauteur = dimensions
    if min(largeur, hauteur) < COTE_MIN_PX:
        return _verdict(MotifRecevabilite.TROP_PETITE, image.score_nettete)
    if image.score_nettete < SEUIL_NETTETE:
        return _verdict(MotifRecevabilite.FLOU, image.score_nettete)
    if image.luminance_moyenne < LUMINANCE_MIN:
        return _verdict(MotifRecevabilite.SOUS_EXPOSE, image.score_nettete)
    if image.luminance_moyenne > LUMINANCE_MAX:
        return _verdict(MotifRecevabilite.SUR_EXPOSE, image.score_nettete)
    return _verdict(MotifRecevabilite.OK, image.score_nettete)
```

- [ ] **Step 4: Lancer les tests pour vérifier qu'ils passent**

Run: `cd api && python -m pytest tests/test_recevabilite.py -v --no-cov`
Expected: PASS — 10 tests

- [ ] **Step 5: Lint et commit**

```bash
cd api && python -m ruff format app/services/vision/ tests/test_recevabilite.py && python -m ruff check app/services/vision/ tests/test_recevabilite.py
cd .. && git add api/app/services/vision/ api/tests/test_recevabilite.py
git commit -m "feat(vision): etage 0 - recevabilite d image sans modele ni dependance

Format et dimensions lus dans les en-tetes PNG/JPEG en Python pur (validation de
securite : on ecrit ces fichiers sur disque). Nettete et exposition calculees par
le navigateur, bornees ici sans etre crues sur parole. Refus = conseil de reprise
en francais simple, jamais un code d erreur."
```

---

## Task 4: Port et dépôt SQLite des parcelles

**Files:**
- Create: `api/app/core/parcelles_store.py`
- Modify: `api/app/domain/ports.py` (ajout de `ParcelleStorePort` en fin de fichier, et de l'import `TYPE_CHECKING`)
- Test: `api/tests/test_parcelles_store.py`

**Interfaces:**
- Consomme : les types de domaine de la Tâche 2.
- Produit — méthodes de `ParcelleStore` (toutes `async`) :
  - `from_settings(settings: Settings) -> ParcelleStore`
  - `pret: bool` (propriété)
  - `initialiser() -> None`
  - `creer_parcelle(proprietaire, nom, localite, direction_regionale) -> Parcelle`
  - `obtenir_parcelle(identifiant, proprietaire) -> Parcelle | None`
  - `lister_parcelles(proprietaire, limite=50) -> list[Parcelle]`
  - `enregistrer_geometrie(identifiant, proprietaire, geometrie) -> Parcelle | None`
  - `enregistrer_capture(capture: Capture) -> Capture`
  - `obtenir_capture(identifiant, proprietaire) -> Capture | None`
  - `lister_captures(parcelle, proprietaire) -> list[Capture]`
  - `purger_captures(avant: datetime) -> list[str]` — retourne les empreintes à supprimer du disque

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `api/tests/test_parcelles_store.py` :

```python
"""Tests du dépôt SQLite des parcelles et de leurs captures."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core.parcelles_store import ParcelleStore
from app.models.parcelle import (
    Capture,
    Coordonnee,
    Geometrie,
    Image,
    Modalite,
    MotifRecevabilite,
    Recevabilite,
    SourceGeometrie,
)

DEVICE = "appareil-a"
AUTRE_DEVICE = "appareil-b"


@pytest.fixture
async def store(tmp_path: Path) -> ParcelleStore:
    depot = ParcelleStore(tmp_path / "parcelles.db", captures_retention_jours=90)
    await depot.initialiser()
    return depot


def _recevabilite() -> Recevabilite:
    return Recevabilite(
        recevable=True,
        motif=MotifRecevabilite.OK,
        conseil="Image exploitable.",
        score_nettete=400.0,
    )


async def test_initialiser_rend_le_depot_pret(store: ParcelleStore):
    assert store.pret is True


async def test_initialisation_tolere_un_chemin_inaccessible(tmp_path: Path):
    """Si /data est cassé, le dépôt n'est pas prêt mais ne lève jamais."""
    impasse = tmp_path / "fichier"
    impasse.write_text("je ne suis pas un dossier", encoding="utf-8")
    depot = ParcelleStore(impasse / "parcelles.db")
    await depot.initialiser()
    assert depot.pret is False


async def test_creer_puis_relire_une_parcelle(store: ParcelleStore):
    parcelle = await store.creer_parcelle(DEVICE, "Bloc Est", "Daloa", "Daloa")
    relue = await store.obtenir_parcelle(parcelle.identifiant, DEVICE)
    assert relue is not None
    assert relue.nom == "Bloc Est"
    assert relue.localite == "Daloa"


async def test_un_autre_appareil_ne_voit_pas_la_parcelle(store: ParcelleStore):
    parcelle = await store.creer_parcelle(DEVICE, "Bloc Est", "Daloa", "Daloa")
    assert await store.obtenir_parcelle(parcelle.identifiant, AUTRE_DEVICE) is None


async def test_lister_ne_rend_que_les_parcelles_de_l_appareil(store: ParcelleStore):
    await store.creer_parcelle(DEVICE, "Bloc Est", "Daloa", "Daloa")
    await store.creer_parcelle(AUTRE_DEVICE, "Bloc Ouest", "Soubré", "Soubré")
    assert [p.nom for p in await store.lister_parcelles(DEVICE)] == ["Bloc Est"]


async def test_enregistrer_une_geometrie_conserve_la_superficie(store: ParcelleStore):
    parcelle = await store.creer_parcelle(DEVICE, "Bloc Est", "Daloa", "Daloa")
    cote = 0.000899
    points = tuple(
        Coordonnee(latitude=a, longitude=b)
        for a, b in [
            (6.85, -5.28),
            (6.85, -5.28 + cote),
            (6.85 + cote, -5.28 + cote),
            (6.85 + cote, -5.28),
        ]
    )
    geometrie = Geometrie.depuis_points(points, SourceGeometrie.PARCOURS_GPS)
    maj = await store.enregistrer_geometrie(parcelle.identifiant, DEVICE, geometrie)
    assert maj is not None
    assert maj.geometrie is not None
    assert maj.geometrie.superficie_ha == pytest.approx(1.0, abs=0.05)
    assert len(maj.geometrie.points) == 4


async def test_enregistrer_une_geometrie_sur_une_parcelle_absente_rend_none(
    store: ParcelleStore,
):
    geometrie = Geometrie.depuis_points(
        (Coordonnee(latitude=6.85, longitude=-5.28),), SourceGeometrie.SAISIE_MANUELLE
    )
    assert await store.enregistrer_geometrie("inexistante", DEVICE, geometrie) is None


async def test_enregistrer_puis_relire_une_capture(store: ParcelleStore):
    parcelle = await store.creer_parcelle(DEVICE, "Bloc Est", "Daloa", "Daloa")
    capture = Capture(
        identifiant="cap-1",
        parcelle=parcelle.identifiant,
        proprietaire=DEVICE,
        modalite=Modalite.PHOTOS,
        cree_le=datetime.now(UTC),
        images=(
            Image(
                empreinte_sha256="a" * 64,
                largeur=1024,
                hauteur=768,
                recevabilite=_recevabilite(),
                coordonnee=Coordonnee(latitude=6.85, longitude=-5.28),
            ),
        ),
    )
    await store.enregistrer_capture(capture)
    relue = await store.obtenir_capture("cap-1", DEVICE)
    assert relue is not None
    assert relue.modalite is Modalite.PHOTOS
    assert len(relue.images) == 1
    assert relue.images[0].empreinte_sha256 == "a" * 64
    assert relue.images[0].recevabilite.motif is MotifRecevabilite.OK


async def test_capture_avec_trace_conserve_l_ordre_des_points(store: ParcelleStore):
    parcelle = await store.creer_parcelle(DEVICE, "Bloc Est", "Daloa", "Daloa")
    trace = tuple(
        Coordonnee(latitude=6.85 + i * 0.001, longitude=-5.28) for i in range(5)
    )
    capture = Capture(
        identifiant="cap-2",
        parcelle=parcelle.identifiant,
        proprietaire=DEVICE,
        modalite=Modalite.PARCOURS,
        cree_le=datetime.now(UTC),
        trace=trace,
    )
    await store.enregistrer_capture(capture)
    relue = await store.obtenir_capture("cap-2", DEVICE)
    assert relue is not None
    assert [round(p.latitude, 3) for p in relue.trace] == [6.850, 6.851, 6.852, 6.853, 6.854]


async def test_lister_les_captures_d_une_parcelle(store: ParcelleStore):
    parcelle = await store.creer_parcelle(DEVICE, "Bloc Est", "Daloa", "Daloa")
    for indice in range(3):
        await store.enregistrer_capture(
            Capture(
                identifiant=f"cap-{indice}",
                parcelle=parcelle.identifiant,
                proprietaire=DEVICE,
                modalite=Modalite.PHOTOS,
                cree_le=datetime.now(UTC),
                images=(
                    Image(
                        empreinte_sha256=str(indice) * 64,
                        largeur=1024,
                        hauteur=768,
                        recevabilite=_recevabilite(),
                    ),
                ),
            )
        )
    assert len(await store.lister_captures(parcelle.identifiant, DEVICE)) == 3


async def test_purger_rend_les_empreintes_des_captures_expirees(store: ParcelleStore):
    parcelle = await store.creer_parcelle(DEVICE, "Bloc Est", "Daloa", "Daloa")
    ancienne = datetime.now(UTC) - timedelta(days=200)
    await store.enregistrer_capture(
        Capture(
            identifiant="cap-vieille",
            parcelle=parcelle.identifiant,
            proprietaire=DEVICE,
            modalite=Modalite.PHOTOS,
            cree_le=ancienne,
            images=(
                Image(
                    empreinte_sha256="f" * 64,
                    largeur=1024,
                    hauteur=768,
                    recevabilite=_recevabilite(),
                ),
            ),
        )
    )
    empreintes = await store.purger_captures(datetime.now(UTC) - timedelta(days=90))
    assert empreintes == ["f" * 64]
    assert await store.obtenir_capture("cap-vieille", DEVICE) is None


async def test_les_operations_sur_un_depot_non_pret_ne_levent_pas(tmp_path: Path):
    depot = ParcelleStore(tmp_path / "jamais-initialise.db")
    assert await depot.lister_parcelles(DEVICE) == []
    assert await depot.obtenir_parcelle("x", DEVICE) is None
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `cd api && python -m pytest tests/test_parcelles_store.py -v --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.core.parcelles_store'`

- [ ] **Step 3: Ajouter le port au domaine**

Dans `api/app/domain/ports.py`, étendre le bloc `TYPE_CHECKING` existant :

```python
if TYPE_CHECKING:
    from app.models.parcelle import Capture, Geometrie, Parcelle
    from app.models.session import ConversationMessage, Session, SessionAvecMessages
```

Puis ajouter en fin de fichier :

```python
@runtime_checkable
class ParcelleStorePort(Protocol):
    """Contrat d'un dépôt de parcelles et de leurs captures terrain."""

    @property
    def pret(self) -> bool:
        """Indique si le schéma a pu être initialisé (parcelles disponibles)."""
        ...

    async def creer_parcelle(
        self, proprietaire: str, nom: str, localite: str, direction_regionale: str
    ) -> Parcelle:
        """Crée une parcelle rattachée à un appareil."""
        ...

    async def obtenir_parcelle(self, identifiant: str, proprietaire: str) -> Parcelle | None:
        """Retourne une parcelle de cet appareil, ou None."""
        ...

    async def lister_parcelles(self, proprietaire: str, limite: int = ...) -> list[Parcelle]:
        """Liste les parcelles de cet appareil, les plus récemment modifiées d'abord."""
        ...

    async def enregistrer_geometrie(
        self, identifiant: str, proprietaire: str, geometrie: Geometrie
    ) -> Parcelle | None:
        """Remplace la géométrie d'une parcelle. None si elle n'existe pas."""
        ...

    async def enregistrer_capture(self, capture: Capture) -> Capture:
        """Persiste une capture (images et/ou trace)."""
        ...

    async def obtenir_capture(self, identifiant: str, proprietaire: str) -> Capture | None:
        """Retourne une capture de cet appareil, ou None."""
        ...

    async def lister_captures(self, parcelle: str, proprietaire: str) -> list[Capture]:
        """Liste les captures d'une parcelle, les plus récentes d'abord."""
        ...

    async def purger_captures(self, avant: datetime) -> list[str]:
        """Supprime les captures antérieures et retourne les empreintes à effacer."""
        ...
```

Ajouter l'import nécessaire en tête de `ports.py` (hors `TYPE_CHECKING`, car utilisé dans une signature évaluée à l'exécution par `runtime_checkable`) :

```python
from datetime import datetime
```

- [ ] **Step 4: Écrire le dépôt**

Créer `api/app/core/parcelles_store.py` :

```python
"""Stockage durable des parcelles et de leurs captures terrain (SQLite, stdlib).

Même choix de conception que :mod:`app.core.sessions` et :mod:`app.core.auth_store` —
``sqlite3`` de la bibliothèque standard (aucune dépendance hors spec §2.1), fichier sur
le volume ``/data``, migrations versionnées par ``PRAGMA user_version``, accès
asynchrone par ``asyncio.to_thread``, écritures sérialisées par un verrou applicatif,
mode WAL pour les lectures concurrentes.

**Initialisation tolérante aux pannes** : si le fichier ne peut être ouvert, le service
démarre quand même et les parcelles sont marquées indisponibles. Le chat ne doit jamais
tomber à cause des parcelles.

Les **images ne sont pas en base** : seule leur empreinte SHA-256 et leurs métadonnées
le sont. Les octets vivent sur le disque, écrits par ``services/parcelles.py``. La purge
retourne les empreintes devenues inutiles, à charge de l'appelant d'effacer les fichiers.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.core.config import Settings
from app.core.logging import get_logger
from app.models.parcelle import (
    Capture,
    Coordonnee,
    Geometrie,
    Image,
    Modalite,
    MotifRecevabilite,
    Parcelle,
    Recevabilite,
    SourceGeometrie,
    TypeGeometrie,
)

logger = get_logger(__name__)

RETENTION_JOURS_DEFAUT = 90


class ParcelleStore:
    """Dépôt SQLite des parcelles cacaoyères et de leurs captures."""

    # Migrations ordonnées : l'indice (0-based) + 1 devient le ``user_version``.
    # Pour faire évoluer le schéma, AJOUTER une entrée à la fin — ne jamais modifier
    # une migration déjà publiée.
    _MIGRATIONS: tuple[str, ...] = (
        """
        CREATE TABLE IF NOT EXISTS parcelles (
            id                  TEXT PRIMARY KEY,
            proprietaire        TEXT NOT NULL,
            nom                 TEXT NOT NULL,
            localite            TEXT NOT NULL,
            direction_regionale TEXT NOT NULL,
            geometrie_json      TEXT,
            cree_le             TEXT NOT NULL,
            maj_le              TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS captures (
            id           TEXT PRIMARY KEY,
            parcelle_id  TEXT NOT NULL REFERENCES parcelles(id) ON DELETE CASCADE,
            proprietaire TEXT NOT NULL,
            modalite     TEXT NOT NULL,
            images_json  TEXT NOT NULL,
            trace_json   TEXT NOT NULL,
            cree_le      TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_parcelles_proprio
            ON parcelles(proprietaire, maj_le DESC);
        CREATE INDEX IF NOT EXISTS idx_captures_parcelle
            ON captures(parcelle_id, cree_le DESC);
        CREATE INDEX IF NOT EXISTS idx_captures_cree
            ON captures(cree_le);
        """,
    )

    def __init__(
        self, chemin: Path, captures_retention_jours: int = RETENTION_JOURS_DEFAUT
    ) -> None:
        """Initialise le dépôt.

        Args:
            chemin: Chemin du fichier SQLite (créé si besoin).
            captures_retention_jours: Rétention des captures, en jours.
        """
        self._chemin = chemin
        self._retention_jours = captures_retention_jours
        self._verrou = asyncio.Lock()
        self._pret = False

    @classmethod
    def from_settings(cls, settings: Settings) -> ParcelleStore:
        """Construit un dépôt à partir des paramètres applicatifs."""
        return cls(
            Path(settings.parcelles_db_path),
            captures_retention_jours=settings.captures_retention_jours,
        )

    @property
    def pret(self) -> bool:
        """Indique si le schéma a pu être initialisé (parcelles disponibles)."""
        return self._pret

    @property
    def retention_jours(self) -> int:
        """Rétention des captures, en jours."""
        return self._retention_jours

    async def initialiser(self) -> None:
        """Crée/migre le schéma. Tolérant aux pannes : ne lève jamais au démarrage."""
        try:
            await asyncio.to_thread(self._migrer)
            self._pret = True
            logger.info("parcelles_pretes", chemin=str(self._chemin))
        except (sqlite3.Error, OSError) as exc:
            self._pret = False
            logger.warning("parcelles_init_echouee", chemin=str(self._chemin), error=str(exc))

    # ------------------------------------------------------------------ schéma

    def _connexion(self) -> sqlite3.Connection:
        """Ouvre une connexion configurée (WAL, clés étrangères)."""
        connexion = sqlite3.connect(self._chemin, timeout=10.0)
        connexion.row_factory = sqlite3.Row
        connexion.execute("PRAGMA journal_mode=WAL")
        connexion.execute("PRAGMA foreign_keys=ON")
        return connexion

    def _migrer(self) -> None:
        """Applique les migrations manquantes, en une transaction par migration."""
        self._chemin.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connexion()) as connexion:
            version = connexion.execute("PRAGMA user_version").fetchone()[0]
            for indice in range(version, len(self._MIGRATIONS)):
                connexion.executescript(self._MIGRATIONS[indice])
                connexion.execute(f"PRAGMA user_version = {indice + 1}")
                connexion.commit()

    # ------------------------------------------------------------ sérialisation

    @staticmethod
    def _coordonnee_en_dict(point: Coordonnee) -> dict[str, object]:
        """Sérialise un point en dictionnaire JSON-compatible."""
        return {
            "latitude": point.latitude,
            "longitude": point.longitude,
            "precision_m": point.precision_m,
            "horodatage": point.horodatage.isoformat() if point.horodatage else None,
        }

    @staticmethod
    def _coordonnee_depuis_dict(brut: dict[str, object]) -> Coordonnee:
        """Reconstruit un point depuis son dictionnaire."""
        horodatage = brut.get("horodatage")
        return Coordonnee(
            latitude=float(brut["latitude"]),  # type: ignore[arg-type]
            longitude=float(brut["longitude"]),  # type: ignore[arg-type]
            precision_m=(
                float(brut["precision_m"]) if brut.get("precision_m") is not None else None  # type: ignore[arg-type]
            ),
            horodatage=datetime.fromisoformat(str(horodatage)) if horodatage else None,
        )

    @classmethod
    def _geometrie_en_json(cls, geometrie: Geometrie) -> str:
        """Sérialise une géométrie."""
        return json.dumps(
            {
                "type": geometrie.type.value,
                "source": geometrie.source.value,
                "superficie_ha": geometrie.superficie_ha,
                "points": [cls._coordonnee_en_dict(p) for p in geometrie.points],
            }
        )

    @classmethod
    def _geometrie_depuis_json(cls, brut: str | None) -> Geometrie | None:
        """Reconstruit une géométrie, ou None si la colonne est vide."""
        if not brut:
            return None
        charge = json.loads(brut)
        return Geometrie(
            type=TypeGeometrie(charge["type"]),
            source=SourceGeometrie(charge["source"]),
            superficie_ha=charge.get("superficie_ha"),
            points=tuple(cls._coordonnee_depuis_dict(p) for p in charge["points"]),
        )

    @classmethod
    def _images_en_json(cls, images: tuple[Image, ...]) -> str:
        """Sérialise les images d'une capture (métadonnées seules)."""
        return json.dumps(
            [
                {
                    "empreinte_sha256": image.empreinte_sha256,
                    "largeur": image.largeur,
                    "hauteur": image.hauteur,
                    "recevabilite": {
                        "recevable": image.recevabilite.recevable,
                        "motif": image.recevabilite.motif.value,
                        "conseil": image.recevabilite.conseil,
                        "score_nettete": image.recevabilite.score_nettete,
                    },
                    "coordonnee": (
                        cls._coordonnee_en_dict(image.coordonnee) if image.coordonnee else None
                    ),
                }
                for image in images
            ]
        )

    @classmethod
    def _images_depuis_json(cls, brut: str) -> tuple[Image, ...]:
        """Reconstruit les images d'une capture."""
        return tuple(
            Image(
                empreinte_sha256=charge["empreinte_sha256"],
                largeur=int(charge["largeur"]),
                hauteur=int(charge["hauteur"]),
                recevabilite=Recevabilite(
                    recevable=bool(charge["recevabilite"]["recevable"]),
                    motif=MotifRecevabilite(charge["recevabilite"]["motif"]),
                    conseil=charge["recevabilite"]["conseil"],
                    score_nettete=float(charge["recevabilite"]["score_nettete"]),
                ),
                coordonnee=(
                    cls._coordonnee_depuis_dict(charge["coordonnee"])
                    if charge.get("coordonnee")
                    else None
                ),
            )
            for charge in json.loads(brut)
        )

    @classmethod
    def _ligne_en_parcelle(cls, ligne: sqlite3.Row) -> Parcelle:
        """Reconstruit une parcelle depuis une ligne SQL."""
        return Parcelle(
            identifiant=ligne["id"],
            proprietaire=ligne["proprietaire"],
            nom=ligne["nom"],
            localite=ligne["localite"],
            direction_regionale=ligne["direction_regionale"],
            geometrie=cls._geometrie_depuis_json(ligne["geometrie_json"]),
            cree_le=datetime.fromisoformat(ligne["cree_le"]),
            maj_le=datetime.fromisoformat(ligne["maj_le"]),
        )

    @classmethod
    def _ligne_en_capture(cls, ligne: sqlite3.Row) -> Capture:
        """Reconstruit une capture depuis une ligne SQL."""
        return Capture(
            identifiant=ligne["id"],
            parcelle=ligne["parcelle_id"],
            proprietaire=ligne["proprietaire"],
            modalite=Modalite(ligne["modalite"]),
            cree_le=datetime.fromisoformat(ligne["cree_le"]),
            images=cls._images_depuis_json(ligne["images_json"]),
            trace=tuple(
                cls._coordonnee_depuis_dict(p) for p in json.loads(ligne["trace_json"])
            ),
        )

    # -------------------------------------------------------------------- CRUD

    async def creer_parcelle(
        self, proprietaire: str, nom: str, localite: str, direction_regionale: str
    ) -> Parcelle:
        """Crée une parcelle rattachée à un appareil.

        Args:
            proprietaire: Identifiant anonyme de l'appareil.
            nom: Libellé donné par le producteur.
            localite: Localité déclarée.
            direction_regionale: Direction régionale ANADER de rattachement.

        Returns:
            La parcelle créée.
        """
        maintenant = datetime.now(UTC)
        parcelle = Parcelle(
            identifiant=uuid4().hex,
            proprietaire=proprietaire,
            nom=nom,
            localite=localite,
            direction_regionale=direction_regionale,
            cree_le=maintenant,
            maj_le=maintenant,
        )
        if not self._pret:
            return parcelle
        async with self._verrou:
            await asyncio.to_thread(self._inserer_parcelle, parcelle)
        return parcelle

    def _inserer_parcelle(self, parcelle: Parcelle) -> None:
        """Insère une parcelle (appelé dans un thread)."""
        with closing(self._connexion()) as connexion:
            connexion.execute(
                "INSERT INTO parcelles (id, proprietaire, nom, localite, "
                "direction_regionale, geometrie_json, cree_le, maj_le) "
                "VALUES (?, ?, ?, ?, ?, NULL, ?, ?)",
                (
                    parcelle.identifiant,
                    parcelle.proprietaire,
                    parcelle.nom,
                    parcelle.localite,
                    parcelle.direction_regionale,
                    parcelle.cree_le.isoformat(),
                    parcelle.maj_le.isoformat(),
                ),
            )
            connexion.commit()

    async def obtenir_parcelle(self, identifiant: str, proprietaire: str) -> Parcelle | None:
        """Retourne une parcelle de cet appareil, ou None."""
        if not self._pret:
            return None
        return await asyncio.to_thread(self._lire_parcelle, identifiant, proprietaire)

    def _lire_parcelle(self, identifiant: str, proprietaire: str) -> Parcelle | None:
        """Lit une parcelle (appelé dans un thread)."""
        with closing(self._connexion()) as connexion:
            ligne = connexion.execute(
                "SELECT * FROM parcelles WHERE id = ? AND proprietaire = ?",
                (identifiant, proprietaire),
            ).fetchone()
        return self._ligne_en_parcelle(ligne) if ligne else None

    async def lister_parcelles(self, proprietaire: str, limite: int = 50) -> list[Parcelle]:
        """Liste les parcelles de cet appareil, les plus récemment modifiées d'abord."""
        if not self._pret:
            return []
        return await asyncio.to_thread(self._lire_parcelles, proprietaire, limite)

    def _lire_parcelles(self, proprietaire: str, limite: int) -> list[Parcelle]:
        """Lit les parcelles d'un appareil (appelé dans un thread)."""
        with closing(self._connexion()) as connexion:
            lignes = connexion.execute(
                "SELECT * FROM parcelles WHERE proprietaire = ? "
                "ORDER BY maj_le DESC LIMIT ?",
                (proprietaire, limite),
            ).fetchall()
        return [self._ligne_en_parcelle(ligne) for ligne in lignes]

    async def enregistrer_geometrie(
        self, identifiant: str, proprietaire: str, geometrie: Geometrie
    ) -> Parcelle | None:
        """Remplace la géométrie d'une parcelle. None si elle n'existe pas."""
        if not self._pret:
            return None
        async with self._verrou:
            await asyncio.to_thread(
                self._ecrire_geometrie, identifiant, proprietaire, geometrie
            )
        return await self.obtenir_parcelle(identifiant, proprietaire)

    def _ecrire_geometrie(
        self, identifiant: str, proprietaire: str, geometrie: Geometrie
    ) -> None:
        """Écrit la géométrie (appelé dans un thread)."""
        with closing(self._connexion()) as connexion:
            connexion.execute(
                "UPDATE parcelles SET geometrie_json = ?, maj_le = ? "
                "WHERE id = ? AND proprietaire = ?",
                (
                    self._geometrie_en_json(geometrie),
                    datetime.now(UTC).isoformat(),
                    identifiant,
                    proprietaire,
                ),
            )
            connexion.commit()

    async def enregistrer_capture(self, capture: Capture) -> Capture:
        """Persiste une capture (images et/ou trace)."""
        if not self._pret:
            return capture
        async with self._verrou:
            await asyncio.to_thread(self._inserer_capture, capture)
        return capture

    def _inserer_capture(self, capture: Capture) -> None:
        """Insère une capture (appelé dans un thread)."""
        with closing(self._connexion()) as connexion:
            connexion.execute(
                "INSERT INTO captures (id, parcelle_id, proprietaire, modalite, "
                "images_json, trace_json, cree_le) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    capture.identifiant,
                    capture.parcelle,
                    capture.proprietaire,
                    capture.modalite.value,
                    self._images_en_json(capture.images),
                    json.dumps([self._coordonnee_en_dict(p) for p in capture.trace]),
                    capture.cree_le.isoformat(),
                ),
            )
            connexion.execute(
                "UPDATE parcelles SET maj_le = ? WHERE id = ?",
                (datetime.now(UTC).isoformat(), capture.parcelle),
            )
            connexion.commit()

    async def obtenir_capture(self, identifiant: str, proprietaire: str) -> Capture | None:
        """Retourne une capture de cet appareil, ou None."""
        if not self._pret:
            return None
        return await asyncio.to_thread(self._lire_capture, identifiant, proprietaire)

    def _lire_capture(self, identifiant: str, proprietaire: str) -> Capture | None:
        """Lit une capture (appelé dans un thread)."""
        with closing(self._connexion()) as connexion:
            ligne = connexion.execute(
                "SELECT * FROM captures WHERE id = ? AND proprietaire = ?",
                (identifiant, proprietaire),
            ).fetchone()
        return self._ligne_en_capture(ligne) if ligne else None

    async def lister_captures(self, parcelle: str, proprietaire: str) -> list[Capture]:
        """Liste les captures d'une parcelle, les plus récentes d'abord."""
        if not self._pret:
            return []
        return await asyncio.to_thread(self._lire_captures, parcelle, proprietaire)

    def _lire_captures(self, parcelle: str, proprietaire: str) -> list[Capture]:
        """Lit les captures d'une parcelle (appelé dans un thread)."""
        with closing(self._connexion()) as connexion:
            lignes = connexion.execute(
                "SELECT * FROM captures WHERE parcelle_id = ? AND proprietaire = ? "
                "ORDER BY cree_le DESC",
                (parcelle, proprietaire),
            ).fetchall()
        return [self._ligne_en_capture(ligne) for ligne in lignes]

    async def purger_captures(self, avant: datetime) -> list[str]:
        """Supprime les captures antérieures à une date et rend les empreintes.

        Les fichiers d'images ne sont pas effacés ici : la base ne connaît pas le
        disque. L'appelant (``services/parcelles.py``) supprime les fichiers dont les
        empreintes sont retournées.

        Args:
            avant: Date limite ; toute capture plus ancienne est supprimée.

        Returns:
            Les empreintes SHA-256 des images devenues orphelines.
        """
        if not self._pret:
            return []
        async with self._verrou:
            return await asyncio.to_thread(self._purger, avant)

    def _purger(self, avant: datetime) -> list[str]:
        """Purge et collecte les empreintes (appelé dans un thread)."""
        limite = avant.isoformat()
        with closing(self._connexion()) as connexion:
            lignes = connexion.execute(
                "SELECT images_json FROM captures WHERE cree_le < ?", (limite,)
            ).fetchall()
            empreintes = [
                charge["empreinte_sha256"]
                for ligne in lignes
                for charge in json.loads(ligne["images_json"])
            ]
            connexion.execute("DELETE FROM captures WHERE cree_le < ?", (limite,))
            connexion.commit()
        if empreintes:
            logger.info("captures_purgees", nombre=len(empreintes))
        return empreintes
```

- [ ] **Step 5: Lancer les tests pour vérifier qu'ils passent**

Run: `cd api && python -m pytest tests/test_parcelles_store.py -v --no-cov`
Expected: PASS — 12 tests

> Si les tests échouent sur `settings.parcelles_db_path` inexistant, c'est normal : `from_settings` n'est pas testé ici (il l'est en Tâche 6, après l'ajout des réglages). Les tests construisent `ParcelleStore(chemin)` directement.

- [ ] **Step 6: Lint et commit**

```bash
cd api && python -m ruff format app/core/parcelles_store.py app/domain/ports.py tests/test_parcelles_store.py && python -m ruff check app/core/parcelles_store.py app/domain/ports.py tests/test_parcelles_store.py
cd .. && git add api/app/core/parcelles_store.py api/app/domain/ports.py api/tests/test_parcelles_store.py
git commit -m "feat(parcelle): depot SQLite et port de domaine

Moule sessions.py : sqlite3 stdlib, migrations par user_version, asyncio.to_thread,
WAL, initialisation toleree en panne. Les images ne sont pas en base : seule leur
empreinte l est. La purge rend les empreintes orphelines, sans toucher au disque."
```

---

## Task 5: Service métier des parcelles

Le service porte les décisions : validation géographique, décodage base64, écriture disque, verdict de recevabilité, rattachement à la direction régionale.

**Files:**
- Create: `api/app/services/parcelles.py`
- Test: `api/tests/test_service_parcelles.py`

**Interfaces:**
- Consomme : `ParcelleStorePort` (T4), `evaluer` (T3), types et schémas (T2), `dans_cote_ivoire` / `anneau_auto_intersecte` (T1).
- Consomme, **vérifié dans le code existant** : `app.services.contacts.chercher(texte: str) -> ContactDR | None`, dont l'attribut `.nom` porte le libellé de la direction régionale (par exemple `"Direction Régionale Centre-Ouest"` pour Daloa). Ne **pas** créer de nouvelle fonction de rattachement : celle-ci existe et sert déjà à la mise en relation ANADER.
- Produit — `ServiceParcelles` :
  - `__init__(store: ParcelleStorePort, dossier_captures: Path, retention_jours: int = 90, taille_max_octets: int = TAILLE_MAX_OCTETS)`
  - `creer(proprietaire: str, requete: CreerParcelleRequest) -> Parcelle`
  - `lister(proprietaire: str) -> list[Parcelle]`
  - `obtenir(identifiant: str, proprietaire: str) -> Parcelle | None`
  - `obtenir_capture(identifiant: str, proprietaire: str) -> Capture | None`
  - `enregistrer_geometrie(identifiant, proprietaire, requete: GeometrieRequest) -> Parcelle`
  - `deposer_capture(identifiant, proprietaire, requete: CaptureRequest) -> Capture`
  - `purger(maintenant: datetime | None = None) -> int`
  - Exception `GeometrieInvalide(Exception)` avec attribut `motif: str`
  - Exception `ParcelleIntrouvable(Exception)`

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `api/tests/test_service_parcelles.py` :

```python
"""Tests du service métier des parcelles."""

from __future__ import annotations

import base64
import struct
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core.parcelles_store import ParcelleStore
from app.models.parcelle import (
    CaptureRequest,
    CoordonneeRequest,
    CreerParcelleRequest,
    GeometrieRequest,
    ImageRequest,
    Modalite,
    MotifRecevabilite,
    SourceGeometrie,
)
from app.services.parcelles import (
    GeometrieInvalide,
    ParcelleIntrouvable,
    ServiceParcelles,
)

DEVICE = "appareil-a"


def _jpeg(largeur: int = 1024, hauteur: int = 768) -> bytes:
    """En-tête JPEG minimal mais valide."""
    soi = b"\xff\xd8"
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
    sof0 = (
        b"\xff\xc0"
        + struct.pack(">H", 17)
        + b"\x08"
        + struct.pack(">HH", hauteur, largeur)
        + b"\x03"
        + b"\x00" * 9
    )
    return soi + app0 + sof0


def _image_request(**surcharges) -> ImageRequest:
    defauts = {
        "contenu_base64": base64.b64encode(_jpeg()).decode("ascii"),
        "largeur": 1024,
        "hauteur": 768,
        "score_nettete": 400.0,
        "luminance_moyenne": 128.0,
    }
    return ImageRequest(**{**defauts, **surcharges})


def _carre() -> list[CoordonneeRequest]:
    cote = 0.000899
    lat, lon = 6.85, -5.28
    return [
        CoordonneeRequest(latitude=a, longitude=b)
        for a, b in [
            (lat, lon),
            (lat, lon + cote),
            (lat + cote, lon + cote),
            (lat + cote, lon),
        ]
    ]


@pytest.fixture
async def service(tmp_path: Path) -> ServiceParcelles:
    store = ParcelleStore(tmp_path / "parcelles.db")
    await store.initialiser()
    return ServiceParcelles(store, dossier_captures=tmp_path / "captures")


async def test_creer_rattache_la_direction_regionale(service: ServiceParcelles):
    parcelle = await service.creer(DEVICE, CreerParcelleRequest(nom="Bloc Est", localite="Daloa"))
    assert parcelle.direction_regionale


async def test_enregistrer_un_carre_calcule_un_hectare(service: ServiceParcelles):
    parcelle = await service.creer(DEVICE, CreerParcelleRequest(nom="Bloc", localite="Daloa"))
    maj = await service.enregistrer_geometrie(
        parcelle.identifiant,
        DEVICE,
        GeometrieRequest(points=_carre(), source=SourceGeometrie.PARCOURS_GPS),
    )
    assert maj.geometrie is not None
    assert maj.geometrie.superficie_ha == pytest.approx(1.0, abs=0.05)


async def test_geometrie_hors_cote_d_ivoire_refusee(service: ServiceParcelles):
    parcelle = await service.creer(DEVICE, CreerParcelleRequest(nom="Bloc", localite="Daloa"))
    paris = [
        CoordonneeRequest(latitude=48.86 + i * 0.001, longitude=2.35 + j * 0.001)
        for i, j in [(0, 0), (0, 1), (1, 1), (1, 0)]
    ]
    with pytest.raises(GeometrieInvalide) as info:
        await service.enregistrer_geometrie(
            parcelle.identifiant, DEVICE, GeometrieRequest(points=paris)
        )
    assert "Côte d'Ivoire" in info.value.motif


async def test_geometrie_auto_intersectee_refusee(service: ServiceParcelles):
    parcelle = await service.creer(DEVICE, CreerParcelleRequest(nom="Bloc", localite="Daloa"))
    huit = [
        CoordonneeRequest(latitude=a, longitude=b)
        for a, b in [(6.85, -5.28), (6.86, -5.27), (6.85, -5.27), (6.86, -5.28)]
    ]
    with pytest.raises(GeometrieInvalide) as info:
        await service.enregistrer_geometrie(
            parcelle.identifiant, DEVICE, GeometrieRequest(points=huit)
        )
    assert "coupe" in info.value.motif.lower()


async def test_superficie_absurde_refusee(service: ServiceParcelles):
    """Un anneau de plusieurs milliers d'hectares n'est pas une parcelle."""
    parcelle = await service.creer(DEVICE, CreerParcelleRequest(nom="Bloc", localite="Daloa"))
    enorme = [
        CoordonneeRequest(latitude=a, longitude=b)
        for a, b in [(6.0, -6.0), (6.0, -5.0), (7.0, -5.0), (7.0, -6.0)]
    ]
    with pytest.raises(GeometrieInvalide) as info:
        await service.enregistrer_geometrie(
            parcelle.identifiant, DEVICE, GeometrieRequest(points=enorme)
        )
    assert "superficie" in info.value.motif.lower()


async def test_geometrie_sur_parcelle_inconnue_leve(service: ServiceParcelles):
    with pytest.raises(ParcelleIntrouvable):
        await service.enregistrer_geometrie(
            "inexistante", DEVICE, GeometrieRequest(points=_carre())
        )


async def test_deposer_une_photo_ecrit_le_fichier_sur_disque(
    service: ServiceParcelles, tmp_path: Path
):
    parcelle = await service.creer(DEVICE, CreerParcelleRequest(nom="Bloc", localite="Daloa"))
    capture = await service.deposer_capture(
        parcelle.identifiant,
        DEVICE,
        CaptureRequest(modalite=Modalite.PHOTOS, images=[_image_request()]),
    )
    assert len(capture.images) == 1
    empreinte = capture.images[0].empreinte_sha256
    assert (tmp_path / "captures" / f"{empreinte}.bin").exists()


async def test_le_nom_de_fichier_vient_de_l_empreinte_jamais_du_client(
    service: ServiceParcelles, tmp_path: Path
):
    """Aucune traversée de chemin possible : le client ne nomme pas le fichier."""
    parcelle = await service.creer(DEVICE, CreerParcelleRequest(nom="Bloc", localite="Daloa"))
    capture = await service.deposer_capture(
        parcelle.identifiant,
        DEVICE,
        CaptureRequest(modalite=Modalite.PHOTOS, images=[_image_request()]),
    )
    empreinte = capture.images[0].empreinte_sha256
    assert len(empreinte) == 64
    fichiers = list((tmp_path / "captures").iterdir())
    assert [f.name for f in fichiers] == [f"{empreinte}.bin"]


async def test_image_floue_persistee_avec_son_verdict(service: ServiceParcelles):
    parcelle = await service.creer(DEVICE, CreerParcelleRequest(nom="Bloc", localite="Daloa"))
    capture = await service.deposer_capture(
        parcelle.identifiant,
        DEVICE,
        CaptureRequest(modalite=Modalite.PHOTOS, images=[_image_request(score_nettete=5.0)]),
    )
    assert capture.images[0].recevabilite.recevable is False
    assert capture.images[0].recevabilite.motif is MotifRecevabilite.FLOU
    assert capture.images[0].recevabilite.conseil


async def test_base64_invalide_refuse_sans_ecrire_de_fichier(
    service: ServiceParcelles, tmp_path: Path
):
    parcelle = await service.creer(DEVICE, CreerParcelleRequest(nom="Bloc", localite="Daloa"))
    capture = await service.deposer_capture(
        parcelle.identifiant,
        DEVICE,
        CaptureRequest(
            modalite=Modalite.PHOTOS,
            images=[_image_request(contenu_base64="!!! pas du base64 !!!")],
        ),
    )
    assert capture.images[0].recevabilite.motif is MotifRecevabilite.FORMAT_REFUSE
    assert not (tmp_path / "captures").exists() or not list((tmp_path / "captures").iterdir())


async def test_image_trop_lourde_refusee(service: ServiceParcelles):
    parcelle = await service.creer(DEVICE, CreerParcelleRequest(nom="Bloc", localite="Daloa"))
    gros = base64.b64encode(_jpeg() + b"\x00" * 4_000_000).decode("ascii")
    capture = await service.deposer_capture(
        parcelle.identifiant,
        DEVICE,
        CaptureRequest(modalite=Modalite.PHOTOS, images=[_image_request(contenu_base64=gros)]),
    )
    assert capture.images[0].recevabilite.motif is MotifRecevabilite.FORMAT_REFUSE


async def test_deposer_une_trace_hors_ci_est_refuse(service: ServiceParcelles):
    parcelle = await service.creer(DEVICE, CreerParcelleRequest(nom="Bloc", localite="Daloa"))
    with pytest.raises(GeometrieInvalide):
        await service.deposer_capture(
            parcelle.identifiant,
            DEVICE,
            CaptureRequest(
                modalite=Modalite.PARCOURS,
                trace=[CoordonneeRequest(latitude=48.86, longitude=2.35)],
            ),
        )


async def test_deposer_sur_parcelle_inconnue_leve(service: ServiceParcelles):
    with pytest.raises(ParcelleIntrouvable):
        await service.deposer_capture(
            "inexistante",
            DEVICE,
            CaptureRequest(modalite=Modalite.PHOTOS, images=[_image_request()]),
        )


async def test_purger_supprime_les_fichiers_des_captures_expirees(
    service: ServiceParcelles, tmp_path: Path
):
    parcelle = await service.creer(DEVICE, CreerParcelleRequest(nom="Bloc", localite="Daloa"))
    capture = await service.deposer_capture(
        parcelle.identifiant,
        DEVICE,
        CaptureRequest(modalite=Modalite.PHOTOS, images=[_image_request()]),
    )
    fichier = tmp_path / "captures" / f"{capture.images[0].empreinte_sha256}.bin"
    assert fichier.exists()
    # On purge « depuis le futur » pour que la capture du jour soit expirée.
    supprimes = await service.purger(maintenant=datetime.now(UTC) + timedelta(days=400))
    assert supprimes == 1
    assert not fichier.exists()
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `cd api && python -m pytest tests/test_service_parcelles.py -v --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.parcelles'`

- [ ] **Step 3: Écrire l'implémentation**

Créer `api/app/services/parcelles.py` :

```python
"""Service métier des parcelles — validation, écriture disque, recevabilité.

Le routeur ne décide de rien : toutes les règles vivent ici, conformément à la
séparation imposée par ``CLAUDE.md`` (aucune logique métier dans les routers).

Trois responsabilités, dans cet ordre :

1. **Valider la géographie** — un point hors de l'enveloppe ivoirienne, un anneau qui
   se coupe, une superficie absurde : refus motivé, en français.
2. **Écrire les images sur disque** — nom de fichier dérivé de l'empreinte SHA-256 du
   contenu, **jamais d'une donnée fournie par le client** : aucune traversée de chemin
   n'est possible, et l'empreinte déduplique naturellement.
3. **Rendre un verdict de recevabilité** par image, persisté avec la capture.

Une image refusée est **quand même enregistrée** en métadonnées, avec son motif : le
producteur doit voir ce qui a été rejeté et pourquoi. En revanche, ses octets ne
touchent pas le disque si le format n'est pas reconnu.
"""

from __future__ import annotations

import binascii
import base64
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from app.core.logging import get_logger
from app.domain.ports import ParcelleStorePort
from app.models.parcelle import (
    POINTS_MIN_POLYGONE,
    SUPERFICIE_MAX_HA,
    SUPERFICIE_MIN_HA,
    Capture,
    CaptureRequest,
    Coordonnee,
    CoordonneeRequest,
    CreerParcelleRequest,
    Geometrie,
    GeometrieRequest,
    Image,
    ImageRequest,
    MotifRecevabilite,
    Parcelle,
    Recevabilite,
)
from app.services.contacts import chercher as chercher_direction_regionale
from app.services.geometrie import anneau_auto_intersecte, dans_cote_ivoire
from app.services.vision.recevabilite import TAILLE_MAX_OCTETS, evaluer

logger = get_logger(__name__)

_CONSEIL_FORMAT = (
    "Ce fichier n'est pas une photo reconnue. Envoyez une image JPEG ou PNG."
)


class GeometrieInvalide(Exception):
    """Une géométrie soumise ne peut pas décrire une parcelle cacaoyère ivoirienne."""

    def __init__(self, motif: str) -> None:
        """Initialise l'exception.

        Args:
            motif: Explication en français, destinée à être affichée au producteur.
        """
        super().__init__(motif)
        self.motif = motif


class ParcelleIntrouvable(Exception):
    """La parcelle visée n'existe pas, ou n'appartient pas à cet appareil."""


class ServiceParcelles:
    """Orchestration métier des parcelles et de leurs captures terrain."""

    def __init__(
        self,
        store: ParcelleStorePort,
        dossier_captures: Path,
        retention_jours: int = 90,
        taille_max_octets: int = TAILLE_MAX_OCTETS,
    ) -> None:
        """Initialise le service.

        Args:
            store: Dépôt de persistance des parcelles.
            dossier_captures: Dossier où écrire les images (volume ``/data``).
            retention_jours: Rétention des captures avant purge, en jours.
            taille_max_octets: Plafond de taille par image, après décodage.
        """
        self._store = store
        self._dossier = dossier_captures
        self._retention_jours = retention_jours
        self._taille_max = taille_max_octets

    # ------------------------------------------------------------- parcelles

    async def creer(self, proprietaire: str, requete: CreerParcelleRequest) -> Parcelle:
        """Crée une parcelle et la rattache à sa direction régionale ANADER.

        Le rattachement réutilise l'annuaire de mise en relation ANADER déjà en place
        (``services/contacts.py``) : une localité inconnue donne une chaîne vide, jamais
        une direction inventée.
        """
        contact = chercher_direction_regionale(requete.localite)
        return await self._store.creer_parcelle(
            proprietaire,
            requete.nom,
            requete.localite,
            contact.nom if contact else "",
        )

    async def lister(self, proprietaire: str) -> list[Parcelle]:
        """Liste les parcelles de cet appareil."""
        return await self._store.lister_parcelles(proprietaire)

    async def obtenir(self, identifiant: str, proprietaire: str) -> Parcelle | None:
        """Retourne une parcelle de cet appareil, ou None."""
        return await self._store.obtenir_parcelle(identifiant, proprietaire)

    async def obtenir_capture(self, identifiant: str, proprietaire: str) -> Capture | None:
        """Retourne une capture de cet appareil, ou None."""
        return await self._store.obtenir_capture(identifiant, proprietaire)

    # ------------------------------------------------------------- géométrie

    @staticmethod
    def _en_coordonnees(points: list[CoordonneeRequest]) -> tuple[Coordonnee, ...]:
        """Convertit des points d'API en points de domaine."""
        return tuple(
            Coordonnee(
                latitude=p.latitude,
                longitude=p.longitude,
                precision_m=p.precision_m,
                horodatage=p.horodatage,
            )
            for p in points
        )

    def _valider_points(self, points: tuple[Coordonnee, ...]) -> None:
        """Vérifie que des points peuvent décrire le contour d'une parcelle ivoirienne.

        Ne contrôle **pas** la superficie : celle-ci n'existe qu'une fois la géométrie
        construite, et c'est ``enregistrer_geometrie`` qui s'en charge.

        Raises:
            GeometrieInvalide: Si un point sort du pays, ou si l'anneau se coupe.
        """
        for point in points:
            if not dans_cote_ivoire(point.latitude, point.longitude):
                raise GeometrieInvalide(
                    "Un des points relevés se trouve hors de la Côte d'Ivoire. "
                    "Vérifiez que la géolocalisation du téléphone est active."
                )
        if len(points) < POINTS_MIN_POLYGONE:
            return
        if anneau_auto_intersecte([(p.latitude, p.longitude) for p in points]):
            raise GeometrieInvalide(
                "Le tracé se coupe lui-même : refaites le tour de la parcelle sans "
                "revenir en arrière."
            )

    async def enregistrer_geometrie(
        self, identifiant: str, proprietaire: str, requete: GeometrieRequest
    ) -> Parcelle:
        """Valide puis enregistre la géométrie d'une parcelle.

        Raises:
            ParcelleIntrouvable: Si la parcelle n'existe pas pour cet appareil.
            GeometrieInvalide: Si la géométrie n'est pas plausible.
        """
        if await self._store.obtenir_parcelle(identifiant, proprietaire) is None:
            raise ParcelleIntrouvable(identifiant)
        points = self._en_coordonnees(requete.points)
        self._valider_points(points)
        geometrie = Geometrie.depuis_points(points, source=requete.source)
        if geometrie.superficie_ha is not None and not (
            SUPERFICIE_MIN_HA <= geometrie.superficie_ha <= SUPERFICIE_MAX_HA
        ):
            raise GeometrieInvalide(
                f"La superficie calculée ({geometrie.superficie_ha:.2f} ha) sort des "
                f"bornes attendues pour une parcelle ({SUPERFICIE_MIN_HA} à "
                f"{SUPERFICIE_MAX_HA} ha). Refaites le tour de la parcelle."
            )
        maj = await self._store.enregistrer_geometrie(identifiant, proprietaire, geometrie)
        if maj is None:
            raise ParcelleIntrouvable(identifiant)
        logger.info(
            "parcelle_geometrie_enregistree",
            parcelle=identifiant,
            points=len(points),
            superficie_ha=geometrie.superficie_ha,
        )
        return maj

    # --------------------------------------------------------------- captures

    def _ecrire_image(self, donnees: bytes) -> str:
        """Écrit les octets d'une image et retourne son empreinte SHA-256.

        Le nom de fichier dérive de l'empreinte du contenu, jamais d'une donnée du
        client : aucune traversée de chemin n'est possible, et deux téléversements
        identiques ne consomment qu'un fichier.
        """
        empreinte = hashlib.sha256(donnees).hexdigest()
        self._dossier.mkdir(parents=True, exist_ok=True)
        chemin = self._dossier / f"{empreinte}.bin"
        if not chemin.exists():
            chemin.write_bytes(donnees)
        return empreinte

    def _traiter_image(self, requete: ImageRequest) -> Image:
        """Décode, évalue et persiste une image ; rend son enregistrement de domaine."""
        try:
            donnees = base64.b64decode(requete.contenu_base64, validate=True)
        except (binascii.Error, ValueError):
            return self._image_refusee(requete, "base64_invalide")
        if len(donnees) > self._taille_max:
            return self._image_refusee(requete, "trop_lourde")

        verdict = evaluer(requete, donnees)
        if verdict.motif is MotifRecevabilite.FORMAT_REFUSE:
            return self._image_refusee(requete, "format_inconnu")

        empreinte = self._ecrire_image(donnees)
        return Image(
            empreinte_sha256=empreinte,
            largeur=requete.largeur,
            hauteur=requete.hauteur,
            recevabilite=verdict,
            coordonnee=(
                self._en_coordonnees([requete.coordonnee])[0] if requete.coordonnee else None
            ),
        )

    def _image_refusee(self, requete: ImageRequest, cause: str) -> Image:
        """Fabrique l'enregistrement d'une image rejetée, sans écrire sur disque."""
        logger.info("capture_image_refusee", cause=cause)
        return Image(
            empreinte_sha256="",
            largeur=requete.largeur,
            hauteur=requete.hauteur,
            recevabilite=Recevabilite(
                recevable=False,
                motif=MotifRecevabilite.FORMAT_REFUSE,
                conseil=_CONSEIL_FORMAT,
                score_nettete=requete.score_nettete,
            ),
        )

    async def deposer_capture(
        self, identifiant: str, proprietaire: str, requete: CaptureRequest
    ) -> Capture:
        """Traite et persiste une capture terrain.

        Raises:
            ParcelleIntrouvable: Si la parcelle n'existe pas pour cet appareil.
            GeometrieInvalide: Si un point de la trace sort de la Côte d'Ivoire.
        """
        if await self._store.obtenir_parcelle(identifiant, proprietaire) is None:
            raise ParcelleIntrouvable(identifiant)
        trace = self._en_coordonnees(requete.trace)
        for point in trace:
            if not dans_cote_ivoire(point.latitude, point.longitude):
                raise GeometrieInvalide(
                    "Un des points du parcours se trouve hors de la Côte d'Ivoire."
                )
        images = tuple(self._traiter_image(image) for image in requete.images)
        capture = Capture(
            identifiant=uuid4().hex,
            parcelle=identifiant,
            proprietaire=proprietaire,
            modalite=requete.modalite,
            cree_le=datetime.now(UTC),
            images=images,
            trace=trace,
        )
        await self._store.enregistrer_capture(capture)
        logger.info(
            "capture_deposee",
            parcelle=identifiant,
            modalite=requete.modalite.value,
            images=len(images),
            points=len(trace),
            refusees=sum(1 for i in images if not i.recevabilite.recevable),
        )
        return capture

    # ------------------------------------------------------------------ purge

    async def purger(self, maintenant: datetime | None = None) -> int:
        """Supprime les captures expirées et leurs fichiers.

        Args:
            maintenant: Instant de référence (injecté par les tests).

        Returns:
            Le nombre de fichiers effectivement supprimés du disque.
        """
        reference = maintenant or datetime.now(UTC)
        empreintes = await self._store.purger_captures(
            reference - timedelta(days=self._retention_jours)
        )
        supprimes = 0
        for empreinte in empreintes:
            if not empreinte:
                continue
            chemin = self._dossier / f"{empreinte}.bin"
            if chemin.exists():
                chemin.unlink()
                supprimes += 1
        return supprimes
```

- [ ] **Step 4: Lancer les tests pour vérifier qu'ils passent**

Run: `cd api && python -m pytest tests/test_service_parcelles.py -v --no-cov`
Expected: PASS — 14 tests

- [ ] **Step 5: Lint et commit**

```bash
cd api && python -m ruff format app/services/parcelles.py tests/test_service_parcelles.py && python -m ruff check app/services/parcelles.py tests/test_service_parcelles.py
cd .. && git add api/app/services/parcelles.py api/tests/test_service_parcelles.py
git commit -m "feat(parcelle): service metier - validation geographique et ecriture disque

Refus motives en francais : point hors CI, trace auto-intersectee, superficie hors
0,1-50 ha. Nom de fichier derive du SHA-256 du contenu, jamais d une donnee client
(aucune traversee de chemin, deduplication naturelle). Une image refusee est
persistee en metadonnees avec son motif, mais ses octets ne touchent pas le disque."
```

---

## Task 6: Réglages, câblage et routeur HTTP

**Files:**
- Modify: `api/app/core/config.py` (classe `Settings`, après les réglages de sessions)
- Modify: `api/app/api_deps.py`
- Modify: `api/app/main.py` (lifespan, purge, `include_router`)
- Create: `api/app/routers/parcelles.py`
- Test: `api/tests/test_parcelles_api.py`

**Interfaces:**
- Consomme : `ServiceParcelles` (T5), `ParcelleStore` (T4), schémas (T2).
- Produit :
  - `Settings.parcelles_enabled: bool = False`, `parcelles_db_path: str = "/data/parcelles.db"`, `captures_dir: str = "/data/captures"`, `captures_retention_jours: int = 90`, `profil_materiel: Literal["gpu", "cpu"] = "cpu"`
  - `get_parcelle_store(request) -> ParcelleStorePort`
  - `get_service_parcelles(request) -> ServiceParcelles`
  - Routes listées en §6.3 de la spec

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `api/tests/test_parcelles_api.py` :

```python
"""Tests des endpoints /v1/parcelles."""

from __future__ import annotations

import base64
import struct

import pytest
from fastapi.testclient import TestClient

ENTETES = {"X-Device-Id": "appareil-a"}
AUTRES_ENTETES = {"X-Device-Id": "appareil-b"}


def _jpeg(largeur: int = 1024, hauteur: int = 768) -> bytes:
    soi = b"\xff\xd8"
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
    sof0 = (
        b"\xff\xc0"
        + struct.pack(">H", 17)
        + b"\x08"
        + struct.pack(">HH", hauteur, largeur)
        + b"\x03"
        + b"\x00" * 9
    )
    return soi + app0 + sof0


def _image_payload(**surcharges) -> dict:
    charge = {
        "contenu_base64": base64.b64encode(_jpeg()).decode("ascii"),
        "largeur": 1024,
        "hauteur": 768,
        "score_nettete": 400.0,
        "luminance_moyenne": 128.0,
    }
    charge.update(surcharges)
    return charge


def _carre() -> list[dict]:
    cote = 0.000899
    lat, lon = 6.85, -5.28
    return [
        {"latitude": a, "longitude": b}
        for a, b in [
            (lat, lon),
            (lat, lon + cote),
            (lat + cote, lon + cote),
            (lat + cote, lon),
        ]
    ]


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    """Client de test avec les parcelles activées sur un volume temporaire."""
    monkeypatch.setenv("PARCELLES_ENABLED", "true")
    monkeypatch.setenv("PARCELLES_DB_PATH", str(tmp_path / "parcelles.db"))
    monkeypatch.setenv("CAPTURES_DIR", str(tmp_path / "captures"))
    from app.core.config import get_settings
    from app.main import create_app

    get_settings.cache_clear()
    with TestClient(create_app()) as client_test:
        yield client_test
    get_settings.cache_clear()


def _creer(client: TestClient, entetes: dict = ENTETES) -> str:
    reponse = client.post(
        "/v1/parcelles", json={"nom": "Bloc Est", "localite": "Daloa"}, headers=entetes
    )
    assert reponse.status_code == 201, reponse.text
    return reponse.json()["identifiant"]


def test_creer_une_parcelle_renvoie_201(client: TestClient):
    reponse = client.post(
        "/v1/parcelles", json={"nom": "Bloc Est", "localite": "Daloa"}, headers=ENTETES
    )
    assert reponse.status_code == 201
    charge = reponse.json()
    assert charge["nom"] == "Bloc Est"
    assert charge["geometrie"] is None


def test_creer_sans_nom_renvoie_422(client: TestClient):
    reponse = client.post("/v1/parcelles", json={"localite": "Daloa"}, headers=ENTETES)
    assert reponse.status_code == 422


def test_lister_ne_montre_que_ses_propres_parcelles(client: TestClient):
    _creer(client, ENTETES)
    _creer(client, AUTRES_ENTETES)
    reponse = client.get("/v1/parcelles", headers=ENTETES)
    assert reponse.status_code == 200
    assert len(reponse.json()) == 1


def test_obtenir_une_parcelle_d_un_autre_appareil_renvoie_404(client: TestClient):
    identifiant = _creer(client, ENTETES)
    reponse = client.get(f"/v1/parcelles/{identifiant}", headers=AUTRES_ENTETES)
    assert reponse.status_code == 404


def test_enregistrer_une_geometrie_renvoie_la_superficie(client: TestClient):
    identifiant = _creer(client)
    reponse = client.put(
        f"/v1/parcelles/{identifiant}/geometrie",
        json={"points": _carre(), "source": "parcours_gps"},
        headers=ENTETES,
    )
    assert reponse.status_code == 200, reponse.text
    geometrie = reponse.json()["geometrie"]
    assert geometrie["type"] == "polygone"
    assert geometrie["superficie_ha"] == pytest.approx(1.0, abs=0.05)


def test_geometrie_hors_ci_renvoie_422_avec_motif_lisible(client: TestClient):
    identifiant = _creer(client)
    paris = [
        {"latitude": 48.86 + i * 0.001, "longitude": 2.35 + j * 0.001}
        for i, j in [(0, 0), (0, 1), (1, 1), (1, 0)]
    ]
    reponse = client.put(
        f"/v1/parcelles/{identifiant}/geometrie",
        json={"points": paris},
        headers=ENTETES,
    )
    assert reponse.status_code == 422
    assert "Côte d'Ivoire" in reponse.json()["detail"]


def test_geometrie_sur_parcelle_inconnue_renvoie_404(client: TestClient):
    reponse = client.put(
        "/v1/parcelles/inexistante/geometrie",
        json={"points": _carre()},
        headers=ENTETES,
    )
    assert reponse.status_code == 404


def test_deposer_des_photos_renvoie_201_et_le_verdict(client: TestClient):
    identifiant = _creer(client)
    reponse = client.post(
        f"/v1/parcelles/{identifiant}/captures",
        json={"modalite": "photos", "images": [_image_payload()]},
        headers=ENTETES,
    )
    assert reponse.status_code == 201, reponse.text
    charge = reponse.json()
    assert charge["modalite"] == "photos"
    assert charge["images"][0]["recevabilite"]["recevable"] is True


def test_deposer_une_photo_floue_renvoie_le_conseil_de_reprise(client: TestClient):
    identifiant = _creer(client)
    reponse = client.post(
        f"/v1/parcelles/{identifiant}/captures",
        json={"modalite": "photos", "images": [_image_payload(score_nettete=3.0)]},
        headers=ENTETES,
    )
    assert reponse.status_code == 201
    recevabilite = reponse.json()["images"][0]["recevabilite"]
    assert recevabilite["recevable"] is False
    assert recevabilite["motif"] == "flou"
    assert "approchez" in recevabilite["conseil"].lower()


def test_deposer_un_parcours_video_accepte_les_deux_contrats(client: TestClient):
    identifiant = _creer(client)
    reponse = client.post(
        f"/v1/parcelles/{identifiant}/captures",
        json={
            "modalite": "parcours_video",
            "images": [_image_payload()],
            "trace": _carre(),
        },
        headers=ENTETES,
    )
    assert reponse.status_code == 201
    charge = reponse.json()
    assert len(charge["images"]) == 1
    assert len(charge["trace"]) == 4


def test_capture_vide_renvoie_422(client: TestClient):
    identifiant = _creer(client)
    reponse = client.post(
        f"/v1/parcelles/{identifiant}/captures",
        json={"modalite": "photos", "images": [], "trace": []},
        headers=ENTETES,
    )
    assert reponse.status_code == 422


def test_plus_de_douze_images_renvoie_422(client: TestClient):
    identifiant = _creer(client)
    reponse = client.post(
        f"/v1/parcelles/{identifiant}/captures",
        json={"modalite": "video", "images": [_image_payload() for _ in range(13)]},
        headers=ENTETES,
    )
    assert reponse.status_code == 422


def test_relire_une_capture(client: TestClient):
    identifiant = _creer(client)
    depot = client.post(
        f"/v1/parcelles/{identifiant}/captures",
        json={"modalite": "photos", "images": [_image_payload()]},
        headers=ENTETES,
    )
    capture_id = depot.json()["identifiant"]
    reponse = client.get(
        f"/v1/parcelles/{identifiant}/captures/{capture_id}", headers=ENTETES
    )
    assert reponse.status_code == 200
    assert reponse.json()["identifiant"] == capture_id


def test_endpoints_absents_quand_le_drapeau_est_off(tmp_path, monkeypatch):
    """Parcelles désactivées : les routes répondent 404, le reste de l'API vit."""
    monkeypatch.setenv("PARCELLES_ENABLED", "false")
    from app.core.config import get_settings
    from app.main import create_app

    get_settings.cache_clear()
    with TestClient(create_app()) as client_test:
        assert client_test.get("/v1/parcelles", headers=ENTETES).status_code == 404
        assert client_test.get("/health").status_code == 200
    get_settings.cache_clear()
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `cd api && python -m pytest tests/test_parcelles_api.py -v --no-cov`
Expected: FAIL — 404 sur toutes les routes (le routeur n'existe pas)

- [ ] **Step 3: Ajouter les réglages**

Dans `api/app/core/config.py`, ajouter à la classe `Settings`, après les réglages de sessions, et documenter chaque champ dans la docstring `Attributes` de la classe (le style du module l'exige) :

```python
    # --- Parcelles & captures terrain (V3, chantier C1) ---
    parcelles_enabled: bool = False
    parcelles_db_path: str = "/data/parcelles.db"
    captures_dir: str = "/data/captures"
    captures_retention_jours: int = 90

    # Profil matériel : déclare les capacités disponibles, pas le backend
    # (``inference_backend`` s'en charge). Défaut ``cpu`` : une erreur de
    # configuration dégrade le service, elle ne le casse pas.
    profil_materiel: Literal["gpu", "cpu"] = "cpu"
```

Lignes à ajouter à la docstring `Attributes` :

```
        parcelles_enabled: Active les parcelles et les captures terrain (V3, C1).
        parcelles_db_path: Chemin du fichier SQLite des parcelles (volume /data).
        captures_dir: Dossier des images de capture (volume /data).
        captures_retention_jours: Rétention des captures avant purge, en jours.
        profil_materiel: Capacités disponibles ("gpu" ou "cpu").
```

- [ ] **Step 4: Câbler les dépendances**

Dans `api/app/api_deps.py`, ajouter après `get_session_store` :

```python
def get_parcelle_store(request: Request) -> ParcelleStorePort:
    """Retourne le dépôt de parcelles stocké dans l'état de l'application."""
    return request.app.state.parcelles


def get_service_parcelles(request: Request) -> ServiceParcelles:
    """Retourne le service métier des parcelles stocké dans l'état de l'application."""
    return request.app.state.service_parcelles
```

Et les imports correspondants :

```python
from app.domain.ports import ParcelleStorePort  # à ajouter à l'import groupé existant
from app.services.parcelles import ServiceParcelles
```

- [ ] **Step 5: Câbler le cycle de vie**

Dans `api/app/main.py`, ajouter les imports :

```python
from pathlib import Path

from app.core.parcelles_store import ParcelleStore
from app.routers import parcelles
from app.services.parcelles import ServiceParcelles
```

Dans `lifespan`, après le bloc des sessions :

```python
    app.state.parcelles = ParcelleStore.from_settings(settings)
    app.state.service_parcelles = ServiceParcelles(
        app.state.parcelles,
        dossier_captures=Path(settings.captures_dir),
        retention_jours=settings.captures_retention_jours,
    )
    app.state.purge_captures_task = None
    if settings.parcelles_enabled:
        await app.state.parcelles.initialiser()
        app.state.purge_captures_task = _lancer_purge_captures(app)
```

Dans le bloc de libération (là où `purge_task` est annulée), annuler aussi `purge_captures_task` en suivant exactement le même motif.

Ajouter la tâche de purge, calquée sur `_lancer_purge_sessions` (lire son implémentation autour de `api/app/main.py:100` et reproduire sa structure — `asyncio.create_task`, boucle avec `asyncio.sleep`, capture des exceptions, journalisation) :

```python
def _lancer_purge_captures(app: FastAPI) -> asyncio.Task[None]:
    """Lance la purge périodique des captures expirées (moule des sessions)."""

    async def boucle() -> None:
        while True:
            await asyncio.sleep(24 * 3600)
            try:
                nombre = await app.state.service_parcelles.purger()
                if nombre:
                    logger.info("captures_purgees_disque", nombre=nombre)
            except Exception as exc:  # noqa: BLE001 - la purge ne doit jamais tuer l'app
                logger.warning("purge_captures_echouee", error=str(exc))

    return asyncio.create_task(boucle())
```

Enfin, enregistrer le routeur sous condition, à côté des autres `include_router` :

```python
    if get_settings().parcelles_enabled:
        app.include_router(parcelles.router)
```

- [ ] **Step 6: Écrire le routeur**

Créer `api/app/routers/parcelles.py` :

```python
"""Endpoints /v1/parcelles — parcelles cacaoyères et captures terrain (V3, C1).

Adaptateurs HTTP du service métier : **aucune règle ici**. On traduit les exceptions
du service en codes de statut, et c'est tout.

Cloisonnement par appareil, comme les sessions V2 (D1) : chaque requête porte un
identifiant anonyme ``X-Device-Id``. Un navigateur ne voit jamais les parcelles d'un
autre — et l'on ne stocke aucune IP.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api_deps import get_cache_client, get_client_ip, get_device_id, get_service_parcelles
from app.domain.ports import CachePort
from app.models.parcelle import (
    CaptureReponse,
    CaptureRequest,
    CreerParcelleRequest,
    GeometrieRequest,
    ParcelleReponse,
)
from app.services.parcelles import GeometrieInvalide, ParcelleIntrouvable, ServiceParcelles

router = APIRouter(prefix="/v1", tags=["parcelles"])

_TROP_DE_REQUETES = "Trop de requêtes, veuillez réessayer dans une minute."


async def _garde_debit(cache: CachePort, client_ip: str) -> None:
    """Applique le rate-limit par IP.

    Raises:
        HTTPException: 429 si la limite est dépassée.
    """
    if await cache.hit_rate_limit(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=_TROP_DE_REQUETES
        )


@router.post(
    "/parcelles", response_model=ParcelleReponse, status_code=status.HTTP_201_CREATED
)
async def creer_parcelle(
    payload: CreerParcelleRequest,
    client_ip: str = Depends(get_client_ip),
    device_id: str = Depends(get_device_id),
    cache: CachePort = Depends(get_cache_client),
    service: ServiceParcelles = Depends(get_service_parcelles),
) -> ParcelleReponse:
    """Crée une parcelle rattachée à l'appareil appelant."""
    await _garde_debit(cache, client_ip)
    parcelle = await service.creer(device_id, payload)
    return ParcelleReponse.model_validate(parcelle, from_attributes=True)


@router.get("/parcelles", response_model=list[ParcelleReponse])
async def lister_parcelles(
    device_id: str = Depends(get_device_id),
    service: ServiceParcelles = Depends(get_service_parcelles),
) -> list[ParcelleReponse]:
    """Liste les parcelles de l'appareil appelant."""
    parcelles = await service.lister(device_id)
    return [ParcelleReponse.model_validate(p, from_attributes=True) for p in parcelles]


@router.get("/parcelles/{identifiant}", response_model=ParcelleReponse)
async def obtenir_parcelle(
    identifiant: str,
    device_id: str = Depends(get_device_id),
    service: ServiceParcelles = Depends(get_service_parcelles),
) -> ParcelleReponse:
    """Retourne une parcelle de l'appareil appelant.

    Raises:
        HTTPException: 404 si elle n'existe pas pour cet appareil.
    """
    parcelle = await service.obtenir(identifiant, device_id)
    if parcelle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Parcelle inconnue.")
    return ParcelleReponse.model_validate(parcelle, from_attributes=True)


@router.put("/parcelles/{identifiant}/geometrie", response_model=ParcelleReponse)
async def enregistrer_geometrie(
    identifiant: str,
    payload: GeometrieRequest,
    client_ip: str = Depends(get_client_ip),
    device_id: str = Depends(get_device_id),
    cache: CachePort = Depends(get_cache_client),
    service: ServiceParcelles = Depends(get_service_parcelles),
) -> ParcelleReponse:
    """Enregistre le contour relevé d'une parcelle.

    Raises:
        HTTPException: 404 si la parcelle est inconnue, 422 si la géométrie est
            invalide (le motif est renvoyé tel quel, il est destiné au producteur).
    """
    await _garde_debit(cache, client_ip)
    try:
        parcelle = await service.enregistrer_geometrie(identifiant, device_id, payload)
    except ParcelleIntrouvable as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Parcelle inconnue."
        ) from exc
    except GeometrieInvalide as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.motif
        ) from exc
    return ParcelleReponse.model_validate(parcelle, from_attributes=True)


@router.post(
    "/parcelles/{identifiant}/captures",
    response_model=CaptureReponse,
    status_code=status.HTTP_201_CREATED,
)
async def deposer_capture(
    identifiant: str,
    payload: CaptureRequest,
    client_ip: str = Depends(get_client_ip),
    device_id: str = Depends(get_device_id),
    cache: CachePort = Depends(get_cache_client),
    service: ServiceParcelles = Depends(get_service_parcelles),
) -> CaptureReponse:
    """Dépose une capture terrain (images échantillonnées et/ou trace GPS).

    Raises:
        HTTPException: 404 si la parcelle est inconnue, 422 si la trace est invalide.
    """
    await _garde_debit(cache, client_ip)
    try:
        capture = await service.deposer_capture(identifiant, device_id, payload)
    except ParcelleIntrouvable as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Parcelle inconnue."
        ) from exc
    except GeometrieInvalide as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.motif
        ) from exc
    return CaptureReponse.model_validate(capture, from_attributes=True)


@router.get(
    "/parcelles/{identifiant}/captures/{capture_id}", response_model=CaptureReponse
)
async def obtenir_capture(
    identifiant: str,
    capture_id: str,
    device_id: str = Depends(get_device_id),
    service: ServiceParcelles = Depends(get_service_parcelles),
) -> CaptureReponse:
    """Retourne une capture de l'appareil appelant.

    Raises:
        HTTPException: 404 si la capture est inconnue ou ne concerne pas la parcelle.
    """
    capture = await service.obtenir_capture(capture_id, device_id)
    if capture is None or capture.parcelle != identifiant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Capture inconnue.")
    return CaptureReponse.model_validate(capture, from_attributes=True)
```

- [ ] **Step 7: Lancer les tests pour vérifier qu'ils passent**

Run: `cd api && python -m pytest tests/test_parcelles_api.py -v --no-cov`
Expected: PASS — 14 tests

- [ ] **Step 8: Lancer la suite complète et vérifier la couverture**

Run: `cd api && python -m pytest -q`
Expected: PASS, couverture ≥ 97 %. Si un module de C1 est sous le seuil, ajouter les tests manquants sur les branches non couvertes avant de committer.

- [ ] **Step 9: Lint et commit**

```bash
cd api && python -m ruff format app/ tests/ && python -m ruff check app/ tests/
cd .. && git add api/app/core/config.py api/app/api_deps.py api/app/main.py api/app/routers/parcelles.py api/app/services/parcelles.py api/tests/test_parcelles_api.py
git commit -m "feat(parcelle): endpoints /v1/parcelles derriere le drapeau PARCELLES_ENABLED

Six routes, aucune logique metier dans le routeur. Cloisonnement par X-Device-Id
comme les sessions V2. Purge quotidienne des captures expirees. Drapeau OFF par
defaut : deploiement en deux temps."
```

---

## Task 7: Variables d'environnement du déploiement

**Files:**
- Modify: `deploy/k8s/api.yaml`
- Test: manuel (voir étapes)

**Interfaces:**
- Consomme : les réglages de la Tâche 6.
- Produit : la ConfigMap et le volume nécessaires en production.

- [ ] **Step 1: Lire le manifeste pour repérer la ConfigMap et le volume `/data`**

Run: `grep -n "PARCELLES\|SESSIONS_\|/data\|volumeMounts\|configMapKeyRef" deploy/k8s/api.yaml`

Repérer comment `SESSIONS_ENABLED` et `SESSIONS_DB_PATH` sont injectés, et où le volume persistant `/data` est monté.

- [ ] **Step 2: Ajouter les variables, en calquant exactement le motif des sessions**

Dans la ConfigMap de `deploy/k8s/api.yaml` :

```yaml
  # Parcelles & captures terrain (V3, chantier C1). OFF au premier deploiement :
  # on verifie que le schema SQLite se cree sur /data avant d ouvrir les routes.
  PARCELLES_ENABLED: "false"
  PARCELLES_DB_PATH: "/data/parcelles.db"
  CAPTURES_DIR: "/data/captures"
  CAPTURES_RETENTION_JOURS: "90"
  PROFIL_MATERIEL: "cpu"
```

Puis injecter chaque variable dans le conteneur `api` par `configMapKeyRef`, à l'identique de `SESSIONS_ENABLED`.

- [ ] **Step 3: Vérifier que le manifeste est valide**

Run: `python -c "import yaml,sys; list(yaml.safe_load_all(open('deploy/k8s/api.yaml', encoding='utf-8'))); print('YAML valide')"`
Expected: `YAML valide`

- [ ] **Step 4: Commit**

```bash
git add deploy/k8s/api.yaml
git commit -m "chore(deploy): variables des parcelles et profil materiel (OFF par defaut)

Deploiement en deux temps : on verifie la creation du schema sur /data avant
d ouvrir les routes."
```

---

## Task 8: Écran « Ma parcelle » — les quatre modalités

C'est ici que vivent l'échantillonnage vidéo, `watchPosition` et le calcul des métriques de netteté. Le navigateur possède les pixels décodés : il fait le travail que le serveur ne peut pas faire sans dépendance, et refuse une mauvaise image **avant** de consommer de la bande passante.

**Files:**
- Create: `web/parcelle.html`
- Create: `web/parcelle.js`
- Test: manuel sur téléphone réel (voir étapes) — pas de banc de test JavaScript dans ce dépôt

**Interfaces:**
- Consomme : les endpoints de la Tâche 6.
- Produit : l'écran producteur.

- [ ] **Step 1: Repérer les conventions du front existant**

Run: `ls web/ && grep -rn "X-Device-Id" web/ | head -5`

Reprendre la même façon d'obtenir et de persister l'identifiant d'appareil, les mêmes styles et la même structure de page que l'écran de chat. **Ne pas introduire de framework** : le front est en JavaScript natif.

- [ ] **Step 2: Écrire le module de capture**

Créer `web/parcelle.js` avec ces quatre fonctions, plus le câblage vers l'API :

```javascript
// Netteté par variance du laplacien, calculée sur les pixels que le navigateur
// possède déjà. Le serveur ne peut pas le faire : il n'a pas de décodeur d'image
// (aucune dépendance nouvelle n'est autorisée côté API).
function scoreNettete(imageData) {
  const { data, width, height } = imageData;
  const gris = new Float32Array(width * height);
  for (let i = 0; i < gris.length; i++) {
    const p = i * 4;
    gris[i] = 0.299 * data[p] + 0.587 * data[p + 1] + 0.114 * data[p + 2];
  }
  let somme = 0;
  let sommeCarres = 0;
  let nombre = 0;
  for (let y = 1; y < height - 1; y++) {
    for (let x = 1; x < width - 1; x++) {
      const i = y * width + x;
      const laplacien =
        -4 * gris[i] + gris[i - 1] + gris[i + 1] + gris[i - width] + gris[i + width];
      somme += laplacien;
      sommeCarres += laplacien * laplacien;
      nombre++;
    }
  }
  const moyenne = somme / nombre;
  return sommeCarres / nombre - moyenne * moyenne;
}

function luminanceMoyenne(imageData) {
  const { data } = imageData;
  let somme = 0;
  for (let p = 0; p < data.length; p += 4) {
    somme += 0.299 * data[p] + 0.587 * data[p + 1] + 0.114 * data[p + 2];
  }
  return somme / (data.length / 4);
}

// Échantillonne une vidéo en images fixes, SUR L'APPAREIL. Téléverser une video de
// 100 Mo sur un reseau mobile ivoirien echouerait : douze vues suffisent au constat.
const IMAGES_MAX = 12;
const INTERVALLE_S = 2;

async function echantillonnerVideo(fichier) {
  const video = document.createElement('video');
  video.muted = true;
  video.src = URL.createObjectURL(fichier);
  await new Promise((ok, ko) => {
    video.onloadedmetadata = ok;
    video.onerror = () => ko(new Error('Vidéo illisible.'));
  });
  const canvas = document.createElement('canvas');
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  const contexte = canvas.getContext('2d');
  const images = [];
  const total = Math.min(IMAGES_MAX, Math.floor(video.duration / INTERVALLE_S) || 1);
  for (let indice = 0; indice < total; indice++) {
    video.currentTime = indice * INTERVALLE_S;
    await new Promise((ok) => { video.onseeked = ok; });
    contexte.drawImage(video, 0, 0);
    images.push(await depuisCanvas(canvas, contexte));
  }
  URL.revokeObjectURL(video.src);
  return images;
}

async function depuisCanvas(canvas, contexte) {
  const donnees = contexte.getImageData(0, 0, canvas.width, canvas.height);
  const blob = await new Promise((ok) => canvas.toBlob(ok, 'image/jpeg', 0.85));
  const tampon = await blob.arrayBuffer();
  let binaire = '';
  const octets = new Uint8Array(tampon);
  for (let i = 0; i < octets.length; i++) binaire += String.fromCharCode(octets[i]);
  return {
    contenu_base64: btoa(binaire),
    largeur: canvas.width,
    hauteur: canvas.height,
    score_nettete: scoreNettete(donnees),
    luminance_moyenne: luminanceMoyenne(donnees),
  };
}

// Parcours GPS du contour de la parcelle. On ne garde un point que s'il apporte de
// l'information : au moins 5 m depuis le precedent, precision acceptable.
const DISTANCE_MIN_M = 5;
const PRECISION_MAX_M = 50;

function demarrerParcours(surPoint) {
  return navigator.geolocation.watchPosition(
    (position) => {
      const { latitude, longitude, accuracy } = position.coords;
      if (accuracy > PRECISION_MAX_M) return;
      surPoint({
        latitude,
        longitude,
        precision_m: accuracy,
        horodatage: new Date(position.timestamp).toISOString(),
      });
    },
    (erreur) => console.warn('Géolocalisation indisponible :', erreur.message),
    { enableHighAccuracy: true, maximumAge: 0, timeout: 15000 },
  );
}
```

Câbler ensuite les appels : `POST /v1/parcelles`, `PUT /v1/parcelles/{id}/geometrie`, `POST /v1/parcelles/{id}/captures`, avec l'en-tête `X-Device-Id`. Filtrer localement par la distance minimale entre points avant d'envoyer la trace.

- [ ] **Step 3: Écrire la page**

Créer `web/parcelle.html` : formulaire de création (nom, localité), liste des parcelles, et quatre boutons de capture — *Photos*, *Vidéo*, *Parcours*, *Parcours + vidéo*. Afficher, pour chaque image, le verdict de recevabilité et son conseil de reprise. Si `navigator.geolocation` est refusé, proposer la saisie manuelle de la localité — **jamais un blocage**.

- [ ] **Step 4: Vérifier localement**

Run: `docker compose up -d api web` puis ouvrir l'écran dans un navigateur.

Vérifier : création d'une parcelle, dépôt de photos, refus d'une photo volontairement floue avec son conseil, échantillonnage d'une courte vidéo, tracé d'un parcours (simulable via les outils de développement du navigateur).

- [ ] **Step 5: Vérifier sur téléphone réel**

C'est le seul test qui compte pour cet écran. Vérifier : l'appareil photo s'ouvre, la géolocalisation autorise et refuse proprement, une vidéo de 30 s produit bien 12 images ou moins, le téléversement aboutit sur réseau mobile.

- [ ] **Step 6: Commit**

```bash
git add web/parcelle.html web/parcelle.js
git commit -m "feat(web): ecran Ma parcelle - quatre modalites de capture

Echantillonnage video SUR L APPAREIL (12 images max, 1 / 2 s) : televerser une
video sur reseau mobile ivoirien echouerait. Nettete et luminance calculees sur les
pixels du canvas, refus avant televersement. watchPosition filtre a 5 m et 50 m de
precision. Geolocalisation refusee = saisie manuelle, jamais un blocage."
```

---

## Recette de fin de chantier

À exécuter avant de déclarer C1 terminé. Chaque ligne correspond à un critère d'acceptation de la spec §6.5.

- [ ] `cd api && python -m pytest -q` — vert, couverture ≥ 97 %
- [ ] `cd api && python -m ruff check app/ tests/` — aucune erreur
- [ ] Les quatre modalités aboutissent à une capture persistée, **vérifié sur téléphone réel**
- [ ] Une géométrie hors Côte d'Ivoire est refusée avec un motif lisible (422)
- [ ] Une géométrie auto-intersectée est refusée avec un motif lisible (422)
- [ ] Une superficie hors 0,1–50 ha est refusée avec un motif lisible (422)
- [ ] Une image floue est refusée avec un conseil de reprise
- [ ] Une image en contre-jour est refusée avec un conseil mentionnant le soleil
- [ ] Un fichier qui n'est pas une image n'est **jamais** écrit sur disque
- [ ] `/data` inaccessible : l'API démarre, `/health` répond 200, le chat fonctionne, les parcelles sont indisponibles
- [ ] Drapeau `PARCELLES_ENABLED=false` : les routes répondent 404, le reste de l'API vit
- [ ] Aucun test n'appelle le réseau
- [ ] Aucun dosage phytosanitaire dans le code ni dans les données de test

Puis mettre à jour `docs/agents_v3.md` avec une section « La parcelle », dans le style pédagogique du document (*le concept*, *les décisions*, *le modèle mental*), et committer.
