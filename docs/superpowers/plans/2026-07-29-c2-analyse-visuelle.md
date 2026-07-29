# C2 — Analyse visuelle en cascade : plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produire, à partir des captures de parcelle livrées par C1, un **constat visuel descriptif non diagnostique**, croisé au contexte météo et géographique, assorti d'une fiche de signalement ANADER et d'une file de revue humaine qui construit le jeu de données ivoirien.

**Architecture:** Une **cascade** d'étages indépendants, chacun testable seul et capable de s'abstenir. C1 a livré l'étage 0 (recevabilité). C2 livre les étages 1 (tri de l'organe, par VLM), 4 (fusion contextuelle), 5 (rédaction du constat) et 6 (boucle de revue). Les étages 2 et 3 — localisation des lésions et étiologie — sont **hors périmètre**, ouverts plus tard par le verrou de rappel (§7.5 de la spec). Le VLM vit derrière un **port mockable** : tout C2 se construit et se teste sans GPU, seul le service du modèle en a besoin.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, `httpx`, `sqlite3` (stdlib), `structlog`, pytest. **Aucune dépendance nouvelle.** Modèle de vision : Qwen3-VL à poids ouverts, servi localement.

**Spec de référence :** `docs/superpowers/specs/2026-07-28-v3-operationnelle-design.md` §7.
**Prérequis livré :** chantier C1 (`docs/superpowers/plans/2026-07-28-c1-socle-parcelle-capture.md`), 872 tests verts.

---

## Global Constraints

Ces contraintes s'appliquent à **toutes** les tâches. Une tâche qui les viole est à refaire.

- **Python 3.11+**, `from __future__ import annotations` en tête de chaque module.
- **Typage systématique.** Aucune variable globale mutable.
- **Clean architecture, strictement** : `domain/` (contrats purs) → `application/` (orchestration pure, testable sans réseau) → `services/` (adaptateurs concrets) → `routers/` (adaptateurs HTTP, **aucune logique métier**).
- **`ruff format` + `ruff check`** doivent passer. `line-length = 100`, règles `E, F, I, UP, B, C4, SIM`.
- **Couverture ≥ 97 %** — `--cov-fail-under=97` fait échouer `pytest` sinon.
- **Logging par `structlog`** (`from app.core.logging import get_logger`). **Jamais `print()`.**
- **Docstrings format Google**, en français, sur chaque module, classe et fonction publique.
- **Aucune dépendance nouvelle.**
- **Aucun appel réseau dans les tests.** Le port de vision est mockable, comme l'est déjà celui de la météo.
- **Aucun dosage phytosanitaire nulle part**, même en donnée de test.
- **Recommandations OWASP appliquées** ; le skill `security-review` valide le chantier avant clôture.

### Doctrine — les trois règles que ce chantier ne peut pas enfreindre

**D3 — Pas de diagnostic autonome.** *Arbitrage Waopron du 28/07/2026.* Le système **constate et signale** : il décrit ce qu'il observe, affiche sa confiance, **ne nomme jamais une maladie**, **jamais un produit**, **jamais un dosage**, et transmet un constat horodaté à l'ANADER pour confirmation humaine. Le pré-diagnostic étiologique s'active au franchissement d'un seuil de rappel par classe (0,90 proposé sur pourriture brune et swollen shoot), **jamais à une date** — hors périmètre C2.

**D1 — Souveraineté.** Le modèle de vision est à poids ouverts et servi **localement**, jamais une API tierce. Le service `vision/` n'est **jamais exposé publiquement** : l'API le consomme en interne, comme elle le fait déjà d'`inference/`.

**Anti-fabrication.** Le pattern « contexte vide → fabrication » a déjà été corrigé une fois sur les agents (v0.6.48). Il ne doit pas revenir par la vision : **sans VLM disponible, l'API le dit** — message explicite, jamais une description inventée, jamais une erreur brute.

---

## Structure des fichiers

| Fichier | Couche | Responsabilité | Tâche |
|---|---|---|---|
| `api/app/domain/ports.py` | domain | **modifié** — ajout de `VisionPort` | 1 |
| `api/app/models/constat.py` | domain | **créé** — types du constat et schémas d'API | 1 |
| `api/app/services/vision/indisponible.py` | services | **créé** — source de vision neutre (profil CPU) | 1 |
| `api/app/services/vision/vlm.py` | services | **créé** — client HTTP du VLM local | 2 |
| `api/app/application/fusion_contextuelle.py` | application | **créé** — étage 4, orchestration pure | 3 |
| `api/app/services/prompts_constat.py` | services | **créé** — consignes strictes du constat | 4 |
| `api/app/services/guardrails.py` | services | **modifié** — refus des noms de maladie en sortie | 4 |
| `api/app/application/constat_visuel.py` | application | **créé** — étage 5, assemblage du constat | 4 |
| `api/app/core/parcelles_store.py` | core | **modifié** — migration 2 : table `constats` | 5 |
| `api/app/services/constats.py` | services | **créé** — service métier du constat | 5 |
| `api/app/routers/parcelles.py` | routers | **modifié** — endpoint `/constat` | 5 |
| `api/app/curation/revue_constats.py` | curation | **créé** — file de revue ANADER | 6 |
| `api/app/core/config.py`, `api_deps.py`, `main.py` | — | **modifiés** — câblage et drapeau | 7 |
| `deploy/k8s/vision.yaml` | deploy | **créé** — service VLM, jamais exposé | 7 |

---

## Task 1: Port de vision, types du constat, source indisponible

Le socle. Rien d'autre ne peut commencer avant, et **cette tâche seule rend déjà le système honnête** : en profil CPU, l'API saura dire qu'elle ne voit pas, au lieu d'inventer.

**Files:**
- Modify: `api/app/domain/ports.py`
- Create: `api/app/models/constat.py`
- Create: `api/app/services/vision/indisponible.py`
- Test: `api/tests/test_models_constat.py`, `api/tests/test_vision_indisponible.py`

**Interfaces:**
- Consomme : `Recevabilite`, `MotifRecevabilite` (C1, `app/models/parcelle.py`).
- Produit :
  - `VisionPort` (Protocol) : `async def decrire(self, images: tuple[bytes, ...], consigne: str) -> str | None`, `async def disponible(self) -> bool`
  - `VisionIndisponible` : implémentation neutre, `decrire` → `None`, `disponible` → `False`
  - `dataclass(frozen=True)` : `Observation`, `Constat`
  - Enums : `Organe`, `NiveauConfiance`, `EtatRevue`
  - Pydantic : `ConstatReponse`, `ObservationReponse`
  - Constante : `CONSIGNE_INDISPONIBLE`

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `api/tests/test_models_constat.py` :

```python
"""Tests des types de domaine du constat visuel."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest

from app.models.constat import (
    Constat,
    EtatRevue,
    NiveauConfiance,
    Observation,
    Organe,
)


def _observation(**surcharges) -> Observation:
    defauts = {
        "organe": Organe.CABOSSE,
        "description": "Cabosse mûre, surface régulière, aucune lésion visible.",
        "confiance": NiveauConfiance.MOYENNE,
        "empreinte_image": "a" * 64,
    }
    return Observation(**{**defauts, **surcharges})


def test_observation_est_immuable():
    obs = _observation()
    with pytest.raises(dataclasses.FrozenInstanceError):
        obs.description = "autre"  # type: ignore[misc]


def test_constat_naît_en_attente_de_revue():
    constat = Constat(
        identifiant="c1",
        capture="cap1",
        parcelle="p1",
        proprietaire="appareil-a",
        observations=(_observation(),),
        texte="Constat visuel…",
        confiance=NiveauConfiance.MOYENNE,
        cree_le=datetime.now(UTC),
    )
    assert constat.etat_revue is EtatRevue.EN_ATTENTE
    assert constat.revu_par == ""


def test_organe_couvre_les_quatre_cas_plus_indetermine():
    assert {o.value for o in Organe} == {
        "cabosse",
        "feuille",
        "tronc",
        "vue_ensemble",
        "indetermine",
    }


def test_niveau_confiance_ordonne():
    """La fusion contextuelle doit pouvoir dégrader : l'ordre doit être comparable."""
    assert NiveauConfiance.FAIBLE.rang < NiveauConfiance.MOYENNE.rang
    assert NiveauConfiance.MOYENNE.rang < NiveauConfiance.ELEVEE.rang


def test_etat_revue_couvre_le_cycle_anader():
    assert {e.value for e in EtatRevue} == {"en_attente", "confirme", "corrige", "rejete"}
```

Créer `api/tests/test_vision_indisponible.py` :

```python
"""Tests de la source de vision neutre (profil CPU, ou VLM absent)."""

from __future__ import annotations

from app.services.vision.indisponible import CONSIGNE_INDISPONIBLE, VisionIndisponible


async def test_vision_indisponible_ne_decrit_rien():
    """Jamais de description inventée : None, que l'appelant devra traiter."""
    assert await VisionIndisponible().decrire((b"\xff\xd8",), "décris") is None


async def test_vision_indisponible_se_declare_absente():
    assert await VisionIndisponible().disponible() is False


def test_la_consigne_d_indisponibilite_est_explicite_et_oriente():
    """Le producteur doit comprendre ce qui se passe et quoi faire, pas voir une erreur."""
    assert "analyse" in CONSIGNE_INDISPONIBLE.lower()
    assert "ANADER" in CONSIGNE_INDISPONIBLE
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `cd api && python -m pytest tests/test_models_constat.py tests/test_vision_indisponible.py -v --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.constat'`

- [ ] **Step 3: Écrire les types de domaine**

Créer `api/app/models/constat.py` :

```python
"""Types métier du constat visuel d'une capture de parcelle.

**Constat, pas diagnostic.** Ce module ne porte aucun nom de maladie, aucun produit,
aucune posologie — c'est l'arbitrage D3 du 28/07/2026 rendu structurel : le vocabulaire
lui-même interdit le diagnostic. Un constat décrit ce qui est observé, affiche sa
confiance, et part en revue humaine.

Deux familles, comme dans ``app/models/parcelle.py`` : types de domaine immuables
(``dataclass(frozen=True)``) et schémas Pydantic d'API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class Organe(str, Enum):
    """Partie du cacaoyer identifiée sur une image (étage 1 de la cascade)."""

    CABOSSE = "cabosse"
    FEUILLE = "feuille"
    TRONC = "tronc"
    VUE_ENSEMBLE = "vue_ensemble"
    INDETERMINE = "indetermine"


class NiveauConfiance(str, Enum):
    """Confiance déclarée pour une observation ou un constat.

    Ordonné (``rang``) parce que la fusion contextuelle doit pouvoir **dégrader** :
    une observation contredite par la météo descend d'un cran.
    """

    FAIBLE = "faible"
    MOYENNE = "moyenne"
    ELEVEE = "elevee"

    @property
    def rang(self) -> int:
        """Rang ordinal, pour comparer et dégrader."""
        return {"faible": 0, "moyenne": 1, "elevee": 2}[self.value]

    def degrader(self) -> NiveauConfiance:
        """Retourne le niveau immédiatement inférieur (plancher : faible)."""
        return NiveauConfiance.FAIBLE if self.rang <= 1 else NiveauConfiance.MOYENNE


class EtatRevue(str, Enum):
    """Cycle de revue par un agent ANADER (étage 6)."""

    EN_ATTENTE = "en_attente"
    CONFIRME = "confirme"
    CORRIGE = "corrige"
    REJETE = "rejete"


@dataclass(frozen=True)
class Observation:
    """Ce qui est observé sur UNE image, sans interprétation étiologique."""

    organe: Organe
    description: str
    confiance: NiveauConfiance
    empreinte_image: str


@dataclass(frozen=True)
class Constat:
    """Constat visuel d'une capture : observations, texte rédigé, état de revue."""

    identifiant: str
    capture: str
    parcelle: str
    proprietaire: str
    observations: tuple[Observation, ...]
    texte: str
    confiance: NiveauConfiance
    cree_le: datetime
    etat_revue: EtatRevue = EtatRevue.EN_ATTENTE
    revu_par: str = ""
    correction: str = ""
    facteurs_contexte: tuple[str, ...] = field(default_factory=tuple)


# --------------------------------------------------------------- schémas d'API


class ObservationReponse(BaseModel):
    """Observation exposée au client."""

    organe: Organe
    description: str
    confiance: NiveauConfiance
    empreinte_image: str


class ConstatReponse(BaseModel):
    """Constat exposé au client."""

    identifiant: str
    capture: str
    parcelle: str
    texte: str
    confiance: NiveauConfiance
    cree_le: datetime
    etat_revue: EtatRevue
    observations: list[ObservationReponse] = Field(default_factory=list)
    facteurs_contexte: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Écrire la source de vision neutre**

Créer `api/app/services/vision/indisponible.py` :

```python
"""Source de vision neutre — profil matériel CPU, ou VLM non configuré.

Même parti pris que ``services/outils/indisponible.py`` pour la météo et les prix :
une source absente **rend une valeur neutre**, jamais une erreur brute, et surtout
jamais une donnée inventée. Le pattern « contexte vide → fabrication » a déjà coûté
un correctif sur les agents (v0.6.48) ; il ne revient pas par la vision.
"""

