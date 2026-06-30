# Détection de localité (agent Météo) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rendre la détection de localité de l'agent Météo robuste (liste officielle des zones, insensible casse/accents, sur tout le fil) et distinguer trois cas : localité cacaoyère → prévision ; localité non cacaoyère du Nord → redirection ; aucune localité → demande de commune.

**Architecture :** Un module partagé `app/services/localites.py` devient la source unique de la connaissance des localités ivoiriennes (détection cacaoyère, détection Nord non cacaoyère, lookup DR). `contacts.py` et `guardrails.py` le consomment (refactor sans changement de comportement, garanti par leurs tests existants). `agent_meteo.py` l'utilise pour ses trois cas.

**Tech Stack :** Python 3.11+, `pyyaml`, `re`, `unicodedata`, `pytest` + `pytest-asyncio`. Aucune nouvelle dépendance.

## Global Constraints

- Python 3.11+, `from __future__ import annotations` en tête de chaque module.
- Typage systématique ; docstrings format Google ; pas de `print()` (logging `structlog` via `app.core.logging.get_logger`).
- `ruff format` + `ruff check` doivent passer ; imports triés par ruff.
- Couverture min. 80 % sur `api/app/` ; inférence et réseau mockés (aucun appel réel en CI).
- Garde-fou métier : la deny-list `LOCALITES_NORD` (décision Waopron) ne doit PAS être élargie. Ne jamais générer de dosage phytosanitaire, même en test.
- Commits sans signature ni mention d'outil IA (`Co-Authored-By` interdit).
- Source de vérité des zones : `api/app/data/contacts_zones.yaml` (10 DR / 60 zones).
- Toutes les commandes se lancent depuis `api/` (où vit `pytest`/`pyproject.toml`).

---

### Task 1: Module `localites.py` — détection des localités

**Files:**
- Create: `api/app/services/localites.py`
- Test: `api/tests/test_localites.py`