from __future__ import annotations

CONSIGNE_INDISPONIBLE = (
    "L'analyse d'image n'est pas disponible en ce moment. Vos photos sont bien "
    "enregistrées et rattachées à votre parcelle. Pour un avis sur ce que vous "
    "observez, montrez-les à votre agent ANADER."
)


class VisionIndisponible:
    """Source de vision neutre : ne décrit rien, et le dit."""

    async def decrire(self, images: tuple[bytes, ...], consigne: str) -> str | None:
        """Retourne ``None`` — aucune description, jamais une invention."""
        return None

    async def disponible(self) -> bool:
        """Indique que la vision est indisponible."""
        return False
```

- [ ] **Step 5: Ajouter le port au domaine**

Dans `api/app/domain/ports.py`, ajouter en fin de fichier :

```python
@runtime_checkable
class VisionPort(Protocol):
    """Contrat d'un modèle de vision décrivant des images de plantation.

    **Descripteur, pas diagnosticien.** L'implémentation ne nomme jamais une maladie :
    la consigne le lui interdit et le garde-fou de sortie le vérifie. Toujours
    mockable — aucun appel réseau en test.
    """

    async def decrire(self, images: tuple[bytes, ...], consigne: str) -> str | None:
        """Décrit les images, ou retourne None si la vision est indisponible."""
        ...

    async def disponible(self) -> bool:
        """Indique si le modèle de vision est joignable."""
        ...
```

- [ ] **Step 6: Lancer les tests pour vérifier qu'ils passent**

Run: `cd api && python -m pytest tests/test_models_constat.py tests/test_vision_indisponible.py -v --no-cov`
Expected: PASS

- [ ] **Step 7: Lint et commit**

```bash
cd api && python -m ruff format app/models/constat.py app/services/vision/indisponible.py app/domain/ports.py tests/test_models_constat.py tests/test_vision_indisponible.py && python -m ruff check app/ tests/
cd .. && git add api/app/models/constat.py api/app/services/vision/indisponible.py api/app/domain/ports.py api/tests/test_models_constat.py api/tests/test_vision_indisponible.py
git commit -m "feat(vision): port de vision, types du constat, source neutre

Constat, pas diagnostic : le vocabulaire lui-meme interdit de nommer une maladie.
Source neutre sur le moule de outils/indisponible.py — une vision absente rend une
valeur neutre et le dit, jamais une description inventee."
```

---

## Task 2: Client VLM local

**Files:**
- Create: `api/app/services/vision/vlm.py`
- Test: `api/tests/test_vision_vlm.py`

**Interfaces:**
- Consomme : `VisionPort` (T1).
- Produit : `ClientVLM(base_url: str, modele: str, timeout_s: float = 60.0)` conforme à `VisionPort`, plus `from_settings(settings) -> ClientVLM | VisionIndisponible`.

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `api/tests/test_vision_vlm.py` :

```python
"""Tests du client HTTP du modèle de vision local (aucun appel réseau réel)."""

from __future__ import annotations

import httpx
import pytest

from app.services.vision.vlm import ClientVLM

IMAGE = b"\xff\xd8\xff\xe0 fausse image jpeg"


def _client(transport: httpx.MockTransport) -> ClientVLM:
    vlm = ClientVLM(base_url="http://vision:8000", modele="qwen3-vl")
    vlm._client = httpx.AsyncClient(transport=transport, base_url="http://vision:8000")
    return vlm


async def test_decrire_retourne_le_texte_du_modele():
    def repondre(requete: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Cabosse mûre, surface régulière."}}]},
        )

    texte = await _client(httpx.MockTransport(repondre)).decrire((IMAGE,), "décris")
    assert texte == "Cabosse mûre, surface régulière."


async def test_les_images_partent_en_data_uri_base64():
    """Le VLM est servi en interne : on lui passe les octets, pas une URL publique."""
    vues: dict[str, object] = {}

    def repondre(requete: httpx.Request) -> httpx.Response:
        vues["corps"] = requete.content.decode("utf-8")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    await _client(httpx.MockTransport(repondre)).decrire((IMAGE,), "décris")
    assert "data:image/jpeg;base64," in str(vues["corps"])
    assert "http://" not in str(vues["corps"]).split("data:image")[0][-200:]