**Interfaces:**
- Consumes: `app/data/contacts_zones.yaml` (clés `directions_regionales[].siege`, `[].zones`, `[].nom`, `[].tel`, `[].email`, `[].verifie`).
- Produces (utilisés par Tasks 2, 3, 4) :
  - `LOCALITES_NORD: dict[str, str]` — deny-list (clé normalisée → nom d'affichage).
  - `detecter(texte: str) -> str | None` — nom canonique de la première localité **cacaoyère** (hors deny-list Nord), ou `None`.
  - `detecter_nord(texte: str) -> str | None` — nom d'affichage de la première ville **Nord non cacaoyère**, ou `None`.
  - `chercher_zone(texte: str) -> tuple[dict, str] | None` — `(dict_DR, libellé_normalisé_matché)` de la première zone citée (**toutes zones**), ou `None`.

- [ ] **Step 1 : Écrire les tests qui échouent**

Create `api/tests/test_localites.py` :

```python
"""Tests du module de détection de localités ivoiriennes."""

from __future__ import annotations

from pathlib import Path

from app.services import localites


def test_detecter_localite_cacaoyere_insensible_casse_accents() -> None:
    assert localites.detecter("Quel temps à daloa ?") == "Daloa"


def test_detecter_libelle_compose() -> None:
    # Libellé en deux mots reconnu (le plus long prime).
    assert localites.detecter("prévisions sur san pedro") == "San Pedro"


def test_detecter_mot_frontiere_pas_de_match_partiel() -> None:
    # « Manioc » ne doit pas matcher la zone « Man ».
    assert localites.detecter("je cultive le manioc") is None


def test_detecter_aucune_localite() -> None:
    assert localites.detecter("bonjour, comment ça va ?") is None


def test_detecter_exclut_ville_nord() -> None:
    # Korhogo est dans le YAML mais hors filière cacao : exclu du détecteur cacaoyer.
    assert localites.detecter("quel temps à Korhogo ?") is None


def test_detecter_nord_reconnait_ville_nord() -> None:
    assert localites.detecter_nord("quel temps à Korhogo ?") == "Korhogo"


def test_detecter_nord_none_sur_ville_cacaoyere() -> None:
    assert localites.detecter_nord("quel temps à Daloa ?") is None


def test_chercher_zone_renvoie_dr_et_libelle() -> None:
    trouve = localites.chercher_zone("je suis planteur à Daloa")
    assert trouve is not None
    dr, zone = trouve
    assert dr["nom"] == "Direction Régionale Centre-Ouest"
    assert zone == "daloa"


def test_chercher_zone_inclut_le_nord() -> None:
    # Un producteur du Nord garde droit au contact ANADER.
    trouve = localites.chercher_zone("contact à Korhogo")
    assert trouve is not None
    dr, _zone = trouve
    assert dr["nom"] == "Direction Régionale Nord"


def test_yaml_illisible_degrade_proprement(monkeypatch) -> None:
    monkeypatch.setattr(localites, "_CHEMIN", Path("/inexistant/contacts.yaml"))
    localites._annuaire.cache_clear()
    localites._index.cache_clear()
    try:
        assert localites.detecter("quel temps à Daloa ?") is None
        assert localites.chercher_zone("Daloa") is None
    finally:
        localites._annuaire.cache_clear()
        localites._index.cache_clear()
```

- [ ] **Step 2 : Lancer les tests pour vérifier l'échec**

Run: `cd api && pytest tests/test_localites.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.localites'`

- [ ] **Step 3 : Écrire l'implémentation minimale**

Create `api/app/services/localites.py` :

```python
"""Détection de localités ivoiriennes dans un texte libre.

Brique à responsabilité unique, partagée par plusieurs services :

- l'agent Météo géocode la localité CACAOYÈRE détectée (``detecter``) ;
- l'agent Météo signale une localité NON cacaoyère du Nord (``detecter_nord``) ;
- ``contacts.py`` retrouve la Direction Régionale d'une zone (``chercher_zone``,
  TOUTES zones — un producteur du Nord garde droit au contact ANADER) ;
- ``guardrails.py`` importe la deny-list ``LOCALITES_NORD``.

Source de vérité des zones : ``app/data/contacts_zones.yaml`` (10 DR / 60 zones). La
connaissance « cacaoyère ou non » repose sur la deny-list curée ``LOCALITES_NORD``
(décision métier Waopron) — volontairement NON élargie.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path

import yaml

from app.core.logging import get_logger

logger = get_logger(__name__)

_CHEMIN = Path(__file__).resolve().parent.parent / "data" / "contacts_zones.yaml"

# Villes de savane du Nord, non cacaoyères (climat trop sec / saison des pluies trop
# courte). Deny-list curée — décision métier Waopron, NON élargie. Clé normalisée
# (minuscule sans accent) -> nom d'affichage.
LOCALITES_NORD: dict[str, str] = {
    "korhogo": "Korhogo",
    "katiola": "Katiola",
    "ferkessedougou": "Ferkessédougou",
    "ferke": "Ferké",
    "boundiali": "Boundiali",
    "odienne": "Odienné",
    "tengrela": "Tengréla",
    "bouna": "Bouna",
    "dabakala": "Dabakala",
    "niakaramandougou": "Niakaramandougou",
    "kong": "Kong",
    "minignan": "Minignan",
    "ouangolodougou": "Ouangolodougou",
    "sinematiali": "Sinématiali",
    "kouto": "Kouto",
}


def _normaliser(texte: str) -> str:
    """Minuscule + suppression des accents, pour une comparaison robuste."""
    sans_accent = "".join(
        c for c in unicodedata.normalize("NFD", texte) if unicodedata.category(c) != "Mn"
    )
    return sans_accent.lower()


@lru_cache(maxsize=1)
def _annuaire() -> dict:
    """Charge l'annuaire YAML (mémoïsé). Renvoie {} si absent/illisible."""
    try:
        return yaml.safe_load(_CHEMIN.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("annuaire_localites_illisible", error=str(exc))
        return {}


@lru_cache(maxsize=1)
def _index() -> list[tuple[re.Pattern, str, dict]]:
    """Index ``(regex sur libellé normalisé, nom canonique, DR)``, du plus long au plus court.

    Trié par longueur de libellé décroissante pour qu'un libellé long (« san pedro »)
    prime sur un court. Le mot-frontière évite les correspondances partielles.
    """
    paires: list[tuple[str, str, dict]] = []
    for dr in _annuaire().get("directions_regionales", []):
        libelles = {dr.get("siege", ""), *dr.get("zones", [])}
        for libelle in libelles:
            if libelle:
                paires.append((_normaliser(libelle), libelle, dr))
    paires.sort(key=lambda p: len(p[0]), reverse=True)
    return [(re.compile(rf"\b{re.escape(n)}\b"), canon, dr) for n, canon, dr in paires]


def detecter(texte: str) -> str | None:
    """Nom canonique de la première localité CACAOYÈRE citée, ou ``None``.

    Exclut les villes de ``LOCALITES_NORD`` (non cacaoyères) : leur prévision n'a pas
    de sens agronomique pour le cacao.

    Args:
        texte: Texte libre (idéalement tout le fil de conversation).

    Returns:
        Le nom canonique (casse d'origine du YAML), ou ``None``.
    """
    norm = _normaliser(texte)
    for motif, canon, _dr in _index():
        if motif.search(norm) and _normaliser(canon) not in LOCALITES_NORD:
            return canon
    return None


def detecter_nord(texte: str) -> str | None:
    """Nom d'affichage de la première ville NON cacaoyère du Nord citée, ou ``None``."""
    norm = _normaliser(texte)
    for cle, nom in LOCALITES_NORD.items():
        if re.search(rf"\b{re.escape(cle)}\b", norm):
            return nom
    return None


def chercher_zone(texte: str) -> tuple[dict, str] | None:
    """``(dict_DR, libellé normalisé matché)`` de la première zone citée, ou ``None``.

    Inclut TOUTES les zones (Nord compris) : un producteur du Nord garde droit au
    contact ANADER.
    """
    norm = _normaliser(texte)
    for motif, _canon, dr in _index():
        m = motif.search(norm)
        if m:
            return dr, m.group(0)
    return None
```

- [ ] **Step 4 : Lancer les tests pour vérifier le succès**

Run: `cd api && pytest tests/test_localites.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5 : Lint**

Run: `cd api && ruff check app/services/localites.py tests/test_localites.py && ruff format --check app/services/localites.py tests/test_localites.py`
Expected: no errors

- [ ] **Step 6 : Commit**

```bash
git add api/app/services/localites.py api/tests/test_localites.py
git commit -m "feat(localites): module de détection des localités (cacaoyère/Nord/DR)"
```

---

### Task 2: Refactor `contacts.py` pour consommer `localites`

**Files:**
- Modify: `api/app/services/contacts.py`
- Test (existant, garde-fou de non-régression) : `api/tests/test_contacts.py`

**Interfaces:**
- Consumes: `localites.chercher_zone(texte) -> tuple[dict, str] | None` (Task 1).
- Produces: `contacts.chercher(texte) -> ContactDR | None` (signature publique inchangée).

- [ ] **Step 1 : Vérifier l'état vert AVANT refactor (filet de sécurité)**

Run: `cd api && pytest tests/test_contacts.py -v`
Expected: PASS (les tests existants décrivent le comportement à préserver)

- [ ] **Step 2 : Remplacer le scan local par la délégation à `localites`**

Dans `api/app/services/contacts.py` :

a) Remplacer l'import `unicodedata` (devenu inutile) et ajouter l'import `localites`. La section d'imports devient :

```python
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from app.core.logging import get_logger
from app.services import localites
```

b) Supprimer la fonction `_normaliser` et la fonction `_index_zones` (tout le bloc, du `def _normaliser` au `return [...]` de `_index_zones`).

c) Remplacer le corps de `chercher` par une délégation :

```python
def chercher(texte: str) -> ContactDR | None:
    """Retrouve la Direction Régionale compétente pour la localité citée, ou None.

    Args:
        texte: Texte libre (idéalement toute la conversation) où chercher une ville.

    Returns:
        Le contact de la DR correspondante, ou None si aucune localité connue n'apparaît.
    """
    trouve = localites.chercher_zone(texte)
    if trouve is None:
        return None
    dr, zone = trouve
    return ContactDR(
        nom=dr.get("nom", "ANADER"),
        siege=dr.get("siege", ""),
        tel=dr.get("tel", ""),
        email=dr.get("email", ""),
        verifie=bool(dr.get("verifie", False)),
        zone=zone,
    )
```

> Note : `_annuaire`, `_CHEMIN`, `intention_contact`, `siege`, `formater` et `_INTENT_CONTACT` restent inchangés (`siege()` continue d'utiliser `_annuaire()`).

- [ ] **Step 3 : Lancer les tests de non-régression**

Run: `cd api && pytest tests/test_contacts.py -v`
Expected: PASS (8 tests, comportement identique)

- [ ] **Step 4 : Lint**

Run: `cd api && ruff check app/services/contacts.py && ruff format --check app/services/contacts.py`
Expected: no errors (notamment : `unicodedata` n'est plus importé inutilement)

- [ ] **Step 5 : Commit**

```bash
git add api/app/services/contacts.py
git commit -m "refactor(contacts): déléguer la détection de zone à localites"
```

---

### Task 3: Refactor `guardrails.py` pour importer `LOCALITES_NORD`

**Files:**
- Modify: `api/app/services/guardrails.py`
- Test (existant, garde-fou de non-régression) : `api/tests/test_guardrails.py`

**Interfaces:**
- Consumes: `localites.LOCALITES_NORD` (Task 1).
- Produces: `guardrails.evaluer(question) -> Refus | None` (comportement inchangé).

- [ ] **Step 1 : Vérifier l'état vert AVANT refactor**

Run: `cd api && pytest tests/test_guardrails.py -v`
Expected: PASS

- [ ] **Step 2 : Importer la deny-list au lieu de la redéfinir**

Dans `api/app/services/guardrails.py` :

a) Ajouter l'import (à placer avec les autres imports `from app...`) :

```python
from app.services.localites import LOCALITES_NORD
```

b) Supprimer le bloc littéral `_LOCALITES_NORD = { ... }` (les 15 entrées) — il vit désormais dans `localites.py`.

c) Là où `_RE_LOCALITES_NORD` est construit, remplacer la référence `_LOCALITES_NORD` par `LOCALITES_NORD` :

```python
_RE_LOCALITES_NORD = tuple(
    (re.compile(rf"\b{re.escape(cle)}\b"), nom) for cle, nom in LOCALITES_NORD.items()
)
```

> Note : `_normaliser`, `_localite_nord_detectee`, `_message_zone`, `REFUS_ZONE_NON_CACAO`, `_TERMES_ZONE_DECLENCHEUR` et `evaluer` restent inchangés.

- [ ] **Step 3 : Lancer les tests de non-régression**

Run: `cd api && pytest tests/test_guardrails.py -v`
Expected: PASS (dont `test_zone_nord_non_cacaoyere_corrigee`, `test_zone_nord_nomme_la_localite`, `test_zone_nord_pas_de_faux_positif`)

- [ ] **Step 4 : Lint**

Run: `cd api && ruff check app/services/guardrails.py && ruff format --check app/services/guardrails.py`
Expected: no errors

- [ ] **Step 5 : Commit**

```bash
git add api/app/services/guardrails.py
git commit -m "refactor(guardrails): importer LOCALITES_NORD depuis localites"
```

---

### Task 4: Agent Météo — trois cas + détection sur tout le fil

**Files:**
- Modify: `api/app/services/agents/agent_meteo.py`
- Test: `api/tests/agents/test_agent_meteo.py`

**Interfaces:**
- Consumes: `localites.detecter`, `localites.detecter_nord` (Task 1) ; `OutilMeteo.invoquer(localite=...)` (existant).
- Produces: `AgentMeteo(inference, outil)` (constructeur **sans** `geo_defaut`) ; `_contexte` renvoie soit des prévisions, soit une consigne « zone non cacaoyère », soit une consigne « demande la commune ».

- [ ] **Step 1 : Écrire les nouveaux tests qui échouent**

Ajouter dans `api/tests/agents/test_agent_meteo.py`. D'abord, étendre le double `_MeteoFactice` pour tracer les invocations, puis ajouter trois tests. Remplacer la classe `_MeteoFactice` existante par :

```python
class _MeteoFactice:
    def __init__(self) -> None:
        self.appelee_avec: str | None = None

    async def previsions(self, localite: str) -> dict:
        self.appelee_avec = localite
        return {"localite": localite, "pluie_mm_24h": 12, "resume": "pluie demain"}
```

Puis ajouter ces tests à la fin du fichier :

```python
@pytest.mark.asyncio
async def test_localite_dans_historique_est_detectee() -> None:
    # La ville est citée dans un tour précédent, pas dans le dernier message.
    inf = _InferenceFactice()
    meteo = _MeteoFactice()
    agent = AgentMeteo(inf, OutilMeteo(meteo))
    requete = AgentRequete(
        "et la pluie demain ?",
        Langue.FR,
        "et la pluie demain ?",
        "ip",
        [{"role": "user", "content": "je suis planteur à Daloa"}],
    )
    await agent.traiter(requete)
    assert meteo.appelee_avec == "Daloa"


@pytest.mark.asyncio
async def test_zone_nord_consigne_sans_prevision() -> None:
    # Une ville de savane du Nord : on n'interroge PAS la météo, on redirige.
    inf = _InferenceFactice()
    meteo = _MeteoFactice()
    agent = AgentMeteo(inf, OutilMeteo(meteo))
    await agent.traiter(_requete("quel temps à Korhogo ?"))
    assert meteo.appelee_avec is None
    assert inf.contexte_recu is not None
    assert "Korhogo" in inf.contexte_recu
    assert "savane" in inf.contexte_recu.lower()


@pytest.mark.asyncio
async def test_sans_localite_demande_la_commune() -> None:
    # Aucune ville : on demande la commune, sans interroger la météo ni inventer.
    inf = _InferenceFactice()
    meteo = _MeteoFactice()
    agent = AgentMeteo(inf, OutilMeteo(meteo))
    await agent.traiter(_requete("y aura-t-il des averses demain ?"))
    assert meteo.appelee_avec is None
    assert inf.contexte_recu is not None
    assert "commune" in inf.contexte_recu.lower()
```

- [ ] **Step 2 : Lancer les tests pour vérifier l'échec**

Run: `cd api && pytest tests/agents/test_agent_meteo.py -v`
Expected: FAIL — `test_zone_nord_consigne_sans_prevision` et `test_sans_localite_demande_la_commune` échouent (l'ancien code géocode « Côte d'Ivoire » ou ne pose pas la consigne) ; `test_localite_dans_historique_est_detectee` échoue (l'ancien `_detecter_localite` ne lit que le dernier tour).

- [ ] **Step 3 : Réécrire `agent_meteo.py`**

Remplacer intégralement `api/app/services/agents/agent_meteo.py` par :

```python
"""Agent Météo : conseil sensible au climat (fenêtres de traitement/récolte).

Tool use : récupère des prévisions via OutilMeteo puis les injecte comme contexte
factuel dans le prompt. Le modèle raisonne sur des données fraîches, pas sa mémoire.

Trois cas, évalués sur tout le fil (historique + dernier tour) :
- localité cacaoyère détectée -> prévisions Open-Meteo ;
- localité non cacaoyère du Nord -> consigne de redirection (pas une zone cacao) ;
- aucune localité -> consigne demandant la commune (jamais de météo inventée).
"""

from __future__ import annotations

from app.domain.agents import AgentRequete
from app.domain.ports import InferencePort
from app.services import localites
from app.services.agents.base import AgentBase, compter_mots_cles
from app.services.outils.meteo import OutilMeteo

# Déclencheurs CLIMATIQUES uniquement. On exclut volontairement les termes
# d'agronomie générale (« traiter », « récolte », « temps ») : ambigus, ils
# détournaient des questions ancrées sur le RAG vers la météo. En l'absence de mot
# climatique, le conseil revient à l'agent RAG (généraliste). Routage par MOT ENTIER.
_MOTS_METEO = (
    "pluie",
    "pluies",
    "pleuvoir",
    "pleut",
    "precipitation",
    "precipitations",
    "précipitation",
    "précipitations",
    "prevision",
    "previsions",
    "prévision",
    "prévisions",
    "averse",
    "averses",
    "meteo",
    "météo",
    "climat",
    "climatique",
    "saison",
    "saisons",
    "secher",
    "sécher",
    "sechage",
    "séchage",
    "ensoleillement",
    "soleil",
    "humidite",
    "humidité",
    "fenetre",
    "fenêtre",
    "irrigation",
    "arrosage",
)

# Consigne quand aucune commune n'est précisée : on ne fabrique JAMAIS de météo, on
# demande la localité (même pattern de souveraineté que l'agent Prix sans cours).
_CONSIGNE_COMMUNE = (
    "Aucune commune n'a été précisée : aucune prévision météo locale fiable n'est "
    "disponible. N'avance AUCUNE donnée météo et n'en invente sous aucun prétexte. "
    "Demande poliment au producteur dans quelle commune (zone cacaoyère) il se "
    "trouve, afin de lui fournir une prévision locale au prochain échange."
)


def _consigne_nord(localite: str) -> str:
    """Consigne pour une localité de savane du Nord (non cacaoyère)."""
    return (
        f"La localité {localite} se situe dans la zone de savane du nord de la Côte "
        "d'Ivoire, au climat trop sec et à la saison des pluies trop courte pour le "
        "cacaoyer : ce n'est pas une zone cacaoyère. N'avance AUCUNE prévision ni "
        "conseil de culture du cacao pour cette localité. Explique avec tact au "
        "producteur qu'elle n'est pas concernée par la culture du cacao et oriente-le "
        "vers l'ANADER pour les cultures adaptées à sa région."
    )


class AgentMeteo(AgentBase):
    """Conseil agronomique tenant compte des prévisions météo locales."""

    nom = "meteo"
    description = "Conseil sensible au climat : fenêtres de traitement et de récolte."
    mots_cles = _MOTS_METEO

    def __init__(self, inference: InferencePort, outil: OutilMeteo) -> None:
        """Initialise l'agent Météo.

        Args:
            inference: Port d'inférence.
            outil: Outil de récupération des prévisions.
        """
        super().__init__(inference)
        self._outil = outil

    async def peut_traiter(self, requete: AgentRequete) -> float:
        """Score élevé si la question évoque le climat (mot entier)."""
        touches = compter_mots_cles(requete.fil_ancre, self.mots_cles)
        if touches == 0:
            return 0.0
        return min(0.7 + 0.1 * touches, 1.0)

    async def _contexte(self, requete: AgentRequete) -> str | None:
        """Construit le contexte selon la localité détectée sur tout le fil."""
        texte = _fil_complet(requete)
        localite = localites.detecter(texte)
        if localite is not None:
            previsions = await self._outil.invoquer(localite=localite)
            return _formater_previsions(localite, previsions)
        nord = localites.detecter_nord(texte)
        if nord is not None:
            return _consigne_nord(nord)
        return _CONSIGNE_COMMUNE


def _fil_complet(requete: AgentRequete) -> str:
    """Concatène les tours utilisateur de l'historique et le dernier tour ancré.

    Une ville citée plus tôt dans la conversation reste ainsi connue au tour suivant.
    """
    tours = [t.get("content", "") for t in requete.historique if t.get("role") == "user"]
    return " ".join([*tours, requete.fil_ancre])


def _formater_previsions(localite: str, previsions: dict[str, object]) -> str | None:
    """Met en forme les prévisions en contexte injectable, ou None si vide."""
    if not previsions:
        return None
    resume = previsions.get("resume", "")
    pluie = previsions.get("pluie_mm_24h", "?")
    return f"Prévisions météo pour {localite} : {resume} (pluie 24h : {pluie} mm)."
```

- [ ] **Step 4 : Lancer les tests pour vérifier le succès**

Run: `cd api && pytest tests/agents/test_agent_meteo.py -v`
Expected: PASS (les 4 tests existants + les 3 nouveaux)

- [ ] **Step 5 : Lint**

Run: `cd api && ruff check app/services/agents/agent_meteo.py tests/agents/test_agent_meteo.py && ruff format --check app/services/agents/agent_meteo.py tests/agents/test_agent_meteo.py`
Expected: no errors

- [ ] **Step 6 : Commit**

```bash
git add api/app/services/agents/agent_meteo.py api/tests/agents/test_agent_meteo.py
git commit -m "feat(agents): l'agent Météo détecte la localité (cacaoyère/Nord/aucune) sur tout le fil"
```

---

### Task 5: Vérification globale

**Files:** aucun (validation transverse).

- [ ] **Step 1 : Suite complète + couverture**

Run: `cd api && pytest --cov=app --cov-report=term-missing -q`
Expected: PASS (aucun test cassé) ; couverture `app/` ≥ 80 %.

- [ ] **Step 2 : Lint + format global**

Run: `cd api && ruff check app tests && ruff format --check app tests`
Expected: no errors

- [ ] **Step 3 : Vérifier l'absence de référence orpheline**

Run: `cd api && grep -rn "geo_defaut\|_detecter_localite\|_LOCALITES_NORD\|_index_zones" app tests`
Expected: aucune correspondance (les anciens symboles ont disparu).

---

## Self-Review

**Spec coverage :**
- Module `localites.py` (detecter/detecter_nord/chercher_zone/LOCALITES_NORD) → Task 1. ✓
- Refactor `contacts.py` → Task 2. ✓
- Refactor `guardrails.py` (import LOCALITES_NORD) → Task 3. ✓
- Agent Météo : 3 cas, détection sur tout le fil, retrait regex + `geo_defaut` → Task 4. ✓
- Tests `localites`, `agent_meteo`, non-régression `contacts`/`guardrails` → Tasks 1-4 + 5. ✓
- Dégradation YAML illisible → test dédié Task 1. ✓
- Couverture ≥ 80 %, lint → Task 5. ✓

**Type consistency :** `detecter -> str|None`, `detecter_nord -> str|None`, `chercher_zone -> tuple[dict,str]|None`, `LOCALITES_NORD: dict[str,str]` — utilisés de façon cohérente en Tasks 2 (déballe `(dr, zone)`), 3 (`.items()`), 4 (str|None). `AgentMeteo(inference, outil)` sans `geo_defaut` — cohérent avec `api_deps.py:111` qui le construit déjà ainsi.

**Placeholder scan :** aucun TODO/TBD ; chaque step de code montre le code complet.