async def test_une_erreur_http_degrade_sans_lever():
    def repondre(requete: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    assert await _client(httpx.MockTransport(repondre)).decrire((IMAGE,), "décris") is None


async def test_un_timeout_degrade_sans_lever():
    def repondre(requete: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("trop lent")

    assert await _client(httpx.MockTransport(repondre)).decrire((IMAGE,), "décris") is None


async def test_une_reponse_malformee_degrade_sans_lever():
    def repondre(requete: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"pas": "ce qu'on attend"})

    assert await _client(httpx.MockTransport(repondre)).decrire((IMAGE,), "décris") is None


async def test_sans_image_on_n_appelle_meme_pas_le_modele():
    appels = {"n": 0}

    def repondre(requete: httpx.Request) -> httpx.Response:
        appels["n"] += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    assert await _client(httpx.MockTransport(repondre)).decrire((), "décris") is None
    assert appels["n"] == 0


async def test_disponible_suit_la_sonde_de_sante():
    def sain(requete: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    def malade(requete: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refus")

    assert await _client(httpx.MockTransport(sain)).disponible() is True
    assert await _client(httpx.MockTransport(malade)).disponible() is False


@pytest.mark.parametrize("nombre", [1, 3, 12])
async def test_toutes_les_images_sont_transmises(nombre):
    vues: dict[str, int] = {}

    def repondre(requete: httpx.Request) -> httpx.Response:
        vues["n"] = requete.content.decode("utf-8").count("data:image/jpeg;base64,")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    await _client(httpx.MockTransport(repondre)).decrire(tuple([IMAGE] * nombre), "décris")
    assert vues["n"] == nombre
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `cd api && python -m pytest tests/test_vision_vlm.py -v --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.vision.vlm'`

- [ ] **Step 3: Écrire le client**

Créer `api/app/services/vision/vlm.py` :

```python
"""Client HTTP du modèle de vision local (API compatible OpenAI).

Le service ``vision/`` n'est **jamais exposé publiquement** : l'API le consomme en
interne, comme elle le fait déjà d'``inference/``. Les images lui sont passées en
``data:`` URI base64 — jamais une URL que le modèle irait chercher lui-même, ce qui
ouvrirait une porte SSRF et sortirait du périmètre souverain.

**Dégradation systématique** : toute panne (HTTP, réseau, réponse malformée) rend
``None``. L'appelant traduira en consigne d'indisponibilité ; rien n'est inventé.
"""

from __future__ import annotations

import base64

import httpx

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Un constat descriptif est court : on borne la génération pour ne pas laisser le
# modèle divaguer, et pour tenir la latence sur une image de plantation.
MAX_TOKENS_DESCRIPTION = 220

# Température basse : on veut une description reproductible, pas de la créativité.
TEMPERATURE_DESCRIPTION = 0.2


class ClientVLM:
    """Adaptateur ``VisionPort`` vers un modèle de vision servi localement."""

    def __init__(
        self, base_url: str, modele: str, timeout_s: float = 60.0
    ) -> None:
        """Initialise le client.

        Args:
            base_url: URL interne du service de vision.
            modele: Nom du modèle transmis à l'API.
            timeout_s: Délai maximal d'une requête, en secondes.
        """
        self._modele = modele
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout_s)

    @classmethod
    def from_settings(cls, settings: Settings) -> ClientVLM:
        """Construit le client à partir des paramètres applicatifs."""
        return cls(
            base_url=settings.vision_url,
            modele=settings.vision_modele,
            timeout_s=settings.vision_timeout_s,
        )

    @staticmethod
    def _en_data_uri(image: bytes) -> str:
        """Encode une image en ``data:`` URI base64."""
        return "data:image/jpeg;base64," + base64.b64encode(image).decode("ascii")

    def _corps(self, images: tuple[bytes, ...], consigne: str) -> dict[str, object]:
        """Construit le corps de requête compatible OpenAI (contenu multimodal)."""
        contenu: list[dict[str, object]] = [{"type": "text", "text": consigne}]
        contenu += [
            {"type": "image_url", "image_url": {"url": self._en_data_uri(image)}}
            for image in images
        ]
        return {
            "model": self._modele,
            "messages": [{"role": "user", "content": contenu}],
            "max_tokens": MAX_TOKENS_DESCRIPTION,
            "temperature": TEMPERATURE_DESCRIPTION,
        }

    async def decrire(self, images: tuple[bytes, ...], consigne: str) -> str | None:
        """Décrit les images, ou retourne ``None`` si la vision est indisponible.

        Args:
            images: Octets des images à décrire (JPEG ou PNG).
            consigne: Consigne stricte encadrant la description.

        Returns:
            Le texte descriptif, ou ``None`` en cas d'absence d'image ou de panne.
        """
        if not images:
            return None
        try:
            reponse = await self._client.post("/v1/chat/completions", json=self._corps(images, consigne))
            reponse.raise_for_status()
            charge = reponse.json()
            texte = charge["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            logger.warning("vision_indisponible", error=str(exc))
            return None
        texte = str(texte).strip()
        return texte or None

    async def disponible(self) -> bool:
        """Sonde la disponibilité du service de vision."""
        try:
            reponse = await self._client.get("/v1/models")
            return reponse.status_code == 200
        except httpx.HTTPError:
            return False

    async def close(self) -> None:
        """Ferme le client HTTP."""
        await self._client.aclose()
```

- [ ] **Step 4: Lancer les tests pour vérifier qu'ils passent**

Run: `cd api && python -m pytest tests/test_vision_vlm.py -v --no-cov`
Expected: PASS

> Les réglages `vision_url`, `vision_modele` et `vision_timeout_s` n'existent qu'à la Tâche 7 : `from_settings` n'est donc pas testée ici, comme `ParcelleStore.from_settings` ne l'était pas en C1. C'est attendu.

- [ ] **Step 5: Lint et commit**

```bash
cd api && python -m ruff format app/services/vision/vlm.py tests/test_vision_vlm.py && python -m ruff check app/ tests/
cd .. && git add api/app/services/vision/vlm.py api/tests/test_vision_vlm.py
git commit -m "feat(vision): client HTTP du modele de vision local

Images passees en data: URI base64, jamais une URL que le modele irait chercher
(porte SSRF, et sortie du perimetre souverain). Toute panne degrade en None :
l appelant dira que la vision est indisponible, rien ne sera invente."
```

---

## Task 3: Étage 4 — la fusion contextuelle

**C'est l'avantage structurel de la plateforme**, et l'argument technique central de la présentation sur ce volet. Aucun classifieur publié sur le cacao ne fait cela, parce qu'aucun n'a d'agents météo et de RAG derrière lui.

**Files:**
- Create: `api/app/application/fusion_contextuelle.py`
- Test: `api/tests/test_fusion_contextuelle.py`

**Interfaces:**
- Consomme : `NiveauConfiance` (T1).
- Produit :
  - `dataclass(frozen=True) ContexteParcelle(pluie_mm_14j: float | None, saison: str, localite: str, alertes_deforestation: int | None)`
  - `dataclass(frozen=True) Fusion(confiance: NiveauConfiance, facteurs: tuple[str, ...])`
  - `fusionner(description: str, confiance: NiveauConfiance, contexte: ContexteParcelle) -> Fusion`
  - Constantes : `PLUIE_HUMIDE_MM = 60.0`, `PLUIE_SECHE_MM = 5.0`

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `api/tests/test_fusion_contextuelle.py` :

```python
"""Tests de l'étage 4 — croisement du constat visuel et du contexte de la parcelle."""

from __future__ import annotations

from app.application.fusion_contextuelle import ContexteParcelle, fusionner
from app.models.constat import NiveauConfiance

POURRITURE = "Cabosses présentant des taches brunes étendues et un début de pourriture."
SAIN = "Cabosses de couleur homogène, surface régulière, aucune tache."


def _contexte(**surcharges) -> ContexteParcelle:
    defauts = {
        "pluie_mm_14j": 120.0,
        "saison": "grande saison des pluies",
        "localite": "Daloa",
        "alertes_deforestation": 0,
    }
    return ContexteParcelle(**{**defauts, **surcharges})


def test_humidite_prolongee_conforte_une_observation_de_pourriture():
    fusion = fusionner(POURRITURE, NiveauConfiance.MOYENNE, _contexte(pluie_mm_14j=150.0))
    assert fusion.confiance is NiveauConfiance.MOYENNE
    assert any("plui" in f.lower() for f in fusion.facteurs)


def test_trois_semaines_seches_degradent_une_observation_de_pourriture():
    """C'est le cœur de l'étage 4 : le contexte contredit l'image, on descend d'un cran."""
    fusion = fusionner(POURRITURE, NiveauConfiance.ELEVEE, _contexte(pluie_mm_14j=1.0))
    assert fusion.confiance is NiveauConfiance.MOYENNE
    assert any("sec" in f.lower() for f in fusion.facteurs)


def test_la_degradation_a_un_plancher():
    fusion = fusionner(POURRITURE, NiveauConfiance.FAIBLE, _contexte(pluie_mm_14j=0.0))
    assert fusion.confiance is NiveauConfiance.FAIBLE


def test_une_observation_saine_n_est_pas_degradee_par_la_secheresse():
    fusion = fusionner(SAIN, NiveauConfiance.ELEVEE, _contexte(pluie_mm_14j=1.0))
    assert fusion.confiance is NiveauConfiance.ELEVEE


def test_meteo_absente_degrade_la_confiance_sans_inventer():
    """Souveraineté : sans donnée, on ne conforte rien — on l'écrit."""
    fusion = fusionner(POURRITURE, NiveauConfiance.ELEVEE, _contexte(pluie_mm_14j=None))
    assert fusion.confiance is NiveauConfiance.MOYENNE
    assert any("indisponible" in f.lower() for f in fusion.facteurs)


def test_la_localite_figure_toujours_dans_les_facteurs():
    fusion = fusionner(SAIN, NiveauConfiance.MOYENNE, _contexte(localite="Soubré"))
    assert any("Soubré" in f for f in fusion.facteurs)


def test_les_facteurs_sont_lisibles_par_un_producteur():
    """Pas de jargon ni de code : ces phrases finissent dans le constat."""
    fusion = fusionner(POURRITURE, NiveauConfiance.MOYENNE, _contexte())
    for facteur in fusion.facteurs:
        assert facteur[0].isupper() or facteur[0].isdigit()
        assert "_" not in facteur


def test_aucun_facteur_ne_nomme_une_maladie():
    """D3 : la fusion explique un contexte, elle ne conclut jamais à une maladie."""
    fusion = fusionner(POURRITURE, NiveauConfiance.ELEVEE, _contexte(pluie_mm_14j=150.0))
    joint = " ".join(fusion.facteurs).lower()
    for interdit in ("pourriture brune", "phytophthora", "swollen shoot", "mirides"):
        assert interdit not in joint
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `cd api && python -m pytest tests/test_fusion_contextuelle.py -v --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.application.fusion_contextuelle'`

- [ ] **Step 3: Écrire l'implémentation**

Créer `api/app/application/fusion_contextuelle.py` :

```python
"""Étage 4 de la cascade — croise l'observation visuelle et le contexte de la parcelle.

**C'est ce qu'aucun classifieur publié sur le cacao ne fait**, faute d'agents météo et
de RAG derrière lui. Une observation évoquant une pourriture après trois semaines
sèches est douteuse : la pourriture brune se développe avec l'humidité prolongée. On
ne conclut rien — on **dégrade la confiance** et on **écrit pourquoi**.

Orchestration pure : aucun réseau, aucun accès disque, entièrement testable.

**D3 rappelé** : les facteurs produits ici expliquent un contexte. Ils ne nomment
jamais une maladie, et un test le vérifie.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.constat import NiveauConfiance

# Cumul de pluie sur 14 jours au-delà duquel l'humidité est jugée prolongée.
PLUIE_HUMIDE_MM = 60.0

# Cumul en deçà duquel la période est jugée sèche.
PLUIE_SECHE_MM = 5.0

# Termes descriptifs (jamais un nom de maladie) qui, dans une description, évoquent
# une atteinte favorisée par l'humidité. On reste au niveau du SYMPTÔME OBSERVÉ.
_SIGNES_HUMIDITE = ("pourri", "tache brune", "taches brunes", "moisiss", "chancre")


@dataclass(frozen=True)
class ContexteParcelle:
    """Ce que la plateforme sait déjà de la parcelle, au moment du constat."""

    pluie_mm_14j: float | None
    saison: str
    localite: str
    alertes_deforestation: int | None


@dataclass(frozen=True)
class Fusion:
    """Confiance après croisement, et les facteurs qui l'expliquent."""

    confiance: NiveauConfiance
    facteurs: tuple[str, ...]


def _evoque_une_atteinte_humide(description: str) -> bool:
    """Indique si la description mentionne un signe favorisé par l'humidité."""
    minuscules = description.lower()
    return any(signe in minuscules for signe in _SIGNES_HUMIDITE)


def fusionner(
    description: str, confiance: NiveauConfiance, contexte: ContexteParcelle
) -> Fusion:
    """Croise une observation visuelle avec le contexte connu de la parcelle.

    Args:
        description: Texte descriptif produit par le modèle de vision.
        confiance: Confiance de l'observation avant croisement.
        contexte: Météo récente, saison, localité, alertes de la zone.

    Returns:
        La confiance après croisement et les facteurs, rédigés pour un producteur.
    """
    facteurs: list[str] = [f"Parcelle située à {contexte.localite}."]
    if contexte.saison:
        facteurs.append(f"Période : {contexte.saison}.")

    resultat = confiance
    if not _evoque_une_atteinte_humide(description):
        return Fusion(confiance=resultat, facteurs=tuple(facteurs))

    if contexte.pluie_mm_14j is None:
        facteurs.append(
            "Relevé de pluie indisponible pour cette zone : le constat n'a pas pu "
            "être recoupé avec la météo récente."
        )
        resultat = resultat.degrader()
    elif contexte.pluie_mm_14j >= PLUIE_HUMIDE_MM:
        facteurs.append(
            f"Il est tombé {contexte.pluie_mm_14j:.0f} mm de pluie sur les 14 derniers "
            "jours : une humidité prolongée favorise ce type d'atteinte."
        )
    elif contexte.pluie_mm_14j <= PLUIE_SECHE_MM:
        facteurs.append(
            f"Il n'est tombé que {contexte.pluie_mm_14j:.0f} mm de pluie sur les 14 "
            "derniers jours : ce temps sec cadre mal avec ce qui est observé, la "
            "confiance est abaissée."
        )
        resultat = resultat.degrader()

    return Fusion(confiance=resultat, facteurs=tuple(facteurs))
```

- [ ] **Step 4: Lancer les tests pour vérifier qu'ils passent**

Run: `cd api && python -m pytest tests/test_fusion_contextuelle.py -v --no-cov`
Expected: PASS — 8 tests

- [ ] **Step 5: Lint et commit**

```bash
cd api && python -m ruff format app/application/fusion_contextuelle.py tests/test_fusion_contextuelle.py && python -m ruff check app/ tests/
cd .. && git add api/app/application/fusion_contextuelle.py api/tests/test_fusion_contextuelle.py
git commit -m "feat(vision): etage 4 - fusion contextuelle du constat et de la meteo

Une observation evoquant une atteinte humide apres trois semaines seches est
douteuse : on ne conclut rien, on degrade la confiance et on ecrit pourquoi.
Meteo absente = degradation aussi : sans donnee on ne conforte rien.
Les facteurs n emploient jamais un nom de maladie (D3), un test le verifie."
```

---

## Task 4: Étage 5 — rédaction du constat et garde-fou anti-diagnostic

**Files:**
- Create: `api/app/services/prompts_constat.py`
- Modify: `api/app/services/guardrails.py`
- Create: `api/app/application/constat_visuel.py`
- Test: `api/tests/test_prompts_constat.py`, `api/tests/test_guardrails_constat.py`, `api/tests/test_constat_visuel.py`

**Interfaces:**
- Consomme : `VisionPort` (T1), `InferencePort`, `fusionner`/`ContexteParcelle` (T3), `guardrails.verifier_reponse`.
- Produit :
  - `CONSIGNE_DESCRIPTION` (str) et `consigne_redaction(facteurs: tuple[str, ...]) -> str`
  - `guardrails.contient_diagnostic(texte: str) -> str | None` — rend le terme fautif, ou None
  - `ServiceConstatVisuel(vision, inference)` avec `async def analyser(images, contexte) -> Constat | None`

- [ ] **Step 1: Écrire les tests du garde-fou**

Créer `api/tests/test_guardrails_constat.py` :

```python
"""Tests du garde-fou anti-diagnostic sur les sorties de constat visuel (D3)."""

from __future__ import annotations

import pytest

from app.services.guardrails import contient_diagnostic


@pytest.mark.parametrize(
    "texte",
    [
        "Il s'agit de la pourriture brune des cabosses.",
        "Ces symptômes évoquent le swollen shoot.",
        "Attaque de mirides caractérisée.",
        "Le Phytophthora est en cause.",
        "C'est une anthracnose.",
    ],
)
def test_un_nom_de_maladie_est_refuse(texte):
    assert contient_diagnostic(texte) is not None


@pytest.mark.parametrize(
    "texte",
    [
        "Les cabosses présentent des taches brunes étendues sur environ un tiers de leur surface.",
        "Feuillage vert clair, quelques feuilles tachetées, port général affaissé.",
        "Vue d'ensemble : ombrage dense, sous-bois peu entretenu.",
    ],
)
def test_une_description_de_symptome_passe(texte):
    """Décrire ce qu'on voit est autorisé ; nommer la cause ne l'est pas."""
    assert contient_diagnostic(texte) is None


def test_le_terme_fautif_est_rendu_pour_la_journalisation():
    fautif = contient_diagnostic("Ces cabosses ont la pourriture brune.")
    assert fautif is not None
    assert "pourriture brune" in fautif.lower()


def test_la_detection_ignore_la_casse_et_les_accents():
    assert contient_diagnostic("POURRITURE BRUNE confirmée") is not None
    assert contient_diagnostic("swollen-shoot probable") is not None
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `cd api && python -m pytest tests/test_guardrails_constat.py -v --no-cov`
Expected: FAIL — `ImportError: cannot import name 'contient_diagnostic'`

- [ ] **Step 3: Implémenter le garde-fou**

Dans `api/app/services/guardrails.py`, ajouter en fin de fichier :

```python
# Noms de maladies et de ravageurs du cacaoyer. Les prononcer, c'est diagnostiquer —
# ce que D3 interdit tant que le verrou de rappel par classe n'est pas franchi
# (spec §7.5). On décrit un symptôme observé ; on ne nomme jamais sa cause.
_NOMS_MALADIES = (
    "pourriture brune",
    "phytophthora",
    "swollen shoot",
    "swollen-shoot",
    "cssv",
    "mirides",
    "miride",
    "capsides",
    "anthracnose",
    "fusariose",
    "armillaire",
    "moniliose",
    "balai de sorciere",
)

_RE_MALADIES = _compiler(_NOMS_MALADIES)


def contient_diagnostic(texte: str) -> str | None:
    """Retourne le nom de maladie trouvé dans le texte, ou None.

    Garde-fou de sortie propre au constat visuel (D3, arbitrage du 28/07/2026) : le
    système décrit ce qu'il observe et ne nomme jamais la cause. Un constat qui nomme
    une maladie est rejeté, pas corrigé — on ne réécrit pas une sortie compromise.

    Args:
        texte: Texte du constat à vérifier.

    Returns:
        Le terme fautif (pour la journalisation), ou ``None`` si le texte est sain.
    """
    normalise = _normaliser(texte)
    for terme, motif in zip(_NOMS_MALADIES, _RE_MALADIES, strict=True):
        if motif.search(normalise):
            return terme
    return None
```

> Vérifie la signature réelle de `_compiler` et `_normaliser` en tête de `guardrails.py` avant d'écrire : `_compiler` rend un `tuple[re.Pattern, ...]` et `_normaliser` retire les accents et abaisse la casse. Si `_compiler` produit des motifs à mot entier, `swollen-shoot` peut ne pas matcher — dans ce cas, ajoute la variante nécessaire à `_NOMS_MALADIES` et **dis-le dans ton rapport**.

- [ ] **Step 4: Lancer les tests du garde-fou**

Run: `cd api && python -m pytest tests/test_guardrails_constat.py -v --no-cov`
Expected: PASS

- [ ] **Step 5: Écrire les tests des consignes**

Créer `api/tests/test_prompts_constat.py` :

```python
"""Tests des consignes encadrant le constat visuel."""

from __future__ import annotations

from app.services.prompts_constat import CONSIGNE_DESCRIPTION, consigne_redaction


def test_la_consigne_de_description_interdit_de_nommer_une_maladie():
    minuscules = CONSIGNE_DESCRIPTION.lower()
    assert "ne nomme" in minuscules or "sans nommer" in minuscules
    assert "maladie" in minuscules


def test_la_consigne_de_description_interdit_produit_et_dosage():
    minuscules = CONSIGNE_DESCRIPTION.lower()
    assert "produit" in minuscules
    assert "dose" in minuscules or "dosage" in minuscules


def test_la_consigne_de_redaction_reprend_les_facteurs_de_contexte():
    consigne = consigne_redaction(("Parcelle située à Daloa.", "Période : saison sèche."))
    assert "Daloa" in consigne
    assert "saison sèche" in consigne


def test_la_consigne_de_redaction_impose_l_orientation_anader():
    assert "ANADER" in consigne_redaction(())


def test_la_consigne_de_redaction_tient_sans_facteur():
    """Aucun contexte disponible : la consigne reste valide et ne ment pas."""
    consigne = consigne_redaction(())
    assert consigne
    assert "ANADER" in consigne
```

- [ ] **Step 6: Écrire les consignes**

Créer `api/app/services/prompts_constat.py` :

```python
"""Consignes encadrant le constat visuel (étages 1 et 5 de la cascade).

Le levier est la **consigne**, pas le plafond de tokens — leçon acquise en juillet sur
le dialogue naturel. Ces textes sont donc écrits comme des interdits explicites, et
doublés d'un garde-fou de sortie (``guardrails.contient_diagnostic``) : on ne fait pas
confiance au modèle pour respecter une consigne, on vérifie.
"""

from __future__ import annotations

CONSIGNE_DESCRIPTION = (
    "Tu es un observateur agronome. Décris FACTUELLEMENT ce que montrent ces photos "
    "de cacaoyer : partie de la plante visible (cabosse, feuille, tronc, vue "
    "d'ensemble), couleurs, taches, textures, étendue approximative de ce que tu "
    "observes, état de l'ombrage et de l'entretien.\n"
    "INTERDITS ABSOLUS : ne nomme JAMAIS une maladie ni un ravageur. Ne propose "
    "JAMAIS un produit, un traitement ou une dose. N'affirme pas une cause. Si une "
    "photo est inexploitable, dis-le simplement.\n"
    "Réponds en français simple, en trois phrases au maximum."
)


def consigne_redaction(facteurs: tuple[str, ...]) -> str:
    """Construit la consigne de rédaction du constat destiné au producteur.

    Args:
        facteurs: Éléments de contexte issus de la fusion (étage 4), déjà rédigés.

    Returns:
        La consigne complète, contexte inclus.
    """
    contexte = "\n".join(f"- {facteur}" for facteur in facteurs)
    bloc = f"\nÉléments de contexte connus :\n{contexte}\n" if facteurs else "\n"
    return (
        "Rédige un constat court et clair pour un producteur de cacao ivoirien, à "
        "partir de l'observation ci-dessus."
        f"{bloc}"
        "Règles : ne nomme aucune maladie et aucun ravageur ; ne propose aucun produit "
        "ni aucune dose. Tu peux conseiller des gestes sans produit (récolte sanitaire, "
        "évacuation des cabosses atteintes, élagage, aération). Termine en invitant le "
        "producteur à montrer ces photos à son agent ANADER pour confirmation.\n"
        "Français simple, cinq phrases au maximum."
    )
```

- [ ] **Step 7: Écrire les tests de l'assemblage**

Créer `api/tests/test_constat_visuel.py` :

```python
"""Tests de l'étage 5 — assemblage du constat visuel."""

from __future__ import annotations

import pytest

from app.application.constat_visuel import ServiceConstatVisuel
from app.application.fusion_contextuelle import ContexteParcelle
from app.models.constat import NiveauConfiance, Organe

IMAGES = ((b"\xff\xd8 image", "a" * 64),)


class FauxVision:
    """Port de vision contrôlé par le test (aucun réseau)."""

    def __init__(self, texte: str | None = "Cabosses tachetées de brun sur un tiers.") -> None:
        self.texte = texte
        self.consignes: list[str] = []

    async def decrire(self, images, consigne):
        self.consignes.append(consigne)
        return self.texte

    async def disponible(self) -> bool:
        return self.texte is not None


class FausseInference:
    """Port d'inférence contrôlé par le test."""

    def __init__(self, reponse: str = "Vos cabosses présentent des taches brunes. Montrez ces photos à votre agent ANADER.") -> None:
        self.reponse = reponse
        self.appels: list[str] = []

    async def generer(self, question: str, **_: object) -> str:
        self.appels.append(question)
        return self.reponse

    def generer_stream(self, *_: object, **__: object):
        raise NotImplementedError

    async def ready(self) -> bool:
        return True


def _contexte() -> ContexteParcelle:
    return ContexteParcelle(
        pluie_mm_14j=120.0,
        saison="grande saison des pluies",
        localite="Daloa",
        alertes_deforestation=0,
    )


async def test_le_constat_est_produit_avec_ses_observations():
    service = ServiceConstatVisuel(FauxVision(), FausseInference())
    constat = await service.analyser(IMAGES, _contexte())
    assert constat is not None
    assert len(constat.observations) == 1
    assert constat.observations[0].empreinte_image == "a" * 64
    assert constat.texte


async def test_sans_vision_disponible_aucun_constat_n_est_invente():
    """Anti-fabrication : None, pas une description imaginée (v0.6.48)."""
    service = ServiceConstatVisuel(FauxVision(texte=None), FausseInference())
    assert await service.analyser(IMAGES, _contexte()) is None


async def test_un_constat_qui_nomme_une_maladie_est_rejete():
    """D3 : on ne reecrit pas une sortie compromise, on la refuse."""
    inference = FausseInference(reponse="C'est la pourriture brune, traitez vite.")
    service = ServiceConstatVisuel(FauxVision(), inference)
    assert await service.analyser(IMAGES, _contexte()) is None


async def test_un_constat_qui_donne_un_produit_est_rejete():
    inference = FausseInference(reponse="Appliquez un fongicide cuprique sur les cabosses.")
    service = ServiceConstatVisuel(FauxVision(), inference)
    assert await service.analyser(IMAGES, _contexte()) is None


async def test_la_consigne_de_description_est_bien_transmise_au_vlm():
    vision = FauxVision()
    await ServiceConstatVisuel(vision, FausseInference()).analyser(IMAGES, _contexte())
    assert "ne nomme JAMAIS une maladie" in vision.consignes[0]


async def test_les_facteurs_de_contexte_remontent_dans_le_constat():
    service = ServiceConstatVisuel(FauxVision(), FausseInference())
    constat = await service.analyser(IMAGES, _contexte())
    assert constat is not None
    assert any("Daloa" in f for f in constat.facteurs_contexte)


async def test_le_temps_sec_degrade_la_confiance_du_constat():
    service = ServiceConstatVisuel(FauxVision(), FausseInference())
    sec = ContexteParcelle(
        pluie_mm_14j=1.0, saison="saison sèche", localite="Daloa", alertes_deforestation=0
    )
    constat = await service.analyser(IMAGES, sec)
    assert constat is not None
    assert constat.confiance.rang < NiveauConfiance.MOYENNE.rang + 1


@pytest.mark.parametrize(
    "texte,attendu",
    [
        ("Cabosse mûre, surface régulière.", Organe.CABOSSE),
        ("Feuilles vert clair, quelques taches.", Organe.FEUILLE),
        ("Tronc portant des chancres humides.", Organe.TRONC),
        ("Vue d'ensemble de la plantation, ombrage dense.", Organe.VUE_ENSEMBLE),
        ("Image difficile à interpréter.", Organe.INDETERMINE),
    ],
)
async def test_l_organe_est_deduit_de_la_description(texte, attendu):
    service = ServiceConstatVisuel(FauxVision(texte=texte), FausseInference())
    constat = await service.analyser(IMAGES, _contexte())
    assert constat is not None
    assert constat.observations[0].organe is attendu
```

- [ ] **Step 8: Écrire l'assemblage**

Créer `api/app/application/constat_visuel.py` :

```python
"""Étage 5 de la cascade — assemble le constat visuel destiné au producteur.

Enchaîne : description par le modèle de vision (étage 1) → croisement contextuel
(étage 4) → rédaction par le modèle de conseil → **garde-fou de sortie**.

Deux refus catégoriques, tous deux vérifiés et non contournables :

* **Vision indisponible → aucun constat.** On rend ``None``, l'appelant dira que
  l'analyse n'est pas disponible. Jamais une description imaginée : le pattern
  « contexte vide → fabrication » a déjà coûté un correctif (v0.6.48).
* **Sortie compromise → rejet, pas réécriture.** Un constat qui nomme une maladie, un
  produit ou une dose est jeté. On ne rafistole pas une sortie qui a franchi un
  interdit ; on préfère ne rien rendre.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.application.fusion_contextuelle import ContexteParcelle, fusionner
from app.core.logging import get_logger
from app.domain.ports import InferencePort, VisionPort
from app.models.constat import Constat, NiveauConfiance, Observation, Organe
from app.services import guardrails
from app.services.prompts_constat import CONSIGNE_DESCRIPTION, consigne_redaction

logger = get_logger(__name__)

# Un constat tient en quelques phrases : on borne la génération.
MAX_TOKENS_CONSTAT = 260

# Indices lexicaux du tri d'organe (étage 1). Déterministe, explicable, testable —
# le VLM décrit, ce petit tri classe. Un classifieur affiné le remplacera (étage 1
# définitif) sans changer ce contrat.
_INDICES_ORGANE: tuple[tuple[Organe, tuple[str, ...]], ...] = (
    (Organe.CABOSSE, ("cabosse", "cabosses", "fruit", "fruits")),
    (Organe.FEUILLE, ("feuille", "feuilles", "feuillage")),
    (Organe.TRONC, ("tronc", "rameau", "rameaux", "branche", "branches", "ecorce", "écorce")),
    (Organe.VUE_ENSEMBLE, ("vue d'ensemble", "plantation", "parcelle", "ombrage", "sous-bois")),
)


def _deduire_organe(description: str) -> Organe:
    """Déduit l'organe observé depuis la description, ou ``INDETERMINE``."""
    minuscules = description.lower()
    for organe, indices in _INDICES_ORGANE:
        if any(indice in minuscules for indice in indices):
            return organe
    return Organe.INDETERMINE


class ServiceConstatVisuel:
    """Produit un constat visuel non diagnostique à partir d'images de capture."""

    def __init__(self, vision: VisionPort, inference: InferencePort) -> None:
        """Initialise le service.

        Args:
            vision: Port du modèle de vision (mockable).
            inference: Port du modèle de conseil, qui rédige le constat.
        """
        self._vision = vision
        self._inference = inference

    async def analyser(
        self, images: tuple[tuple[bytes, str], ...], contexte: ContexteParcelle
    ) -> Constat | None:
        """Produit le constat d'un jeu d'images, ou ``None``.

        Args:
            images: Couples ``(octets, empreinte_sha256)`` des images recevables.
            contexte: Contexte connu de la parcelle (météo, saison, localité).

        Returns:
            Le constat, ou ``None`` si la vision est indisponible ou si la sortie a
            franchi un interdit.
        """
        if not images:
            return None
        description = await self._vision.decrire(
            tuple(octets for octets, _ in images), CONSIGNE_DESCRIPTION
        )
        if not description:
            logger.info("constat_vision_indisponible")
            return None

        fautif = guardrails.contient_diagnostic(description)
        if fautif:
            logger.warning("constat_description_compromise", terme=fautif)
            return None

        fusion = fusionner(description, NiveauConfiance.MOYENNE, contexte)
        texte = await self._inference.generer(
            question=f"{description}\n\n{consigne_redaction(fusion.facteurs)}",
            temperature=0.3,
            max_tokens=MAX_TOKENS_CONSTAT,
        )

        fautif = guardrails.contient_diagnostic(texte)
        if fautif:
            logger.warning("constat_sortie_compromise", terme=fautif)
            return None
        if guardrails.verifier_reponse(texte) is not None:
            logger.warning("constat_sortie_refusee_par_garde_fou")
            return None

        organe = _deduire_organe(description)
        observations = tuple(
            Observation(
                organe=organe,
                description=description,
                confiance=fusion.confiance,
                empreinte_image=empreinte,
            )
            for _, empreinte in images
        )
        return Constat(
            identifiant=uuid4().hex,
            capture="",
            parcelle="",
            proprietaire="",
            observations=observations,
            texte=texte.strip(),
            confiance=fusion.confiance,
            cree_le=datetime.now(UTC),
            facteurs_contexte=fusion.facteurs,
        )
```

- [ ] **Step 9: Lancer les tests**

Run: `cd api && python -m pytest tests/test_prompts_constat.py tests/test_guardrails_constat.py tests/test_constat_visuel.py -v --no-cov`
Expected: PASS

- [ ] **Step 10: Vérifier la non-régression des garde-fous existants**

Run: `cd api && python -m pytest tests/test_guardrails.py -v --no-cov`
Expected: PASS — aucun test existant modifié.

- [ ] **Step 11: Lint et commit**

```bash
cd api && python -m ruff format app/services/prompts_constat.py app/services/guardrails.py app/application/constat_visuel.py tests/test_prompts_constat.py tests/test_guardrails_constat.py tests/test_constat_visuel.py && python -m ruff check app/ tests/
cd .. && git add api/app/services/prompts_constat.py api/app/services/guardrails.py api/app/application/constat_visuel.py api/tests/test_prompts_constat.py api/tests/test_guardrails_constat.py api/tests/test_constat_visuel.py
git commit -m "feat(vision): etage 5 - redaction du constat et garde-fou anti-diagnostic

Le levier est la consigne, pas le plafond de tokens — mais on ne fait pas confiance
au modele pour la respecter : contient_diagnostic verifie la sortie et REJETTE, sans
reecrire. Un constat qui nomme une maladie, un produit ou une dose est jete.

Vision indisponible = aucun constat, jamais une description imaginee (v0.6.48)."
```

---

## Task 5: Persistance et endpoint du constat

**Files:**
- Modify: `api/app/core/parcelles_store.py` (migration 2 : table `constats`)
- Create: `api/app/services/constats.py`
- Modify: `api/app/routers/parcelles.py`
- Modify: `api/app/domain/ports.py` (extension de `ParcelleStorePort`)
- Test: `api/tests/test_constats_store.py`, `api/tests/test_constats_api.py`

**Interfaces:**
- Consomme : `Constat` (T1), `ServiceConstatVisuel` (T4), `ServiceParcelles` (C1).
- Produit :
  - `ParcelleStore.enregistrer_constat(constat) -> Constat`, `obtenir_constat(identifiant, proprietaire) -> Constat | None`, `lister_constats_en_attente(limite=50) -> list[Constat]`, `reviser_constat(identifiant, etat, revu_par, correction) -> Constat | None`
  - `ServiceConstats(store, service_parcelles, constat_visuel, dossier_captures)` avec `async def produire(parcelle, capture_id, proprietaire) -> Constat`
  - Exceptions `CaptureIntrouvable`, `VisionIndisponibleErreur`
  - Route `POST /v1/parcelles/{identifiant}/captures/{capture_id}/constat`

- [ ] **Step 1: Écrire les tests du dépôt**

Créer `api/tests/test_constats_store.py` :

```python
"""Tests de la persistance des constats visuels."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.parcelles_store import ParcelleStore
from app.models.constat import Constat, EtatRevue, NiveauConfiance, Observation, Organe

DEVICE = "appareil-a"


@pytest.fixture
async def store(tmp_path: Path) -> ParcelleStore:
    depot = ParcelleStore(tmp_path / "parcelles.db")
    await depot.initialiser()
    return depot


def _constat(identifiant: str = "c1", **surcharges) -> Constat:
    defauts = {
        "identifiant": identifiant,
        "capture": "cap1",
        "parcelle": "p1",
        "proprietaire": DEVICE,
        "observations": (
            Observation(
                organe=Organe.CABOSSE,
                description="Taches brunes sur un tiers de la cabosse.",
                confiance=NiveauConfiance.MOYENNE,
                empreinte_image="a" * 64,
            ),
        ),
        "texte": "Vos cabosses présentent des taches. Montrez-les à votre agent ANADER.",
        "confiance": NiveauConfiance.MOYENNE,
        "cree_le": datetime.now(UTC),
        "facteurs_contexte": ("Parcelle située à Daloa.",),
    }
    return Constat(**{**defauts, **surcharges})


async def test_enregistrer_puis_relire_un_constat(store: ParcelleStore):
    await store.enregistrer_constat(_constat())
    relu = await store.obtenir_constat("c1", DEVICE)
    assert relu is not None
    assert relu.texte.startswith("Vos cabosses")
    assert relu.observations[0].organe is Organe.CABOSSE
    assert relu.facteurs_contexte == ("Parcelle située à Daloa.",)


async def test_un_autre_appareil_ne_voit_pas_le_constat(store: ParcelleStore):
    await store.enregistrer_constat(_constat())
    assert await store.obtenir_constat("c1", "appareil-b") is None


async def test_un_constat_naît_en_attente_de_revue(store: ParcelleStore):
    await store.enregistrer_constat(_constat())
    en_attente = await store.lister_constats_en_attente()
    assert [c.identifiant for c in en_attente] == ["c1"]


async def test_reviser_confirme_sort_le_constat_de_la_file(store: ParcelleStore):
    await store.enregistrer_constat(_constat())
    revu = await store.reviser_constat("c1", EtatRevue.CONFIRME, "agent-anader-7", "")
    assert revu is not None
    assert revu.etat_revue is EtatRevue.CONFIRME
    assert revu.revu_par == "agent-anader-7"
    assert await store.lister_constats_en_attente() == []


async def test_reviser_corrige_conserve_la_correction(store: ParcelleStore):
    """C'est cette correction qui alimentera le jeu de donnees ivoirien (etage 6)."""
    await store.enregistrer_constat(_constat())
    revu = await store.reviser_constat(
        "c1", EtatRevue.CORRIGE, "agent-anader-7", "Ombrage insuffisant, pas une atteinte."
    )
    assert revu is not None
    assert revu.correction.startswith("Ombrage")


async def test_reviser_un_constat_absent_rend_none(store: ParcelleStore):
    assert await store.reviser_constat("inconnu", EtatRevue.CONFIRME, "x", "") is None


async def test_la_file_est_bornee(store: ParcelleStore):
    for indice in range(5):
        await store.enregistrer_constat(_constat(identifiant=f"c{indice}"))
    assert len(await store.lister_constats_en_attente(limite=3)) == 3


async def test_les_operations_sur_un_depot_non_pret_ne_levent_pas(tmp_path: Path):
    depot = ParcelleStore(tmp_path / "jamais.db")
    assert await depot.obtenir_constat("c1", DEVICE) is None
    assert await depot.lister_constats_en_attente() == []
    assert await depot.reviser_constat("c1", EtatRevue.CONFIRME, "x", "") is None
    assert await depot.enregistrer_constat(_constat()) is not None
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `cd api && python -m pytest tests/test_constats_store.py -v --no-cov`
Expected: FAIL — `AttributeError: 'ParcelleStore' object has no attribute 'enregistrer_constat'`

- [ ] **Step 3: Ajouter la migration et les opérations**

Dans `api/app/core/parcelles_store.py`, **ajouter une entrée à la fin** de `_MIGRATIONS` (ne jamais modifier une migration publiée) :

```python
        # Migration 2 (V3, chantier C2) : constats visuels et leur cycle de revue.
        # Les observations et les facteurs de contexte sont sérialisés en JSON, comme
        # les images d'une capture : le dépôt reste à deux tables métier.
        """
        CREATE TABLE IF NOT EXISTS constats (
            id                 TEXT PRIMARY KEY,
            capture_id         TEXT NOT NULL,
            parcelle_id        TEXT NOT NULL,
            proprietaire       TEXT NOT NULL,
            observations_json  TEXT NOT NULL,
            facteurs_json      TEXT NOT NULL,
            texte              TEXT NOT NULL,
            confiance          TEXT NOT NULL,
            etat_revue         TEXT NOT NULL,
            revu_par           TEXT NOT NULL DEFAULT '',
            correction         TEXT NOT NULL DEFAULT '',
            cree_le            TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_constats_revue ON constats(etat_revue, cree_le);
        CREATE INDEX IF NOT EXISTS idx_constats_proprio ON constats(proprietaire, cree_le DESC);
        """,
```

Puis ajouter les méthodes, en suivant exactement le style des méthodes de capture existantes (sérialisation par méthodes de classe, `asyncio.to_thread`, verrou en écriture, retour neutre si `not self._pret`) :

```python
    @classmethod
    def _observations_en_json(cls, observations: tuple[Observation, ...]) -> str:
        """Sérialise les observations d'un constat."""
        return json.dumps(
            [
                {
                    "organe": o.organe.value,
                    "description": o.description,
                    "confiance": o.confiance.value,
                    "empreinte_image": o.empreinte_image,
                }
                for o in observations
            ]
        )

    @classmethod
    def _observations_depuis_json(cls, brut: str) -> tuple[Observation, ...]:
        """Reconstruit les observations d'un constat."""
        return tuple(
            Observation(
                organe=Organe(c["organe"]),
                description=c["description"],
                confiance=NiveauConfiance(c["confiance"]),
                empreinte_image=c["empreinte_image"],
            )
            for c in json.loads(brut)
        )

    @classmethod
    def _ligne_en_constat(cls, ligne: sqlite3.Row) -> Constat:
        """Reconstruit un constat depuis une ligne SQL."""
        return Constat(
            identifiant=ligne["id"],
            capture=ligne["capture_id"],
            parcelle=ligne["parcelle_id"],
            proprietaire=ligne["proprietaire"],
            observations=cls._observations_depuis_json(ligne["observations_json"]),
            texte=ligne["texte"],
            confiance=NiveauConfiance(ligne["confiance"]),
            cree_le=datetime.fromisoformat(ligne["cree_le"]),
            etat_revue=EtatRevue(ligne["etat_revue"]),
            revu_par=ligne["revu_par"],
            correction=ligne["correction"],
            facteurs_contexte=tuple(json.loads(ligne["facteurs_json"])),
        )

    async def enregistrer_constat(self, constat: Constat) -> Constat:
        """Persiste un constat visuel."""
        if not self._pret:
            return constat
        async with self._verrou:
            await asyncio.to_thread(self._inserer_constat, constat)
        return constat

    def _inserer_constat(self, constat: Constat) -> None:
        """Insère un constat (appelé dans un thread)."""
        with closing(self._connexion()) as connexion:
            connexion.execute(
                "INSERT INTO constats (id, capture_id, parcelle_id, proprietaire, "
                "observations_json, facteurs_json, texte, confiance, etat_revue, "
                "revu_par, correction, cree_le) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    constat.identifiant,
                    constat.capture,
                    constat.parcelle,
                    constat.proprietaire,
                    self._observations_en_json(constat.observations),
                    json.dumps(list(constat.facteurs_contexte)),
                    constat.texte,
                    constat.confiance.value,
                    constat.etat_revue.value,
                    constat.revu_par,
                    constat.correction,
                    constat.cree_le.isoformat(),
                ),
            )
            connexion.commit()

    async def obtenir_constat(self, identifiant: str, proprietaire: str) -> Constat | None:
        """Retourne un constat de cet appareil, ou None."""
        if not self._pret:
            return None
        return await asyncio.to_thread(self._lire_constat, identifiant, proprietaire)

    def _lire_constat(self, identifiant: str, proprietaire: str) -> Constat | None:
        """Lit un constat (appelé dans un thread)."""
        with closing(self._connexion()) as connexion:
            ligne = connexion.execute(
                "SELECT * FROM constats WHERE id = ? AND proprietaire = ?",
                (identifiant, proprietaire),
            ).fetchone()
        return self._ligne_en_constat(ligne) if ligne else None

    async def lister_constats_en_attente(self, limite: int = 50) -> list[Constat]:
        """Liste les constats en attente de revue ANADER, les plus anciens d'abord."""
        if not self._pret:
            return []
        return await asyncio.to_thread(self._lire_en_attente, limite)

    def _lire_en_attente(self, limite: int) -> list[Constat]:
        """Lit la file de revue (appelé dans un thread)."""
        with closing(self._connexion()) as connexion:
            lignes = connexion.execute(
                "SELECT * FROM constats WHERE etat_revue = ? ORDER BY cree_le ASC LIMIT ?",
                (EtatRevue.EN_ATTENTE.value, limite),
            ).fetchall()
        return [self._ligne_en_constat(ligne) for ligne in lignes]

    async def reviser_constat(
        self, identifiant: str, etat: EtatRevue, revu_par: str, correction: str
    ) -> Constat | None:
        """Enregistre la décision d'un agent ANADER sur un constat."""
        if not self._pret:
            return None
        async with self._verrou:
            await asyncio.to_thread(self._ecrire_revue, identifiant, etat, revu_par, correction)
        return await asyncio.to_thread(self._lire_constat_sans_proprietaire, identifiant)

    def _ecrire_revue(
        self, identifiant: str, etat: EtatRevue, revu_par: str, correction: str
    ) -> None:
        """Écrit la décision de revue (appelé dans un thread)."""
        with closing(self._connexion()) as connexion:
            connexion.execute(
                "UPDATE constats SET etat_revue = ?, revu_par = ?, correction = ? WHERE id = ?",
                (etat.value, revu_par, correction, identifiant),
            )
            connexion.commit()

    def _lire_constat_sans_proprietaire(self, identifiant: str) -> Constat | None:
        """Lit un constat par son seul identifiant — réservé à la revue ANADER."""
        with closing(self._connexion()) as connexion:
            ligne = connexion.execute(
                "SELECT * FROM constats WHERE id = ?", (identifiant,)
            ).fetchone()
        return self._ligne_en_constat(ligne) if ligne else None
```

Ajouter les imports nécessaires en tête du module :

```python
from app.models.constat import Constat, EtatRevue, NiveauConfiance, Observation, Organe
```

> **Attention à la frontière d'autorisation.** `_lire_constat_sans_proprietaire` contourne délibérément le cloisonnement par appareil : la revue ANADER doit voir les constats de tous les producteurs. Cette méthode est **privée** et n'est appelée que depuis `reviser_constat`, elle-même réservée à la console de curation authentifiée (Tâche 6). Ne l'expose jamais sur une route publique.

- [ ] **Step 4: Étendre le port**

Dans `api/app/domain/ports.py`, ajouter les quatre méthodes à `ParcelleStorePort`, avec les mêmes signatures, et compléter le bloc `TYPE_CHECKING` : `from app.models.constat import Constat, EtatRevue`.

- [ ] **Step 5: Lancer les tests du dépôt**

Run: `cd api && python -m pytest tests/test_constats_store.py -v --no-cov`
Expected: PASS — 8 tests

- [ ] **Step 6: Écrire les tests de l'API**

Créer `api/tests/test_constats_api.py`. Réutiliser **exactement** la fixture `client` de `api/tests/test_parcelles_api.py` (mêmes variables d'environnement, `PREWARM_ENABLED=false`, `get_settings.cache_clear()`), en y ajoutant `VISION_ENABLED=true`, et surcharger la dépendance de vision par un faux port :

```python
"""Tests de l'endpoint de constat visuel."""

from __future__ import annotations

import base64
import struct

import pytest
from fastapi.testclient import TestClient

ENTETES = {"X-Device-Id": "appareil-a"}


def _jpeg(largeur: int = 1024, hauteur: int = 768) -> bytes:
    soi = b"\xff\xd8"
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
    sof0 = (
        b"\xff\xc0" + struct.pack(">H", 17) + b"\x08"
        + struct.pack(">HH", hauteur, largeur) + b"\x03" + b"\x00" * 9
    )
    return soi + app0 + sof0


def _image_payload() -> dict:
    return {
        "contenu_base64": base64.b64encode(_jpeg()).decode("ascii"),
        "largeur": 1024,
        "hauteur": 768,
        "score_nettete": 400.0,
        "luminance_moyenne": 128.0,
    }


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("PARCELLES_ENABLED", "true")
    monkeypatch.setenv("VISION_ENABLED", "true")
    monkeypatch.setenv("PARCELLES_DB_PATH", str(tmp_path / "parcelles.db"))
    monkeypatch.setenv("CAPTURES_DIR", str(tmp_path / "captures"))
    monkeypatch.setenv("SESSIONS_DB_PATH", str(tmp_path / "sessions.db"))
    monkeypatch.setenv("PREWARM_ENABLED", "false")
    from app.core.config import get_settings
    from app.main import create_app

    get_settings.cache_clear()
    with TestClient(create_app()) as client_test:
        yield client_test
    get_settings.cache_clear()


def _parcelle_avec_capture(client: TestClient) -> tuple[str, str]:
    """Crée une parcelle et y dépose une photo. Retourne (parcelle, capture)."""
    parcelle = client.post(
        "/v1/parcelles", json={"nom": "Bloc", "localite": "Daloa"}, headers=ENTETES
    ).json()["identifiant"]
    capture = client.post(
        f"/v1/parcelles/{parcelle}/captures",
        json={"modalite": "photos", "images": [_image_payload()]},
        headers=ENTETES,
    ).json()["identifiant"]
    return parcelle, capture


def test_constat_sur_capture_inconnue_renvoie_404(client: TestClient):
    parcelle, _ = _parcelle_avec_capture(client)
    reponse = client.post(
        f"/v1/parcelles/{parcelle}/captures/inexistante/constat", headers=ENTETES
    )
    assert reponse.status_code == 404


def test_constat_sans_entete_appareil_est_refuse(client: TestClient):
    parcelle, capture = _parcelle_avec_capture(client)
    reponse = client.post(f"/v1/parcelles/{parcelle}/captures/{capture}/constat")
    assert reponse.status_code == 400


def test_vision_indisponible_renvoie_503_avec_une_consigne_lisible(client: TestClient):
    """Profil CPU (defaut des tests) : le VLM est absent, on le DIT."""
    parcelle, capture = _parcelle_avec_capture(client)
    reponse = client.post(
        f"/v1/parcelles/{parcelle}/captures/{capture}/constat", headers=ENTETES
    )
    assert reponse.status_code == 503
    assert "ANADER" in reponse.json()["detail"]
```

- [ ] **Step 7: Écrire le service et la route**

Créer `api/app/services/constats.py` :

```python
"""Service métier du constat visuel : relit les images, contextualise, persiste.

Fait le pont entre la capture stockée par C1 et la cascade d'analyse de C2. Le
constat produit par ``ServiceConstatVisuel`` est **anonyme** (il ne connaît ni sa
capture ni sa parcelle) : c'est ici qu'il est rattaché, par ``dataclasses.replace``
puisque ``Constat`` est immuable.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from app.application.constat_visuel import ServiceConstatVisuel
from app.application.fusion_contextuelle import ContexteParcelle
from app.core.logging import get_logger
from app.domain.ports import ParcelleStorePort
from app.models.constat import Constat
from app.services.parcelles import ServiceParcelles
from app.services.vision.indisponible import CONSIGNE_INDISPONIBLE

logger = get_logger(__name__)


class CaptureIntrouvable(Exception):
    """La capture visée n'existe pas, ou n'appartient pas à cet appareil."""


class VisionIndisponibleErreur(Exception):
    """L'analyse visuelle n'est pas disponible (profil CPU, ou VLM injoignable)."""


class ServiceConstats:
    """Produit et persiste le constat visuel d'une capture."""

    def __init__(
        self,
        store: ParcelleStorePort,
        parcelles: ServiceParcelles,
        constat_visuel: ServiceConstatVisuel,
        dossier_captures: Path,
    ) -> None:
        """Initialise le service.

        Args:
            store: Dépôt de persistance des parcelles et constats.
            parcelles: Service métier des parcelles (lecture de la capture).
            constat_visuel: Cascade d'analyse (étages 1, 4 et 5).
            dossier_captures: Dossier où C1 a écrit les images.
        """
        self._store = store
        self._parcelles = parcelles
        self._analyse = constat_visuel
        self._dossier = dossier_captures

    def _lire_images(self, capture) -> tuple[tuple[bytes, str], ...]:
        """Relit sur disque les octets des images RECEVABLES d'une capture.

        Les images refusées à l'étage 0 sont écartées : les analyser reviendrait à
        interpréter du bruit, ce que la cascade est faite pour éviter.
        """
        lues: list[tuple[bytes, str]] = []
        for image in capture.images:
            if not image.recevabilite.recevable or not image.empreinte_sha256:
                continue
            chemin = self._dossier / f"{image.empreinte_sha256}.bin"
            if chemin.exists():
                lues.append((chemin.read_bytes(), image.empreinte_sha256))
        return tuple(lues)

    async def _contexte(self, parcelle_id: str, proprietaire: str) -> ContexteParcelle:
        """Rassemble ce que la plateforme sait déjà de la parcelle.

        La météo n'est pas branchée dans ce chantier : ``pluie_mm_14j`` vaut ``None``,
        ce que l'étage 4 traduit par une dégradation de confiance et un facteur
        explicite (« relevé de pluie indisponible »). **On ne conforte rien sans
        donnée** — brancher l'outil Open-Meteo existant est une évolution ultérieure,
        et elle ne changera pas ce contrat.
        """
        parcelle = await self._parcelles.obtenir(parcelle_id, proprietaire)
        return ContexteParcelle(
            pluie_mm_14j=None,
            saison="",
            localite=parcelle.localite if parcelle else "",
            alertes_deforestation=None,
        )

    async def produire(
        self, parcelle_id: str, capture_id: str, proprietaire: str
    ) -> Constat:
        """Produit et persiste le constat visuel d'une capture.

        Args:
            parcelle_id: Identifiant de la parcelle.
            capture_id: Identifiant de la capture à analyser.
            proprietaire: Identifiant anonyme de l'appareil.

        Returns:
            Le constat persisté.

        Raises:
            CaptureIntrouvable: Capture inconnue, d'un autre appareil, d'une autre
                parcelle, ou dépourvue d'image recevable.
            VisionIndisponibleErreur: Vision absente, ou sortie compromise.
        """
        capture = await self._parcelles.obtenir_capture(capture_id, proprietaire)
        if capture is None or capture.parcelle != parcelle_id:
            raise CaptureIntrouvable(capture_id)
        images = self._lire_images(capture)
        if not images:
            raise CaptureIntrouvable(capture_id)

        contexte = await self._contexte(parcelle_id, proprietaire)
        constat = await self._analyse.analyser(images, contexte)
        if constat is None:
            raise VisionIndisponibleErreur(CONSIGNE_INDISPONIBLE)

        # Constat immuable : on le rattache par recopie, jamais par mutation.
        rattache = dataclasses.replace(
            constat, capture=capture_id, parcelle=parcelle_id, proprietaire=proprietaire
        )
        await self._store.enregistrer_constat(rattache)
        logger.info(
            "constat_produit",
            parcelle=parcelle_id,
            capture=capture_id,
            images=len(images),
            confiance=rattache.confiance.value,
        )
        return rattache
```

> **Note sur `VisionIndisponibleErreur`.** Elle couvre deux cas que l'appelant ne peut pas distinguer : vision absente, et sortie rejetée par le garde-fou anti-diagnostic. C'est **voulu** — dans les deux cas le producteur n'obtient pas de constat, et lui expliquer que « le modèle a nommé une maladie interdite » n'aurait aucun sens. La distinction reste dans les journaux, où elle sert au diagnostic technique.

Dans `api/app/routers/parcelles.py`, ajouter :

```python
@router.post(
    "/parcelles/{identifiant}/captures/{capture_id}/constat",
    response_model=ConstatReponse,
    status_code=status.HTTP_201_CREATED,
)
async def produire_constat(
    identifiant: str,
    capture_id: str,
    client_ip: str = Depends(get_client_ip),
    device_id: str = Depends(get_device_id_obligatoire),
    cache: CachePort = Depends(get_cache_client),
    service: ServiceConstats = Depends(get_service_constats),
) -> ConstatReponse:
    """Produit le constat visuel d'une capture.

    Raises:
        HTTPException: 404 si la capture est inconnue, 503 si la vision est
            indisponible (profil CPU ou VLM absent), 429 si le débit est dépassé.
    """
    await _garde_debit(cache, client_ip)
    try:
        constat = await service.produire(identifiant, capture_id, device_id)
    except CaptureIntrouvable as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Capture inconnue."
        ) from exc
    except VisionIndisponibleErreur as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return ConstatReponse.model_validate(constat, from_attributes=True)
```

- [ ] **Step 8: Lancer les tests de l'API**

Run: `cd api && python -m pytest tests/test_constats_api.py -v --no-cov`
Expected: PASS

- [ ] **Step 9: Lint et commit**

```bash
cd api && python -m ruff format app/ tests/ && python -m ruff check app/ tests/
cd .. && git add api/app/core/parcelles_store.py api/app/domain/ports.py api/app/services/constats.py api/app/routers/parcelles.py api/tests/test_constats_store.py api/tests/test_constats_api.py
git commit -m "feat(vision): persistance et endpoint du constat visuel

Migration 2 : table constats et son cycle de revue. Vision indisponible -> 503 avec
une consigne lisible qui oriente vers l ANADER, jamais une erreur brute."
```

---

## Task 6: Étage 6 — la file de revue ANADER

**Le vrai différenciateur du chantier.** Chaque constat part en revue ; l'agent confirme ou corrige ; l'étiquette corrigée alimente le jeu d'entraînement. Le système s'améliore **parce qu'il est utilisé**, et la précision annoncée devient mesurable puis publiable. La faiblesse — un modèle qui peut se tromper — devient le moteur du produit.

**Files:**
- Create: `api/app/curation/revue_constats.py`
- Modify: `api/app/curation/main.py` (montage des routes, authentification existante)
- Test: `api/tests/test_revue_constats.py`

**Interfaces:**
- Consomme : `ParcelleStore.lister_constats_en_attente`, `reviser_constat` (T5).
- Produit :
  - `GET /curation/constats` — la file, la plus ancienne d'abord
  - `POST /curation/constats/{identifiant}/revue` — corps `{etat, revu_par, correction}`
  - `GET /curation/constats/export` — JSONL des constats revus, jeu d'entraînement

- [ ] **Step 1: Lire la console existante**

Run: `cd api && grep -n "router\|Depends\|login\|auth" app/curation/main.py | head -30`

**Reprendre exactement** son mécanisme d'authentification. La revue expose les constats de **tous** les producteurs : une route non authentifiée ici serait une fuite de données de parcelle bien plus grave que sur l'API publique.

- [ ] **Step 2: Écrire les tests**

Créer `api/tests/test_revue_constats.py`. Le montage du client et l'authentification se calquent sur `api/tests/test_curation_api.py` — **lis-le d'abord** et reprends sa fixture, en y ajoutant `PARCELLES_ENABLED=true` et un `PARCELLES_DB_PATH` temporaire.

```python
"""Tests de la file de revue ANADER des constats visuels (étage 6)."""

from __future__ import annotations

import json


def _deposer_constat(client, entetes_auth, identifiant="c1") -> str:
    """Insère un constat en attente via le dépôt de l'application."""
    from datetime import UTC, datetime

    from app.models.constat import Constat, NiveauConfiance, Observation, Organe

    constat = Constat(
        identifiant=identifiant,
        capture="cap1",
        parcelle="p1",
        proprietaire="appareil-a",
        observations=(
            Observation(
                organe=Organe.CABOSSE,
                description="Taches brunes sur un tiers de la cabosse.",
                confiance=NiveauConfiance.MOYENNE,
                empreinte_image="a" * 64,
            ),
        ),
        texte="Vos cabosses présentent des taches. Montrez-les à votre agent ANADER.",
        confiance=NiveauConfiance.MOYENNE,
        cree_le=datetime.now(UTC),
    )
    client.app.state.parcelles._pret = True
    import asyncio

    asyncio.get_event_loop().run_until_complete(
        client.app.state.parcelles.enregistrer_constat(constat)
    )
    return identifiant


def test_la_file_est_refusee_sans_authentification(client):
    """La revue voit les constats de TOUS les producteurs : jamais en acces libre."""
    assert client.get("/curation/constats").status_code in (401, 403)


def test_la_revue_est_refusee_sans_authentification(client):
    reponse = client.post(
        "/curation/constats/c1/revue",
        json={"etat": "confirme", "revu_par": "x", "correction": ""},
    )
    assert reponse.status_code in (401, 403)


def test_l_export_est_refuse_sans_authentification(client):
    assert client.get("/curation/constats/export").status_code in (401, 403)


def test_la_file_rend_les_constats_en_attente(client, entetes_auth):
    _deposer_constat(client, entetes_auth)
    reponse = client.get("/curation/constats", headers=entetes_auth)
    assert reponse.status_code == 200
    assert [c["identifiant"] for c in reponse.json()] == ["c1"]


def test_confirmer_sort_le_constat_de_la_file(client, entetes_auth):
    _deposer_constat(client, entetes_auth)
    client.post(
        "/curation/constats/c1/revue",
        json={"etat": "confirme", "revu_par": "agent-anader-7", "correction": ""},
        headers=entetes_auth,
    )
    assert client.get("/curation/constats", headers=entetes_auth).json() == []


def test_corriger_conserve_l_etiquette_humaine(client, entetes_auth):
    _deposer_constat(client, entetes_auth)
    reponse = client.post(
        "/curation/constats/c1/revue",
        json={
            "etat": "corrige",
            "revu_par": "agent-anader-7",
            "correction": "Ombrage insuffisant, pas une atteinte.",
        },
        headers=entetes_auth,
    )
    assert reponse.status_code == 200
    assert reponse.json()["correction"].startswith("Ombrage")


def test_un_etat_de_revue_inconnu_est_refuse(client, entetes_auth):
    _deposer_constat(client, entetes_auth)
    reponse = client.post(
        "/curation/constats/c1/revue",
        json={"etat": "peut-etre", "revu_par": "x", "correction": ""},
        headers=entetes_auth,
    )
    assert reponse.status_code == 422


def test_l_export_produit_une_ligne_par_constat_revu(client, entetes_auth):
    """C'est CE fichier qui devient le jeu de donnees ivoirien (spec §7.6)."""
    _deposer_constat(client, entetes_auth)
    client.post(
        "/curation/constats/c1/revue",
        json={"etat": "corrige", "revu_par": "agent-7", "correction": "Ombrage."},
        headers=entetes_auth,
    )
    corps = client.get("/curation/constats/export", headers=entetes_auth).text
    lignes = [json.loads(l) for l in corps.strip().splitlines()]
    assert len(lignes) == 1
    assert lignes[0]["empreinte"] == "a" * 64
    assert lignes[0]["organe"] == "cabosse"
    assert lignes[0]["etat"] == "corrige"
    assert lignes[0]["correction"] == "Ombrage."


def test_l_export_ignore_les_constats_non_revus(client, entetes_auth):
    """Un constat en attente n a pas d etiquette humaine : il ne sert pas d exemple."""
    _deposer_constat(client, entetes_auth)
    assert client.get("/curation/constats/export", headers=entetes_auth).text.strip() == ""
```

- [ ] **Step 3: Implémenter**

Créer `api/app/curation/revue_constats.py` sur le moule des autres modules de la console (même `APIRouter`, même dépendance d'authentification) :

```python
"""File de revue ANADER des constats visuels — étage 6 de la cascade.

**Le vrai différenciateur du chantier.** Chaque constat part en revue ; l'agent
confirme ou corrige ; l'étiquette corrigée alimente l'export qui deviendra le jeu de
données ivoirien. Le système s'améliore *parce qu'il est utilisé*, et la précision
annoncée devient mesurable puis publiable.

**Ces routes voient les constats de TOUS les producteurs** — elles sont donc derrière
l'authentification de la console, jamais sur l'API publique.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.models.constat import Constat, ConstatReponse, EtatRevue

logger = get_logger(__name__)

router = APIRouter(prefix="/curation", tags=["revue"])

LIMITE_FILE = 50


class RevueRequest(BaseModel):
    """Décision d'un agent ANADER sur un constat."""

    etat: EtatRevue
    revu_par: str = Field(min_length=1, max_length=120)
    correction: str = Field(default="", max_length=2000)


def _depot(request: Request):
    """Retourne le dépôt de parcelles porté par l'application."""
    return request.app.state.parcelles


@router.get("/constats", response_model=list[ConstatReponse])
async def lister_file(request: Request) -> list[ConstatReponse]:
    """Liste les constats en attente de revue, les plus anciens d'abord."""
    constats = await _depot(request).lister_constats_en_attente(limite=LIMITE_FILE)
    return [ConstatReponse.model_validate(c, from_attributes=True) for c in constats]


@router.post("/constats/{identifiant}/revue", response_model=ConstatReponse)
async def reviser(identifiant: str, payload: RevueRequest, request: Request) -> ConstatReponse:
    """Enregistre la décision d'un agent ANADER.

    Raises:
        HTTPException: 404 si le constat est inconnu.
    """
    revu = await _depot(request).reviser_constat(
        identifiant, payload.etat, payload.revu_par, payload.correction
    )
    if revu is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Constat inconnu.")
    logger.info("constat_revu", constat=identifiant, etat=payload.etat.value)
    return ConstatReponse.model_validate(revu, from_attributes=True)


def _lignes_export(constats: list[Constat]) -> str:
    """Sérialise les constats revus en JSONL d'entraînement (une ligne par image)."""
    lignes: list[str] = []
    for constat in constats:
        for observation in constat.observations:
            lignes.append(
                json.dumps(
                    {
                        "empreinte": observation.empreinte_image,
                        "organe": observation.organe.value,
                        "description": observation.description,
                        "etat": constat.etat_revue.value,
                        "correction": constat.correction,
                        "cree_le": constat.cree_le.isoformat(),
                    },
                    ensure_ascii=False,
                )
            )
    return "\n".join(lignes)


@router.get("/constats/export", response_class=PlainTextResponse)
async def exporter(request: Request) -> str:
    """Exporte les constats **revus** en JSONL — le jeu de données ivoirien.

    Un constat en attente n'a pas d'étiquette humaine : il ne sert pas d'exemple et
    n'est donc jamais exporté.
    """
    revus = await _depot(request).lister_constats_revus(limite=5000)
    return _lignes_export(revus)
```

> `lister_constats_revus(limite)` n'existe pas encore : **ajoute-la** à `ParcelleStore` et à `ParcelleStorePort`, sur le moule exact de `lister_constats_en_attente`, avec la clause `WHERE etat_revue != 'en_attente' ORDER BY cree_le ASC LIMIT ?`. Écris son test dans `api/tests/test_constats_store.py` avant l'implémentation.

Monter le routeur dans `api/app/curation/main.py` à côté des autres, **avec la même dépendance d'authentification** — vérifie comment les routeurs existants y sont protégés et reproduis-le à l'identique.

- [ ] **Step 4: Lancer les tests, lint, commit**

Run: `cd api && python -m pytest tests/test_revue_constats.py -v --no-cov` puis `python -m pytest -q`

```bash
git commit -m "feat(vision): etage 6 - file de revue ANADER et export du jeu de donnees

Chaque constat part en revue ; l agent confirme ou corrige ; l etiquette corrigee
alimente l export JSONL qui deviendra le jeu de donnees ivoirien. Le systeme
s ameliore parce qu il est utilise.

Routes derriere l authentification de la console : la revue voit les constats de
TOUS les producteurs."
```

---

## Task 7: Réglages, câblage, déploiement

**Files:**
- Modify: `api/app/core/config.py`, `api/app/api_deps.py`, `api/app/main.py`
- Create: `deploy/k8s/vision.yaml`
- Modify: `deploy/k8s/api.yaml`
- Test: `api/tests/test_config.py` (extension)

**Interfaces:**
- Produit : `Settings.vision_enabled: bool = False`, `vision_url: str = "http://vision:8000"`, `vision_modele: str = "qwen3-vl"`, `vision_timeout_s: float = 60.0` ; `get_service_constats(request) -> ServiceConstats`.

- [ ] **Step 1: Ajouter les réglages**

Dans `api/app/core/config.py`, après les réglages des parcelles, en documentant chaque champ dans la docstring `Attributes` :

```python
    # --- Analyse visuelle (V3, chantier C2) ---
    # OFF par défaut : sans VLM joignable, l'API le dit (503 + consigne ANADER),
    # elle n'invente aucune description.
    vision_enabled: bool = False
    vision_url: str = "http://vision:8000"
    vision_modele: str = "qwen3-vl"
    vision_timeout_s: float = 60.0
```

- [ ] **Step 2: Composer selon le profil matériel**

Dans `api/app/main.py`, à la suite du câblage des parcelles :

```python
    # Le VLM ne tient que sur GPU (spec §3) : en profil CPU on branche la source
    # neutre, qui se déclare indisponible. Aucune description n'est jamais inventée.
    if settings.vision_enabled and settings.profil_materiel == "gpu":
        app.state.vision = ClientVLM.from_settings(settings)
    else:
        app.state.vision = VisionIndisponible()
    app.state.service_constats = ServiceConstats(
        app.state.parcelles,
        app.state.service_parcelles,
        ServiceConstatVisuel(app.state.vision, app.state.inference),
        dossier_captures=Path(settings.captures_dir),
    )
```

Fermer le client de vision dans le bloc de libération, s'il expose `close`.

- [ ] **Step 3: Écrire le manifeste du service de vision**

Créer `deploy/k8s/vision.yaml` : un `Deployment` et un `Service` de type `ClusterIP`, **sans Ingress** — le service n'est jamais exposé publiquement, l'API le consomme en interne, exactement comme `inference`. Reprendre la structure de `deploy/k8s/inference.yaml` : `replicas: 1`, montage du volume `modeles` en lecture seule, `resources.limits` adaptées au GPU, sonde de disponibilité sur `/v1/models`.

Dans `deploy/k8s/api.yaml`, ajouter à la ConfigMap :

```yaml
  # Analyse visuelle (V3, chantier C2). OFF tant que le service de vision n'est pas
  # déployé : l'API répond alors 503 avec une consigne qui oriente vers l'ANADER.
  VISION_ENABLED: "false"
  VISION_URL: "http://vision:8000"
  VISION_MODELE: "qwen3-vl"
  VISION_TIMEOUT_S: "60"
```

- [ ] **Step 4: Vérifier**

Run: `python -c "import yaml; list(yaml.safe_load_all(open('deploy/k8s/vision.yaml', encoding='utf-8'))); list(yaml.safe_load_all(open('deploy/k8s/api.yaml', encoding='utf-8'))); print('YAML valide')"`

Run: `cd api && python -m pytest -q` — couverture ≥ 97 %.

- [ ] **Step 5: Commit**

```bash
git commit -m "chore(vision): reglages, composition selon le profil materiel, manifeste

Le VLM ne tient que sur GPU : en profil CPU la source neutre est branchee et l API
declare la vision indisponible. Service de vision en ClusterIP sans Ingress —
jamais expose publiquement, comme inference."
```

---

## Recette de fin de chantier

Chaque ligne correspond à un critère d'acceptation de la spec §7.8.

- [ ] `cd api && python -m pytest -q` — vert, couverture ≥ 97 %
- [ ] `cd api && python -m ruff check app/ tests/` — aucune erreur
- [ ] Une photo de cabosse produit un constat descriptif **sans nom de maladie, sans produit, sans dosage** — vérifié par test sur les termes interdits
- [ ] En profil CPU, une demande de constat renvoie **503 avec une consigne lisible**, jamais une description inventée
- [ ] Une contradiction météo **dégrade la confiance** du constat (test de l'étage 4)
- [ ] Chaque constat apparaît dans la file de revue, et une correction est persistée
- [ ] Les routes de revue sont **authentifiées** — une requête anonyme est refusée
- [ ] Aucun test n'appelle le réseau ; aucun dosage phytosanitaire nulle part
- [ ] `security-review` lancé sur la branche, retours traités

Puis mettre à jour `docs/agents_v3.md` avec une section « La cascade de vision », dans le style pédagogique du document (*le concept*, *les décisions*, *le modèle mental*).

## Ce que ce chantier ne livre pas, délibérément

**Étage 2 (localisation des lésions)** et **étage 3 (étiologie)** : ils exigent un jeu de données ivoirien qui n'existe pas encore — c'est précisément l'étage 6 qui va le construire. Le pré-diagnostic s'ouvrira au franchissement du seuil de rappel par classe (0,90 proposé sur pourriture brune et swollen shoot), **jamais à une date**.

**La porte de sortie reste ouverte.** Si le VLM se révèle inutilisable sur des photos ivoiriennes, C1, C3 et C4 se présentent sans lui et la démonstration tient debout. Cette décision se prend **une semaine avant l'événement**, sur essai réel — pas la veille, et pas sans avoir essayé.
