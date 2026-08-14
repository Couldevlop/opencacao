# C3 — Atelier de livrables : plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produire des livrables traçables (étude de filière, dossier de parcelle, bulletin régional) en Markdown, Word, Excel et PowerPoint, où **aucun chiffre ne sort sans provenance** et où chaque document embarque le manifeste qui permet de le rejouer.

**Architecture:** Un moteur d'orchestration pure (`application/redaction.py`) planifie depuis un gabarit YAML déclaratif, rédige section par section avec les sources déclarées, et assemble un objet `Document` immuable. Les quatre formats sont des **adaptateurs** (`services/rendu/`) qui ne remontent jamais dans le moteur. La génération est **asynchrone** (job persisté + SSE), parce qu'une étude représente 10 à 30 générations et que le time-out edge Cloudflare (~100 s) interdit le synchrone.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, sqlite3 (stdlib), PyYAML, `python-docx`, `openpyxl`, `python-pptx`.

## Global Constraints

- **Spec de référence** : `docs/superpowers/specs/2026-07-28-v3-operationnelle-design.md` §8. Elle fait autorité sur tout ce qui n'est pas tranché ici.
- **Couverture minimale 99 %** sur `api/app/` (`--cov-fail-under=99`), inférence **mockée** — aucun appel réseau en test.
- `ruff format` + `ruff check` propres. Python 3.11+, `from __future__ import annotations`, typage systématique, docstrings Google.
- Logging structuré `structlog` — **jamais `print()`**.
- **Aucune logique métier dans les routers.** Tout passe par `app/services/` ou `app/application/`.
- **Trois nouvelles dépendances autorisées** par Waopron le 28/07/2026 (spec §8.5) : `python-docx`, `openpyxl`, `python-pptx`. Toutes pures Python, sans dépendance système. À justifier par un commentaire dans `api/pyproject.toml`, comme `pypdf` et `maxminddb` avant elles.
- **Aucun service externe** (OpenAI, Anthropic, Cohere) dans le pipeline de production.
- **Ne jamais générer de dosage phytosanitaire**, même en exemple de test.

### Doctrine — les quatre règles que ce chantier ne peut pas enfreindre

1. **D4 — pas d'estimation sans source.** Une section dont les sources sont vides produit un **constat de lacune** qui dit ce qui manque et ce qu'il faudrait fournir. Jamais un chiffre inventé, jamais une fourchette « à titre indicatif ».
2. **Aucune affirmation sans provenance.** Chaque `Affirmation` porte source, date, méthode et confiance. Un test échoue si une affirmation sort du moteur sans source — c'est un critère d'acceptation, pas une politesse.
3. **D5 — le dossier de parcelle n'est pas une déclaration de conformité.** Il porte en tête, de façon non contournable, la mention « dossier préparatoire ». Le gabarit ne peut pas l'omettre : le moteur refuse un gabarit `dossier_parcelle` sans mention.
4. **Le manifeste rend le document rejouable.** Modèle, version applicative, documents RAG mobilisés avec leur empreinte, outils appelés avec leur horodatage, profil matériel, demandeur. C'est la souveraineté rendue vérifiable.

### Deux corrections à la spec, relevées à la lecture du code

**1. `build_messages(consigne=...)` est inutilisable ici.** La spec §8.2 propose de réutiliser ce mécanisme pour imposer le registre analytique. Vérification faite (`api/app/services/prompts.py:112-130`), `consigne` **remplace** le contexte RAG : « Si fournie, elle REMPLACE le contexte RAG : le modèle doit poser une question, pas répondre (pas de RAG). » Or une section d'étude a besoin des deux — du contexte RAG **et** du registre analytique. Le plan passe donc par le paramètre `system_prompt`, qui compose correctement avec `contexte`. Un `SYSTEM_PROMPT_REDACTION` dédié est écrit en Tâche 4.

**2. `RagRecuperateur` perd ses passages.** `contexte_pour()` (`api/app/services/rag.py:397`) rend une chaîne formatée : la source et le score de chaque passage sont détruits. La provenance en a besoin. La Tâche 2 extrait `passages_pour()` et fait de `contexte_pour()` un appelant — refactor DRY, aucun changement de comportement.

## Structure des fichiers

| Fichier | Responsabilité |
|---|---|
| `api/app/models/rapport.py` | Types immuables du document (`Affirmation`, `Section`, `Tableau`, `Manifeste`, `Document`) + schémas d'API |
| `api/app/application/provenance.py` | Construction du manifeste, agrégation et **vérification** des affirmations |
| `api/app/services/gabarits.py` | Chargement et validation des gabarits YAML |
| `api/app/data/gabarits/*.yaml` | Les trois gabarits déclaratifs |
| `api/app/services/prompts_redaction.py` | Registre analytique (système + consigne de section) |
| `api/app/application/redaction.py` | Le moteur : planifier → rédiger → assembler |
| `api/app/services/rendu/markdown.py` | Adaptateur Markdown |
| `api/app/services/rendu/word.py` | Adaptateur Word (`python-docx`) |
| `api/app/services/rendu/tableur.py` | Adaptateur Excel (`openpyxl`) |
| `api/app/services/rendu/diapositives.py` | Adaptateur PowerPoint (`python-pptx`) |
| `api/app/core/rapports_store.py` | Persistance des jobs (SQLite, moule `sessions.py`) |
| `api/app/services/rapports.py` | Service métier : créer un job, l'exécuter, exporter |
| `api/app/routers/rapports.py` | Adaptateur HTTP + SSE |

**Sens des dépendances** : `models/` ← `application/` ← `services/` ← `routers/`. Le moteur (`application/redaction.py`) ne connaît **aucun** format de sortie.

---

## Task 1: Types du document et provenance

**Files:**
- Create: `api/app/models/rapport.py`
- Create: `api/app/application/provenance.py`
- Test: `api/tests/test_provenance.py`

**Interfaces:**
- Consomme : `NiveauConfiance` (`app/models/constat.py`, échelle générique déjà ordonnée avec `rang`/`degrader`).
- Produit :
  - `Affirmation(texte, source, date, methode, confiance)` — `frozen`
  - `Section(titre, corps, affirmations, lacune=False)` — `frozen`
  - `Tableau(titre, entetes, lignes)` — `frozen`
  - `Manifeste(modele, version_modele, version_app, profil_materiel, genere_le, demandeur, documents_rag, outils)` — `frozen`
  - `Document(titre, sous_titre, sections, tableaux, manifeste, mention="")` — `frozen`
  - `provenance.construire_manifeste(...) -> Manifeste`
  - `provenance.affirmations_sans_source(document) -> tuple[Affirmation, ...]`
  - `provenance.tableau_de_provenance(document) -> Tableau`

- [ ] **Step 1: Écrire les tests de provenance**

Créer `api/tests/test_provenance.py` :

```python
"""Tests de la provenance — aucune affirmation ne sort sans source (D4)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.application.provenance import (
    affirmations_sans_source,
    construire_manifeste,
    tableau_de_provenance,
)
from app.models.constat import NiveauConfiance
from app.models.rapport import Affirmation, Document, Section


def _affirmation(source: str = "CNRA") -> Affirmation:
    return Affirmation(
        texte="La production a atteint 2,2 millions de tonnes.",
        source=source,
        date="2025-10-01",
        methode="rag",
        confiance=NiveauConfiance.MOYENNE,
    )


def _document(*affirmations: Affirmation) -> Document:
    return Document(
        titre="Étude de filière",
        sous_titre="Cacao ivoirien",
        sections=(
            Section(titre="Contexte", corps="Un paragraphe.", affirmations=affirmations),
        ),
        tableaux=(),
        manifeste=construire_manifeste(
            modele="opencacao-8b",
            version_modele="1.1.0",
            version_app="0.6.75",
            profil_materiel="cpu",
            demandeur="appareil-a",
            documents_rag=(("CNRA", "a" * 16),),
            outils=(("prix", "2026-07-29T10:00:00+00:00"),),
        ),
    )


def test_une_affirmation_sans_source_est_signalee():
    """Critere d acceptation : ce test echoue si une affirmation sort sans source."""
    orphelines = affirmations_sans_source(_document(_affirmation(source="")))
    assert len(orphelines) == 1


def test_une_affirmation_sourcee_ne_declenche_rien():
    assert affirmations_sans_source(_document(_affirmation())) == ()


def test_une_source_faite_d_espaces_ne_compte_pas():
    """« Sourcer » avec un blanc serait un contournement trivial."""
    assert len(affirmations_sans_source(_document(_affirmation(source="   ")))) == 1


def test_le_manifeste_porte_de_quoi_rejouer_le_document():
    manifeste = _document(_affirmation()).manifeste
    assert manifeste.modele == "opencacao-8b"
    assert manifeste.version_app == "0.6.75"
    assert manifeste.profil_materiel == "cpu"
    assert manifeste.documents_rag == (("CNRA", "a" * 16),)
    assert manifeste.outils == (("prix", "2026-07-29T10:00:00+00:00"),)


def test_le_manifeste_est_horodate_en_utc():
    manifeste = _document(_affirmation()).manifeste
    assert manifeste.genere_le.tzinfo is UTC
    assert manifeste.genere_le <= datetime.now(UTC)


def test_le_tableau_de_provenance_reprend_chaque_affirmation():
    tableau = tableau_de_provenance(_document(_affirmation(), _affirmation("ICCO")))
    assert tableau.entetes == ("Section", "Affirmation", "Source", "Date", "Méthode", "Confiance")
    assert len(tableau.lignes) == 2
    assert tableau.lignes[0][2] == "CNRA"
    assert tableau.lignes[1][2] == "ICCO"


def test_le_tableau_de_provenance_nomme_la_section_d_origine():
    """Un auditeur doit pouvoir remonter du chiffre a l endroit ou il est affirme."""
    tableau = tableau_de_provenance(_document(_affirmation()))
    assert tableau.lignes[0][0] == "Contexte"


def test_un_document_sans_affirmation_donne_un_tableau_vide():
    tableau = tableau_de_provenance(_document())
    assert tableau.lignes == ()
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `cd api && python -m pytest tests/test_provenance.py -v --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.rapport'`

- [ ] **Step 3: Écrire les types du document**

Créer `api/app/models/rapport.py` :

```python
"""Types du document produit par l'atelier de livrables (V3, chantier C3).

**La provenance n'est pas une annexe, c'est une structure.** Une ``Affirmation`` ne
peut pas exister sans porter sa source : le type l'exige, et un test échoue si l'une
d'elles sort du moteur sans en avoir une. C'est ce qui rend le document défendable
devant un auditeur — et rejouable, grâce au ``Manifeste``.

Deux familles, comme ``models/parcelle.py`` et ``models/constat.py`` : types de domaine
immuables (``dataclass(frozen=True)``) et schémas Pydantic d'API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.constat import NiveauConfiance


@dataclass(frozen=True)
class Affirmation:
    """Un énoncé du document, avec ce qui permet de le vérifier.

    Attributes:
        texte: L'énoncé tel qu'il apparaît dans le document.
        source: Nom de la source (« CNRA », « Conseil du Café-Cacao »…). Jamais vide.
        date: Date de la donnée au format ISO, ou chaîne vide si la source n'en porte pas.
        methode: Comment elle a été obtenue (« rag », « outil:prix », « parcelle »).
        confiance: Niveau de confiance déclaré.
    """

    texte: str
    source: str
    date: str
    methode: str
    confiance: NiveauConfiance


@dataclass(frozen=True)
class Section:
    """Une section rédigée du document.

    Attributes:
        titre: Titre de la section, fourni par le gabarit (le modèle n'en émet jamais).
        corps: Prose rédigée, ou constat de lacune si ``lacune`` est vrai.
        affirmations: Affirmations sourcées portées par la section.
        lacune: Vrai si aucune source n'était disponible (D4) — on le dit, on n'estime pas.
    """

    titre: str
    corps: str
    affirmations: tuple[Affirmation, ...] = field(default_factory=tuple)
    lacune: bool = False


@dataclass(frozen=True)
class Tableau:
    """Un tableau de données, rendu tel quel par chaque adaptateur de format."""

    titre: str
    entetes: tuple[str, ...]
    lignes: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class Manifeste:
    """De quoi rejouer le document — la souveraineté rendue vérifiable.

    Attributes:
        modele: Nom du modèle de langage utilisé.
        version_modele: Version du modèle.
        version_app: Version applicative ayant produit le document.
        profil_materiel: « cpu » ou « gpu ».
        genere_le: Horodatage UTC de génération.
        demandeur: Identifiant anonyme du compte ou de l'appareil demandeur.
        documents_rag: Couples ``(source, empreinte)`` des passages mobilisés.
        outils: Couples ``(nom, horodatage ISO)`` des outils appelés.
    """

    modele: str
    version_modele: str
    version_app: str
    profil_materiel: str
    genere_le: datetime
    demandeur: str
    documents_rag: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    outils: tuple[tuple[str, str], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Document:
    """Le livrable assemblé, indépendant de tout format de sortie.

    Attributes:
        titre: Titre du document.
        sous_titre: Sous-titre (sujet, parcelle, direction régionale…).
        sections: Sections rédigées, dans l'ordre du gabarit.
        tableaux: Tableaux de données réelles.
        manifeste: Manifeste de génération.
        mention: Mention non contournable affichée en tête (D5), ou chaîne vide.
    """

    titre: str
    sous_titre: str
    sections: tuple[Section, ...]
    tableaux: tuple[Tableau, ...]
    manifeste: Manifeste
    mention: str = ""


# --------------------------------------------------------------- schémas d'API


class SectionReponse(BaseModel):
    """Section exposée au client."""

    titre: str
    corps: str
    lacune: bool


class RapportReponse(BaseModel):
    """État d'un rapport exposé au client."""

    identifiant: str
    gabarit: str
    sujet: str
    etat: str
    sections_faites: int
    sections_total: int
    titre: str = ""
    sections: list[SectionReponse] = Field(default_factory=list)
```

- [ ] **Step 4: Écrire le module de provenance**

Créer `api/app/application/provenance.py` :

```python
"""Provenance des livrables — au centre, pas en annexe (spec §8.4).

Deux services rendus au reste du chantier :

* **Construire le manifeste** qui rend un document rejouable.
* **Vérifier** qu'aucune affirmation n'a échappé au sourçage. Ce n'est pas une
  politesse : c'est un critère d'acceptation, et un test échoue si une affirmation
  sort sans source.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.models.rapport import Affirmation, Document, Manifeste, Tableau

ENTETES_PROVENANCE = ("Section", "Affirmation", "Source", "Date", "Méthode", "Confiance")


def construire_manifeste(
    modele: str,
    version_modele: str,
    version_app: str,
    profil_materiel: str,
    demandeur: str,
    documents_rag: tuple[tuple[str, str], ...] = (),
    outils: tuple[tuple[str, str], ...] = (),
) -> Manifeste:
    """Assemble le manifeste de génération d'un document.

    Args:
        modele: Nom du modèle de langage.
        version_modele: Version du modèle.
        version_app: Version applicative.
        profil_materiel: « cpu » ou « gpu ».
        demandeur: Identifiant anonyme du demandeur.
        documents_rag: Couples ``(source, empreinte)`` des passages mobilisés.
        outils: Couples ``(nom, horodatage ISO)`` des outils appelés.

    Returns:
        Le manifeste, horodaté en UTC au moment de l'appel.
    """
    return Manifeste(
        modele=modele,
        version_modele=version_modele,
        version_app=version_app,
        profil_materiel=profil_materiel,
        genere_le=datetime.now(UTC),
        demandeur=demandeur,
        documents_rag=documents_rag,
        outils=outils,
    )


def affirmations_sans_source(document: Document) -> tuple[Affirmation, ...]:
    """Retourne les affirmations dépourvues de source.

    Un blanc ne compte pas comme une source : ce serait un contournement trivial de
    la règle, et une ligne de tableau de provenance vide devant un auditeur.

    Args:
        document: Document à vérifier.

    Returns:
        Les affirmations fautives, vide si le document est sain.
    """
    return tuple(
        affirmation
        for section in document.sections
        for affirmation in section.affirmations
        if not affirmation.source.strip()
    )


def tableau_de_provenance(document: Document) -> Tableau:
    """Construit le tableau « d'où vient chaque chiffre ».

    Figure en annexe de tout livrable, et en feuille dédiée dans l'export Excel.

    Args:
        document: Document assemblé.

    Returns:
        Le tableau, dont les lignes suivent l'ordre des sections.
    """
    lignes = tuple(
        (
            section.titre,
            affirmation.texte,
            affirmation.source,
            affirmation.date,
            affirmation.methode,
            affirmation.confiance.value,
        )
        for section in document.sections
        for affirmation in section.affirmations
    )
    return Tableau(titre="Provenance des affirmations", entetes=ENTETES_PROVENANCE, lignes=lignes)
```

- [ ] **Step 5: Lancer les tests**

Run: `cd api && python -m pytest tests/test_provenance.py -v --no-cov`
Expected: PASS — 8 tests

- [ ] **Step 6: Lint et commit**

```bash
cd api && python -m ruff format app/models/rapport.py app/application/provenance.py tests/test_provenance.py && python -m ruff check app/ tests/
cd .. && git add api/app/models/rapport.py api/app/application/provenance.py api/tests/test_provenance.py
git commit -m "feat(rapport): types du document et provenance verifiable

Une Affirmation ne peut pas exister sans porter sa source, et affirmations_sans_source
le verifie : c est un critere d acceptation, pas une politesse. Le manifeste porte de
quoi rejouer le document — modele, version, passages RAG avec empreinte, outils
horodates, profil materiel."
```

---

## Task 2: Le RAG rend ses passages

**Files:**
- Modify: `api/app/services/rag.py` (extraction de `passages_pour`)
- Test: `api/tests/test_rag_passages.py`

**Interfaces:**
- Produit : `RagRecuperateur.passages_pour(question: str) -> list[Passage]`
- `contexte_pour` conserve exactement son comportement actuel et devient un appelant.

> **Pourquoi cette tâche existe.** `contexte_pour()` rend une chaîne formatée : la source et le score de chaque passage sont détruits en chemin. La provenance en a besoin — un document doit dire *d'où* vient chaque affirmation. On extrait donc la récupération, sans toucher au formatage.

- [ ] **Step 1: Écrire les tests**

Créer `api/tests/test_rag_passages.py` :

```python
"""Tests de l'extraction des passages RAG (support de la provenance, C3)."""

from __future__ import annotations

from app.services.rag import Passage, RagIndex, RagRecuperateur


class FauxEmbeddings:
    """Service d'embeddings contrôlé par le test (aucun réseau)."""

    def __init__(self, vecteur: list[float] | None = None) -> None:
        self.vecteur = vecteur

    async def embed(self, textes: list[str]) -> list[list[float]] | None:
        return None if self.vecteur is None else [self.vecteur]


def _index() -> RagIndex:
    return RagIndex.depuis_entrees(
        [
            {"texte": "La production ivoirienne avoisine 2,2 millions de tonnes.", "source": "CNRA"},
            {"texte": "Le prix bord-champ est fixe par campagne.", "source": "ICCO"},
        ],
        [[1.0, 0.0], [0.0, 1.0]],
    )


def _recuperateur(embeddings: FauxEmbeddings) -> RagRecuperateur:
    return RagRecuperateur(embeddings, _index(), top_k=2, min_similarite=0.0)


async def test_les_passages_portent_leur_source():
    """C est CETTE source qui deviendra la provenance d une affirmation."""
    passages = await _recuperateur(FauxEmbeddings([1.0, 0.0])).passages_pour("production")
    assert passages
    assert all(isinstance(p, Passage) for p in passages)
    assert passages[0].source == "CNRA"


async def test_sans_embeddings_aucun_passage():
    """Service d embeddings absent : on ne fabrique pas de contexte."""
    assert await _recuperateur(FauxEmbeddings(None)).passages_pour("production") == []


async def test_le_contexte_reste_construit_a_partir_des_memes_passages():
    """Non-regression : contexte_pour ne change pas de comportement."""
    recuperateur = _recuperateur(FauxEmbeddings([1.0, 0.0]))
    passages = await recuperateur.passages_pour("production")
    contexte = await recuperateur.contexte_pour("production")
    assert contexte is not None
    assert passages[0].texte in contexte


async def test_sans_passage_le_contexte_est_none():
    assert await _recuperateur(FauxEmbeddings(None)).contexte_pour("production") is None
```

> **Avant d'écrire ces tests**, ouvre `api/tests/test_rag.py` et **reprends sa façon de construire un `RagIndex`**. Si `RagIndex.depuis_entrees` n'existe pas sous ce nom, utilise le constructeur réel et **dis-le dans ton rapport** — n'invente pas d'API.

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `cd api && python -m pytest tests/test_rag_passages.py -v --no-cov`
Expected: FAIL — `AttributeError: 'RagRecuperateur' object has no attribute 'passages_pour'`

- [ ] **Step 3: Extraire la récupération**

Dans `api/app/services/rag.py`, remplacer le corps de `contexte_pour` par deux méthodes :

```python
    async def passages_pour(self, question: str) -> list[Passage]:
        """Retourne les passages pertinents, sources comprises.

        Le formatage en bloc de contexte détruit la source de chaque passage ; la
        provenance d'un livrable en a besoin (C3). On sépare donc la récupération du
        formatage, sans changer le comportement de l'une ni de l'autre.

        Args:
            question: Question ou sujet de section.

        Returns:
            Les passages retenus, ``[]`` si le service d'embeddings est absent ou si
            rien ne passe le seuil.
        """
        vecteurs = await self._embeddings.embed([question])
        if not vecteurs:
            return []
        if self._hybride:
            viviers = self._index.vivier_hybride(vecteurs[0], question, self._candidats)
        else:
            viviers = self._index.candidats(vecteurs[0], self._candidats)
        passages = reranker(
            question,
            viviers,
            top_k=self._top_k,
            poids_lexical=self._poids_lexical,
            seuil_dense=self._seuil,
            seuil_lexical=self._seuil_lexical,
        )
        if passages:
            logger.info(
                "rag_contexte",
                passages=len(passages),
                meilleur=round(passages[0].score, 3),
                viviers=len(viviers),
            )
        return passages

    async def contexte_pour(self, question: str) -> str | None:
        """Retourne le bloc de contexte pour la question, ou None si rien de pertinent."""
        passages = await self.passages_pour(question)
        if not passages:
            return None
        return formater_contexte(passages, self._passage_max_chars or None)
```

- [ ] **Step 4: Lancer les tests, y compris la non-régression du RAG**

Run: `cd api && python -m pytest tests/test_rag_passages.py tests/test_rag.py -v --no-cov`
Expected: PASS — aucun test RAG existant modifié.

- [ ] **Step 5: Lint et commit**

```bash
cd api && python -m ruff format app/services/rag.py tests/test_rag_passages.py && python -m ruff check app/ tests/
cd .. && git add api/app/services/rag.py api/tests/test_rag_passages.py
git commit -m "refactor(rag): extraire passages_pour, le contexte detruisait les sources

Le formatage en bloc de contexte perdait la source de chaque passage. La provenance
d un livrable en a besoin : un document doit dire d ou vient chaque affirmation.
contexte_pour devient un appelant, comportement inchange."
```

---

## Task 3: Gabarits déclaratifs

**Files:**
- Create: `api/app/services/gabarits.py`
- Create: `api/app/data/gabarits/etude_filiere.yaml`
- Create: `api/app/data/gabarits/dossier_parcelle.yaml`
- Create: `api/app/data/gabarits/bulletin_regional.yaml`
- Test: `api/tests/test_gabarits.py`

**Interfaces:**
- Produit :
  - `SectionGabarit(titre, sources, consigne)` — `frozen`
  - `Gabarit(identifiant, titre, sous_titre, public, mention, sections)` — `frozen`
  - `charger_gabarit(identifiant: str) -> Gabarit` — lève `GabaritInconnu` ou `GabaritInvalide`
  - `lister_gabarits() -> tuple[str, ...]`
  - `SOURCES_CONNUES: frozenset[str]`

> **Ajouter un gabarit doit être un fichier YAML, pas du code** (spec §8.3). C'est la même discipline d'extensibilité que « ajouter un agent = un adaptateur ».

- [ ] **Step 1: Écrire les tests**

Créer `api/tests/test_gabarits.py` :

```python
"""Tests des gabarits déclaratifs de livrables."""

from __future__ import annotations

import pytest

from app.services.gabarits import (
    GabaritInconnu,
    GabaritInvalide,
    charger_gabarit,
    lister_gabarits,
    lire_gabarit,
)


def test_les_trois_gabarits_de_la_spec_sont_livres():
    assert set(lister_gabarits()) == {"etude_filiere", "dossier_parcelle", "bulletin_regional"}


def test_un_gabarit_inconnu_est_refuse():
    with pytest.raises(GabaritInconnu):
        charger_gabarit("gabarit-qui-n-existe-pas")


def test_un_identifiant_avec_separateur_de_chemin_est_refuse():
    """Traversee de chemin : l identifiant vient d une requete HTTP."""
    with pytest.raises(GabaritInconnu):
        charger_gabarit("../../../etc/passwd")


def test_l_etude_de_filiere_a_des_sections_ordonnees():
    gabarit = charger_gabarit("etude_filiere")
    assert gabarit.identifiant == "etude_filiere"
    assert len(gabarit.sections) >= 5
    assert all(section.titre for section in gabarit.sections)


def test_chaque_section_declare_des_sources_connues():
    """Une source inconnue serait silencieusement ignoree a la redaction."""
    from app.services.gabarits import SOURCES_CONNUES

    for identifiant in lister_gabarits():
        for section in charger_gabarit(identifiant).sections:
            assert set(section.sources) <= SOURCES_CONNUES


def test_le_dossier_de_parcelle_porte_la_mention_preparatoire():
    """D5 : ce dossier n est PAS une declaration de conformite."""
    mention = charger_gabarit("dossier_parcelle").mention.lower()
    assert "préparatoire" in mention
    assert "conformité" not in mention.replace("de conformité", "")


def test_un_gabarit_sans_section_est_invalide():
    with pytest.raises(GabaritInvalide):
        lire_gabarit({"id": "vide", "titre": "T", "sections": []})


def test_un_gabarit_dont_une_section_n_a_pas_de_titre_est_invalide():
    """Le modele n emet jamais un titre : s il manque, personne ne le fournira."""
    with pytest.raises(GabaritInvalide):
        lire_gabarit({"id": "x", "titre": "T", "sections": [{"sources": ["rag"]}]})


def test_un_gabarit_declarant_une_source_inconnue_est_invalide():
    with pytest.raises(GabaritInvalide):
        lire_gabarit(
            {"id": "x", "titre": "T", "sections": [{"titre": "S", "sources": ["horoscope"]}]}
        )


def test_le_titre_accepte_un_champ_a_substituer():
    """« Étude de filière — {sujet} » : la substitution est faite par le moteur."""
    assert "{sujet}" in charger_gabarit("etude_filiere").titre
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `cd api && python -m pytest tests/test_gabarits.py -v --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.gabarits'`

- [ ] **Step 3: Écrire le chargeur**

Créer `api/app/services/gabarits.py` :

```python
"""Gabarits déclaratifs des livrables (spec §8.3).

**Ajouter un gabarit est un fichier YAML, pas du code.** Même discipline
d'extensibilité que « ajouter un agent = un adaptateur ». Le chargeur valide ce que
le moteur ne pourra plus corriger ensuite : une section sans titre resterait sans
titre (le modèle n'en émet jamais), une source inconnue serait silencieusement
ignorée à la rédaction.

Le référentiel vit dans ``app/data/gabarits/``, sur le modèle de ``sources_agro.yaml``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from app.core.logging import get_logger

logger = get_logger(__name__)

_DOSSIER = Path(__file__).resolve().parent.parent / "data" / "gabarits"

# Sources qu'une section peut déclarer. Le moteur sait collecter celles-ci et
# seulement celles-ci ; une valeur hors liste est une faute de gabarit, pas une
# extension silencieuse.
SOURCES_CONNUES = frozenset({"rag", "prix", "meteo", "satellite", "parcelle", "constats"})


class GabaritInconnu(Exception):
    """Le gabarit demandé n'existe pas."""


class GabaritInvalide(Exception):
    """Le gabarit existe mais ne respecte pas le contrat."""


@dataclass(frozen=True)
class SectionGabarit:
    """Une section déclarée par un gabarit.

    Attributes:
        titre: Titre imposé de la section — le modèle n'en produit jamais.
        sources: Sources à collecter pour cette section.
        consigne: Consigne de rédaction propre à la section.
    """

    titre: str
    sources: tuple[str, ...]
    consigne: str


@dataclass(frozen=True)
class Gabarit:
    """Un gabarit de livrable.

    Attributes:
        identifiant: Identifiant du gabarit (nom du fichier, sans extension).
        titre: Titre du document, pouvant porter ``{sujet}``.
        sous_titre: Sous-titre, pouvant porter ``{sujet}``.
        public: Public visé, pour information.
        mention: Mention non contournable en tête du document (D5), ou vide.
        sections: Sections dans l'ordre de rédaction.
    """

    identifiant: str
    titre: str
    sous_titre: str
    public: str
    mention: str
    sections: tuple[SectionGabarit, ...]


def lister_gabarits() -> tuple[str, ...]:
    """Retourne les identifiants des gabarits disponibles, triés."""
    if not _DOSSIER.is_dir():
        return ()
    return tuple(sorted(chemin.stem for chemin in _DOSSIER.glob("*.yaml")))


def lire_gabarit(charge: dict) -> Gabarit:
    """Valide et construit un gabarit à partir de sa charge YAML.

    Args:
        charge: Contenu YAML désérialisé.

    Returns:
        Le gabarit validé.

    Raises:
        GabaritInvalide: Titre manquant, aucune section, section sans titre, ou
            source déclarée hors de ``SOURCES_CONNUES``.
    """
    titre = str(charge.get("titre") or "").strip()
    if not titre:
        raise GabaritInvalide("titre manquant")
    sections_brutes = charge.get("sections") or []
    if not sections_brutes:
        raise GabaritInvalide("aucune section")

    sections: list[SectionGabarit] = []
    for brute in sections_brutes:
        titre_section = str(brute.get("titre") or "").strip()
        if not titre_section:
            raise GabaritInvalide("section sans titre")
        sources = tuple(str(source) for source in brute.get("sources") or ())
        inconnues = set(sources) - SOURCES_CONNUES
        if inconnues:
            raise GabaritInvalide(f"sources inconnues : {', '.join(sorted(inconnues))}")
        sections.append(
            SectionGabarit(
                titre=titre_section,
                sources=sources,
                consigne=str(brute.get("consigne") or "").strip(),
            )
        )

    return Gabarit(
        identifiant=str(charge.get("id") or "").strip(),
        titre=titre,
        sous_titre=str(charge.get("sous_titre") or "").strip(),
        public=str(charge.get("public") or "").strip(),
        mention=str(charge.get("mention") or "").strip(),
        sections=tuple(sections),
    )


def charger_gabarit(identifiant: str) -> Gabarit:
    """Charge un gabarit depuis ``app/data/gabarits``.

    Args:
        identifiant: Identifiant du gabarit (nom de fichier sans extension).

    Returns:
        Le gabarit validé.

    Raises:
        GabaritInconnu: Identifiant absent du dossier des gabarits.
        GabaritInvalide: Le fichier existe mais ne respecte pas le contrat.
    """
    # L'identifiant vient d'une requête HTTP : on n'assemble jamais un chemin avec
    # une donnée client, on choisit dans une liste blanche calculée depuis le disque.
    if identifiant not in lister_gabarits():
        raise GabaritInconnu(identifiant)
    chemin = _DOSSIER / f"{identifiant}.yaml"
    charge = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    return lire_gabarit(charge)
```

- [ ] **Step 4: Écrire les trois gabarits**

Créer `api/app/data/gabarits/etude_filiere.yaml` :

```yaml
# Étude de filière — 5 à 15 pages, destinée aux institutions, bailleurs, chercheurs.
# Registre ANALYTIQUE : troisième personne, aucune adresse au lecteur, aucun renvoi
# ANADER. Le corpus est en registre « conseil au producteur » ; c'est le prompt
# système de rédaction qui redresse le registre, pas le gabarit.
id: etude_filiere
titre: "Étude de filière — {sujet}"
sous_titre: "Cacao ivoirien — analyse documentée"
public: "institutions, bailleurs, chercheurs"
mention: ""
sections:
  - titre: "Contexte de la filière"
    sources: [rag]
    consigne: "Situer le sujet dans la filière cacao ivoirienne : place économique, acteurs, chiffres de cadrage."
  - titre: "État des connaissances agronomiques"
    sources: [rag]
    consigne: "Exposer ce que les sources agronomiques établissent sur le sujet, sans recommandation de traitement."
  - titre: "Conditions climatiques observées"
    sources: [meteo, rag]
    consigne: "Décrire les conditions climatiques relevées et leur incidence documentée sur le sujet."
  - titre: "Marché et prix"
    sources: [prix, rag]
    consigne: "Rapporter le prix officiel en vigueur et son évolution, sans projection ni prévision."
  - titre: "Pression sur le couvert forestier"
    sources: [satellite, rag]
    consigne: "Rapporter les alertes de déforestation disponibles pour la zone, sans conclure à une conformité réglementaire."
  - titre: "Limites de la présente étude"
    sources: []
    consigne: "Énoncer ce que les sources mobilisées ne permettent pas d'établir."
```

Créer `api/app/data/gabarits/dossier_parcelle.yaml` :

```yaml
# Dossier de parcelle — destiné aux coopératives et exportateurs.
# D5 : ce dossier N'EST PAS une déclaration de conformité EUDR. La mention est
# portée en tête, de façon non contournable, et un test vérifie qu'elle y est.
id: dossier_parcelle
titre: "Dossier préparatoire de parcelle — {sujet}"
sous_titre: "Éléments de traçabilité"
public: "coopératives, exportateurs"
mention: >-
  Document préparatoire. Il rassemble des éléments de traçabilité et ne constitue
  ni une déclaration de conformité, ni une attestation réglementaire. Tout maillon
  manquant de la chaîne est signalé comme tel.
sections:
  - titre: "Identification de la parcelle"
    sources: [parcelle]
    consigne: "Rapporter la localisation déclarée, la superficie calculée et la direction régionale de rattachement."
  - titre: "Chaîne d'approvisionnement"
    sources: [parcelle]
    consigne: "Exposer les maillons connus, du producteur à l'exportateur, et déclarer explicitement ceux qui manquent."
  - titre: "Constat satellite"
    sources: [satellite]
    consigne: "Rapporter les alertes de déforestation datées pour la parcelle, sans conclure à une conformité."
  - titre: "Constats visuels"
    sources: [constats]
    consigne: "Rapporter les constats visuels enregistrés pour la parcelle et leur état de revue."
  - titre: "Pièces à fournir"
    sources: []
    consigne: "Énoncer ce qu'il faudrait produire pour compléter le dossier."
```

Créer `api/app/data/gabarits/bulletin_regional.yaml` :

```yaml
# Bulletin régional — une page, périodique, destiné aux producteurs et à l'ANADER.
# Seul gabarit dont le public est le producteur : le registre y reste direct.
id: bulletin_regional
titre: "Bulletin régional — {sujet}"
sous_titre: "Météo, prix et alertes de la zone"
public: "producteurs, ANADER"
mention: ""
sections:
  - titre: "Conditions météorologiques"
    sources: [meteo]
    consigne: "Rapporter les conditions relevées pour la zone sur la période."
  - titre: "Prix du cacao"
    sources: [prix]
    consigne: "Rapporter le prix bord-champ officiel en vigueur."
  - titre: "Alertes de la zone"
    sources: [satellite]
    consigne: "Rapporter les alertes de déforestation relevées sur la zone."
```

- [ ] **Step 5: Lancer les tests**

Run: `cd api && python -m pytest tests/test_gabarits.py -v --no-cov`
Expected: PASS — 10 tests

- [ ] **Step 6: Lint et commit**

```bash
cd api && python -m ruff format app/services/gabarits.py tests/test_gabarits.py && python -m ruff check app/ tests/
cd .. && git add api/app/services/gabarits.py api/app/data/gabarits/ api/tests/test_gabarits.py
git commit -m "feat(rapport): gabarits declaratifs des livrables

Ajouter un gabarit est un fichier YAML, pas du code. Le chargeur valide ce que le
moteur ne pourra plus corriger : une section sans titre resterait sans titre (le
modele n en emet jamais), une source inconnue serait ignoree en silence.

L identifiant vient d une requete HTTP : liste blanche calculee depuis le disque,
jamais un chemin assemble avec une donnee client."
```

---

## Task 4: Le moteur de rédaction

**Files:**
- Create: `api/app/services/prompts_redaction.py`
- Create: `api/app/application/redaction.py`
- Test: `api/tests/test_prompts_redaction.py`, `api/tests/test_redaction.py`

**Interfaces:**
- Consomme : `Gabarit`/`SectionGabarit` (T3), `Affirmation`/`Section`/`Document` (T1), `provenance.construire_manifeste` (T1), `InferencePort`.
- Produit :
  - `SYSTEM_PROMPT_REDACTION` (str), `consigne_section(section, sujet) -> str`
  - `CollecteurPort` (Protocol) : `async def collecter(sujet: str) -> tuple[Affirmation, ...]`
  - `MoteurRedaction(inference, collecteurs, contexte_generation)` avec
    `async def rediger(gabarit, sujet, demandeur, progression=None) -> Document`
  - `ContexteGeneration(modele, version_modele, version_app, profil_materiel)` — `frozen`

> **Le levier est la consigne, pas le plafond de tokens.** Mais `build_messages(consigne=...)` **remplace** le contexte RAG (`prompts.py:128-129`) : inutilisable pour une section qui a besoin des deux. On passe donc par `system_prompt=SYSTEM_PROMPT_REDACTION`, qui compose avec `contexte`.

- [ ] **Step 1: Écrire les tests des consignes**

Créer `api/tests/test_prompts_redaction.py` :

```python
"""Tests du registre analytique imposé aux sections de livrable."""

from __future__ import annotations

from app.services.gabarits import SectionGabarit
from app.services.prompts_redaction import SYSTEM_PROMPT_REDACTION, consigne_section


def test_le_registre_interdit_l_adresse_au_lecteur():
    """Le corpus est en registre « conseil au producteur » : on redresse."""
    minuscules = SYSTEM_PROMPT_REDACTION.lower()
    assert "troisième personne" in minuscules
    assert "ne t'adresse" in minuscules or "sans t'adresser" in minuscules


def test_le_registre_interdit_le_renvoi_anader_dans_une_etude():
    """Renvoyer un bailleur vers l ANADER serait faux."""
    assert "anader" in SYSTEM_PROMPT_REDACTION.lower()


def test_le_registre_interdit_d_inventer_un_chiffre():
    minuscules = SYSTEM_PROMPT_REDACTION.lower()
    assert "n'invente" in minuscules or "jamais un chiffre" in minuscules


def test_le_registre_interdit_les_titres():
    """La structure vient du gabarit ; le modele n emet jamais un titre."""
    minuscules = SYSTEM_PROMPT_REDACTION.lower()
    assert "titre" in minuscules


def test_la_consigne_de_section_reprend_le_titre_et_le_sujet():
    consigne = consigne_section(
        SectionGabarit(titre="Marché et prix", sources=("prix",), consigne="Rapporter le prix."),
        sujet="la campagne 2025-2026",
    )
    assert "Marché et prix" in consigne
    assert "la campagne 2025-2026" in consigne
    assert "Rapporter le prix." in consigne


def test_la_consigne_tient_sans_consigne_propre_a_la_section():
    consigne = consigne_section(
        SectionGabarit(titre="Contexte", sources=(), consigne=""), sujet="le cacao"
    )
    assert "Contexte" in consigne
    assert consigne.strip()
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `cd api && python -m pytest tests/test_prompts_redaction.py -v --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.prompts_redaction'`

- [ ] **Step 3: Écrire le registre analytique**

Créer `api/app/services/prompts_redaction.py` :

```python
"""Registre analytique des livrables (spec §8.2).

**Le corpus n'est pas en registre d'étude.** Les 10 000 paires de
``corpus_cacao_rag.jsonl`` sont intégralement en registre *conseil au producteur*
(« rendez-vous auprès de l'agent ANADER de votre zone »). Sollicitée sur une section
d'étude, la LoRA s'adresserait au producteur et renverrait vers l'ANADER — ce qui est
faux dans un document destiné à un bailleur.

**On redresse par le prompt système, pas par ``consigne``.** ``build_messages`` traite
``consigne`` comme un REMPLACEMENT du contexte RAG (voir sa docstring) : une section a
besoin des deux. ``system_prompt``, lui, compose. Le traitement durable — 200 à 400
exemples de prose analytique ajoutés au corpus, puis un rafraîchissement de LoRA —
viendra après l'événement, sans changement de socle.
"""

from __future__ import annotations

from app.services.gabarits import SectionGabarit

SYSTEM_PROMPT_REDACTION = (
    "Tu rédiges une section d'un document d'analyse sur la filière cacao ivoirienne, "
    "destiné à des institutions et des bailleurs.\n"
    "REGISTRE : prose analytique à la troisième personne. Ne t'adresse jamais au "
    "lecteur, n'emploie ni « vous » ni l'impératif, et ne renvoie jamais vers un agent "
    "ANADER — ce document ne s'adresse pas à un producteur.\n"
    "INTERDITS : n'invente jamais un chiffre, une date ni un pourcentage ; n'écris que "
    "ce que le contexte fourni établit. Ne propose aucun produit phytosanitaire ni "
    "aucune dose. N'écris aucun titre, aucune puce, aucune numérotation : la structure "
    "du document est déjà fixée.\n"
    "FORME : un seul paragraphe de prose continue, de 600 à 800 caractères."
)


def consigne_section(section: SectionGabarit, sujet: str) -> str:
    """Construit la demande de rédaction d'une section.

    Args:
        section: Section déclarée par le gabarit.
        sujet: Sujet du document, substitué dans la demande.

    Returns:
        La demande adressée au modèle pour cette section.
    """
    propre = f" {section.consigne}" if section.consigne else ""
    return (
        f"Rédige la section « {section.titre} » d'un document portant sur {sujet}."
        f"{propre}"
    )
```

- [ ] **Step 4: Lancer les tests des consignes**

Run: `cd api && python -m pytest tests/test_prompts_redaction.py -v --no-cov`
Expected: PASS — 6 tests

- [ ] **Step 5: Écrire les tests du moteur**

Créer `api/tests/test_redaction.py` :

```python
"""Tests du moteur de rédaction — planifier, rédiger, assembler."""

from __future__ import annotations

import pytest

from app.application.redaction import ContexteGeneration, MoteurRedaction
from app.models.constat import NiveauConfiance
from app.models.rapport import Affirmation
from app.services.gabarits import Gabarit, SectionGabarit


class FausseInference:
    """Port d'inférence contrôlé par le test (aucun réseau)."""

    def __init__(self, reponse: str = "Un paragraphe analytique documenté.") -> None:
        self.reponse = reponse
        self.appels: list[dict] = []

    async def generer(self, question: str, **options: object) -> str:
        self.appels.append({"question": question, **options})
        return self.reponse

    def generer_stream(self, *_: object, **__: object):
        raise NotImplementedError

    async def ready(self) -> bool:
        return True


class FauxCollecteur:
    """Collecteur de source contrôlé par le test."""

    def __init__(self, *affirmations: Affirmation) -> None:
        self.affirmations = affirmations
        self.sujets: list[str] = []

    async def collecter(self, sujet: str) -> tuple[Affirmation, ...]:
        self.sujets.append(sujet)
        return self.affirmations


def _affirmation(source: str = "CNRA") -> Affirmation:
    return Affirmation(
        texte="La production avoisine 2,2 millions de tonnes.",
        source=source,
        date="2025-10-01",
        methode="rag",
        confiance=NiveauConfiance.MOYENNE,
    )


def _gabarit(*sections: SectionGabarit, mention: str = "") -> Gabarit:
    return Gabarit(
        identifiant="etude_filiere",
        titre="Étude de filière — {sujet}",
        sous_titre="Analyse documentée",
        public="bailleurs",
        mention=mention,
        sections=sections or (SectionGabarit("Contexte", ("rag",), "Situer le sujet."),),
    )


def _contexte() -> ContexteGeneration:
    return ContexteGeneration(
        modele="opencacao-8b",
        version_modele="1.1.0",
        version_app="0.6.75",
        profil_materiel="cpu",
    )


def _moteur(inference=None, **collecteurs) -> MoteurRedaction:
    return MoteurRedaction(
        inference or FausseInference(),
        collecteurs or {"rag": FauxCollecteur(_affirmation())},
        _contexte(),
    )


async def test_le_document_porte_une_section_par_section_du_gabarit():
    gabarit = _gabarit(
        SectionGabarit("Contexte", ("rag",), ""),
        SectionGabarit("Marché", ("rag",), ""),
    )
    document = await _moteur().rediger(gabarit, "le cacao", "appareil-a")
    assert [section.titre for section in document.sections] == ["Contexte", "Marché"]


async def test_le_sujet_est_substitue_dans_le_titre():
    document = await _moteur().rediger(_gabarit(), "la campagne 2025-2026", "appareil-a")
    assert document.titre == "Étude de filière — la campagne 2025-2026"


async def test_une_section_sans_source_disponible_rend_un_constat_de_lacune():
    """D4 : on dit ce qui manque, on n estime pas."""
    moteur = _moteur(rag=FauxCollecteur())
    document = await moteur.rediger(_gabarit(), "le cacao", "appareil-a")
    section = document.sections[0]
    assert section.lacune is True
    assert section.affirmations == ()
    assert "source" in section.corps.lower()


async def test_une_section_en_lacune_n_appelle_pas_le_modele():
    """Generer sans source, c est exactement la fabrication qu on interdit."""
    inference = FausseInference()
    moteur = MoteurRedaction(inference, {"rag": FauxCollecteur()}, _contexte())
    await moteur.rediger(_gabarit(), "le cacao", "appareil-a")
    assert inference.appels == []


async def test_une_section_sans_source_declaree_rend_aussi_une_lacune():
    """« Limites de l etude » ne declare aucune source : rien a affirmer."""
    gabarit = _gabarit(SectionGabarit("Limites", (), "Enoncer les limites."))
    document = await _moteur().rediger(gabarit, "le cacao", "appareil-a")
    assert document.sections[0].lacune is True


async def test_les_affirmations_collectees_remontent_dans_la_section():
    document = await _moteur().rediger(_gabarit(), "le cacao", "appareil-a")
    assert document.sections[0].affirmations == (_affirmation(),)


async def test_le_registre_analytique_est_impose_au_modele():
    inference = FausseInference()
    moteur = MoteurRedaction(inference, {"rag": FauxCollecteur(_affirmation())}, _contexte())
    await moteur.rediger(_gabarit(), "le cacao", "appareil-a")
    from app.services.prompts_redaction import SYSTEM_PROMPT_REDACTION

    assert inference.appels[0]["system_prompt"] == SYSTEM_PROMPT_REDACTION


async def test_le_manifeste_recense_les_sources_mobilisees():
    document = await _moteur().rediger(_gabarit(), "le cacao", "appareil-a")
    assert document.manifeste.documents_rag == (("CNRA", "2025-10-01"),)


async def test_le_manifeste_ne_repete_pas_deux_fois_la_meme_source():
    """Deux sections citant le CNRA ne doivent pas le lister deux fois."""
    gabarit = _gabarit(
        SectionGabarit("Contexte", ("rag",), ""),
        SectionGabarit("Marché", ("rag",), ""),
    )
    document = await _moteur().rediger(gabarit, "le cacao", "appareil-a")
    assert document.manifeste.documents_rag == (("CNRA", "2025-10-01"),)


async def test_le_manifeste_porte_le_demandeur_et_le_profil():
    document = await _moteur().rediger(_gabarit(), "le cacao", "appareil-b")
    assert document.manifeste.demandeur == "appareil-b"
    assert document.manifeste.profil_materiel == "cpu"


async def test_la_mention_du_gabarit_est_reportee_sur_le_document():
    """D5 : la mention « dossier preparatoire » ne peut pas se perdre en route."""
    gabarit = _gabarit(mention="Document préparatoire.")
    document = await _moteur().rediger(gabarit, "la parcelle p1", "appareil-a")
    assert document.mention == "Document préparatoire."


async def test_la_progression_est_notifiee_section_par_section():
    """C est ce qui alimente le flux SSE : un evenement par section."""
    vues: list[tuple[int, int, str]] = []

    async def _progression(faites: int, total: int, titre: str) -> None:
        vues.append((faites, total, titre))

    gabarit = _gabarit(
        SectionGabarit("Contexte", ("rag",), ""),
        SectionGabarit("Marché", ("rag",), ""),
    )
    await _moteur().rediger(gabarit, "le cacao", "appareil-a", progression=_progression)
    assert vues == [(1, 2, "Contexte"), (2, 2, "Marché")]


async def test_aucune_affirmation_ne_sort_sans_source():
    """Critere d acceptation de la spec, verifie sur un document reellement produit."""
    from app.application.provenance import affirmations_sans_source

    document = await _moteur().rediger(_gabarit(), "le cacao", "appareil-a")
    assert affirmations_sans_source(document) == ()


async def test_une_affirmation_non_sourcee_par_un_collecteur_est_ecartee():
    """Defense en profondeur : le moteur ne laisse pas passer une source vide."""
    moteur = _moteur(rag=FauxCollecteur(_affirmation(source=""), _affirmation()))
    document = await moteur.rediger(_gabarit(), "le cacao", "appareil-a")
    assert document.sections[0].affirmations == (_affirmation(),)


async def test_un_collecteur_qui_echoue_degrade_en_lacune():
    """Un outil indisponible ne doit pas faire tomber tout le document."""

    class CollecteurCasse:
        async def collecter(self, sujet: str) -> tuple[Affirmation, ...]:
            raise RuntimeError("source injoignable")

    moteur = _moteur(rag=CollecteurCasse())
    document = await moteur.rediger(_gabarit(), "le cacao", "appareil-a")
    assert document.sections[0].lacune is True


async def test_une_source_declaree_sans_collecteur_ne_leve_pas():
    """Gabarit valide mais collecteur absent du cablage : lacune, pas exception."""
    moteur = MoteurRedaction(FausseInference(), {}, _contexte())
    document = await moteur.rediger(_gabarit(), "le cacao", "appareil-a")
    assert document.sections[0].lacune is True
```

- [ ] **Step 6: Lancer les tests pour vérifier qu'ils échouent**

Run: `cd api && python -m pytest tests/test_redaction.py -v --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.application.redaction'`

- [ ] **Step 7: Écrire le moteur**

Créer `api/app/application/redaction.py` :

```python
"""Moteur de rédaction des livrables (spec §8.1) — orchestration pure, testable sans réseau.

Trois temps : **planifier** (le gabarit fournit le plan), **rédiger** (section par
section, chacune avec son contexte propre), **assembler** (un ``Document`` immuable).

**Pourquoi section par section, et pas d'un seul jet.** L'analyse du corpus du
28/07/2026 est sans ambiguïté : réponses de 583 caractères en médiane, 1 201 au
maximum, et 0,0 % de titres, de puces, de listes ou de tableaux. Un 8B qui n'a jamais
lu de document de 30 000 caractères ne peut pas en écrire un d'un seul jet ; il peut
écrire quarante paragraphes de 700 caractères, ce qui est le même document. Le
découpage n'est donc pas seulement une parade au time-out Cloudflare : **c'est ce qui
rend l'étude possible.**

**D4 — une section sans source ne mobilise pas le modèle.** Elle rend un constat de
lacune qui dit ce qui manque. Générer sans contexte est exactement la fabrication que
tout le reste du projet combat (v0.6.48).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.application.provenance import construire_manifeste
from app.core.logging import get_logger
from app.domain.ports import InferencePort
from app.models.rapport import Affirmation, Document, Section
from app.services.gabarits import Gabarit, SectionGabarit
from app.services.prompts_redaction import SYSTEM_PROMPT_REDACTION, consigne_section

logger = get_logger(__name__)

# Une section tient en un paragraphe de 600 à 800 caractères : on borne la génération
# en conséquence. Le levier reste la consigne, ce plafond n'est qu'un garde-corps.
MAX_TOKENS_SECTION = 320

# Température basse : un document d'analyse doit être reproductible, pas créatif.
TEMPERATURE_SECTION = 0.3

_LACUNE = (
    "Aucune source mobilisable n'a été trouvée pour cette section. Elle est laissée "
    "en l'état plutôt que renseignée par estimation : les éléments nécessaires "
    "devront être fournis pour la compléter."
)

ProgressionRappel = Callable[[int, int, str], Awaitable[None]]


@runtime_checkable
class CollecteurPort(Protocol):
    """Contrat d'une source mobilisable par une section."""

    async def collecter(self, sujet: str) -> tuple[Affirmation, ...]:
        """Retourne les affirmations sourcées disponibles pour ce sujet."""
        ...


@dataclass(frozen=True)
class ContexteGeneration:
    """Ce que le manifeste doit savoir de l'exécution en cours."""

    modele: str
    version_modele: str
    version_app: str
    profil_materiel: str


class MoteurRedaction:
    """Produit un ``Document`` à partir d'un gabarit et d'un sujet."""

    def __init__(
        self,
        inference: InferencePort,
        collecteurs: dict[str, CollecteurPort],
        contexte: ContexteGeneration,
    ) -> None:
        """Initialise le moteur.

        Args:
            inference: Port du modèle de langage qui rédige les sections.
            collecteurs: Sources mobilisables, indexées par le nom déclaré au gabarit.
            contexte: Éléments d'exécution reportés dans le manifeste.
        """
        self._inference = inference
        self._collecteurs = collecteurs
        self._contexte = contexte

    async def _collecter(self, section: SectionGabarit, sujet: str) -> tuple[Affirmation, ...]:
        """Rassemble les affirmations des sources déclarées par une section.

        Une source injoignable ne fait pas tomber le document : elle ne contribue
        rien, et la section bascule en lacune si plus rien ne reste.
        """
        recoltees: list[Affirmation] = []
        for nom in section.sources:
            collecteur = self._collecteurs.get(nom)
            if collecteur is None:
                logger.warning("collecteur_absent", source=nom, section=section.titre)
                continue
            try:
                recoltees.extend(await collecteur.collecter(sujet))
            except Exception as exc:  # une source défaillante n'emporte pas le document
                logger.warning("collecteur_echoue", source=nom, error=str(exc))
        # Défense en profondeur : une affirmation sans source ne rentre pas, même si
        # un collecteur en produisait une par erreur.
        return tuple(a for a in recoltees if a.source.strip())

    async def _rediger_section(
        self, section: SectionGabarit, sujet: str, affirmations: tuple[Affirmation, ...]
    ) -> Section:
        """Rédige une section, ou rend son constat de lacune."""
        if not affirmations:
            logger.info("section_en_lacune", section=section.titre)
            return Section(titre=section.titre, corps=_LACUNE, affirmations=(), lacune=True)

        contexte = "\n".join(f"- {a.texte} (source : {a.source})" for a in affirmations)
        corps = await self._inference.generer(
            question=consigne_section(section, sujet),
            contexte=contexte,
            system_prompt=SYSTEM_PROMPT_REDACTION,
            temperature=TEMPERATURE_SECTION,
            max_tokens=MAX_TOKENS_SECTION,
        )
        return Section(titre=section.titre, corps=corps.strip(), affirmations=affirmations)

    async def rediger(
        self,
        gabarit: Gabarit,
        sujet: str,
        demandeur: str,
        progression: ProgressionRappel | None = None,
    ) -> Document:
        """Produit le document complet.

        Args:
            gabarit: Gabarit déclaratif fournissant le plan.
            sujet: Sujet du document, substitué dans le titre.
            demandeur: Identifiant anonyme du demandeur, reporté au manifeste.
            progression: Rappel appelé après chaque section — c'est lui qui alimente
                le flux SSE.

        Returns:
            Le document assemblé, manifeste compris.
        """
        sections: list[Section] = []
        total = len(gabarit.sections)
        for index, declaree in enumerate(gabarit.sections, start=1):
            affirmations = await self._collecter(declaree, sujet)
            sections.append(await self._rediger_section(declaree, sujet, affirmations))
            if progression is not None:
                await progression(index, total, declaree.titre)

        sources = tuple(
            dict.fromkeys(
                (a.source, a.date) for section in sections for a in section.affirmations
            )
        )
        manifeste = construire_manifeste(
            modele=self._contexte.modele,
            version_modele=self._contexte.version_modele,
            version_app=self._contexte.version_app,
            profil_materiel=self._contexte.profil_materiel,
            demandeur=demandeur,
            documents_rag=sources,
        )
        logger.info(
            "document_redige",
            gabarit=gabarit.identifiant,
            sections=total,
            lacunes=sum(1 for s in sections if s.lacune),
        )
        return Document(
            titre=gabarit.titre.format(sujet=sujet),
            sous_titre=gabarit.sous_titre.format(sujet=sujet),
            sections=tuple(sections),
            tableaux=(),
            manifeste=manifeste,
            mention=gabarit.mention,
        )
```

> **Vérifie la signature réelle de `InferencePort.generer`** avant d'écrire (`api/app/domain/ports.py`). Si elle n'accepte pas `system_prompt`, **ajoute-le au port et au client** (`api/app/services/inference.py`) en le faisant suivre à `build_messages(system_prompt=...)`, qui l'accepte déjà — et **dis-le dans ton rapport**.

- [ ] **Step 8: Lancer les tests**

Run: `cd api && python -m pytest tests/test_redaction.py tests/test_prompts_redaction.py -v --no-cov`
Expected: PASS

- [ ] **Step 9: Lint et commit**

```bash
cd api && python -m ruff format app/ tests/ && python -m ruff check app/ tests/
cd .. && git add api/app/services/prompts_redaction.py api/app/application/redaction.py api/tests/test_prompts_redaction.py api/tests/test_redaction.py
git commit -m "feat(rapport): moteur de redaction section par section

Le corpus plafonne a 1 201 caracteres et ne contient aucun titre : un 8B ne peut pas
ecrire une etude d un seul jet, mais il peut ecrire quarante paragraphes de 700
caracteres — c est le meme document. Le decoupage n est pas qu une parade au 524.

D4 : une section sans source n appelle PAS le modele, elle rend un constat de lacune.
Generer sans contexte est exactement la fabrication corrigee en v0.6.48."
```

---

## Task 5: Rendu Markdown

**Files:**
- Create: `api/app/services/rendu/__init__.py`
- Create: `api/app/services/rendu/markdown.py`
- Test: `api/tests/test_rendu_markdown.py`

**Interfaces:**
- Consomme : `Document`, `Tableau` (T1), `provenance.tableau_de_provenance` (T1).
- Produit : `rendu_markdown(document: Document) -> str`

- [ ] **Step 1: Écrire les tests**

Créer `api/tests/test_rendu_markdown.py` :

```python
"""Tests de l'adaptateur Markdown."""

from __future__ import annotations

from datetime import UTC, datetime

from app.models.constat import NiveauConfiance
from app.models.rapport import Affirmation, Document, Manifeste, Section, Tableau
from app.services.rendu.markdown import rendu_markdown


def _document(mention: str = "", lacune: bool = False) -> Document:
    return Document(
        titre="Étude de filière — le cacao",
        sous_titre="Analyse documentée",
        sections=(
            Section(
                titre="Contexte",
                corps="Un paragraphe analytique.",
                affirmations=(
                    Affirmation(
                        texte="La production avoisine 2,2 millions de tonnes.",
                        source="CNRA",
                        date="2025-10-01",
                        methode="rag",
                        confiance=NiveauConfiance.MOYENNE,
                    ),
                ),
                lacune=lacune,
            ),
        ),
        tableaux=(
            Tableau(titre="Prix", entetes=("Campagne", "Prix"), lignes=(("2025-2026", "1 500"),)),
        ),
        manifeste=Manifeste(
            modele="opencacao-8b",
            version_modele="1.1.0",
            version_app="0.6.75",
            profil_materiel="cpu",
            genere_le=datetime(2026, 7, 29, 10, 0, tzinfo=UTC),
            demandeur="appareil-a",
            documents_rag=(("CNRA", "2025-10-01"),),
        ),
        mention=mention,
    )


def test_le_titre_est_un_h1():
    assert rendu_markdown(_document()).startswith("# Étude de filière — le cacao")


def test_chaque_section_devient_un_h2():
    assert "## Contexte" in rendu_markdown(_document())


def test_le_corps_de_section_est_rendu():
    assert "Un paragraphe analytique." in rendu_markdown(_document())


def test_la_mention_precede_le_contenu():
    """D5 : non contournable veut dire EN TETE, pas en annexe."""
    rendu = rendu_markdown(_document(mention="Document préparatoire."))
    assert rendu.index("Document préparatoire.") < rendu.index("## Contexte")


def test_sans_mention_aucun_bloc_vide():
    assert "> \n" not in rendu_markdown(_document())


def test_les_tableaux_sont_rendus_en_markdown():
    rendu = rendu_markdown(_document())
    assert "| Campagne | Prix |" in rendu
    assert "| 2025-2026 | 1 500 |" in rendu


def test_le_tableau_de_provenance_figure_en_annexe():
    rendu = rendu_markdown(_document())
    assert "Provenance des affirmations" in rendu
    assert "CNRA" in rendu


def test_le_manifeste_figure_dans_le_document():
    rendu = rendu_markdown(_document())
    assert "opencacao-8b" in rendu
    assert "0.6.75" in rendu
    assert "cpu" in rendu


def test_une_section_en_lacune_est_signalee_comme_telle():
    """Un lecteur doit voir que la section n a pas ete renseignee."""
    assert "lacune" in rendu_markdown(_document(lacune=True)).lower()


def test_un_pipe_dans_une_cellule_ne_casse_pas_le_tableau():
    """Une valeur venant du modele peut contenir n importe quoi."""
    document = _document()
    casse = Document(
        titre=document.titre,
        sous_titre=document.sous_titre,
        sections=document.sections,
        tableaux=(Tableau(titre="T", entetes=("A",), lignes=(("x | y",),)),),
        manifeste=document.manifeste,
    )
    ligne = [l for l in rendu_markdown(casse).splitlines() if "x " in l][0]
    assert ligne.count("|") == 2
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `cd api && python -m pytest tests/test_rendu_markdown.py -v --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.rendu'`

- [ ] **Step 3: Écrire l'adaptateur**

Créer `api/app/services/rendu/__init__.py` :

```python
"""Adaptateurs de rendu des livrables.

Un ``Document`` entre, un format sort. **Ces modules ne remontent jamais dans le
moteur** : ajouter un format ne touche pas une ligne de ``application/redaction.py``.
"""
```

Créer `api/app/services/rendu/markdown.py` :

```python
"""Rendu Markdown — affichage web et streaming en direct.

Aucune dépendance. C'est aussi le format de référence des tests : ce qui manque ici
manquera partout ailleurs.
"""

from __future__ import annotations

from app.application.provenance import tableau_de_provenance
from app.models.rapport import Document, Tableau


def _cellule(valeur: str) -> str:
    """Neutralise ce qui casserait un tableau Markdown.

    Le contenu peut venir du modèle : un ``|`` ou un saut de ligne dans une cellule
    disloquerait la table.
    """
    return valeur.replace("|", "\\|").replace("\n", " ")


def _tableau(tableau: Tableau) -> list[str]:
    """Rend un tableau en Markdown."""
    lignes = [f"**{tableau.titre}**", ""]
    lignes.append("| " + " | ".join(_cellule(e) for e in tableau.entetes) + " |")
    lignes.append("| " + " | ".join("---" for _ in tableau.entetes) + " |")
    lignes.extend(
        "| " + " | ".join(_cellule(valeur) for valeur in ligne) + " |" for ligne in tableau.lignes
    )
    lignes.append("")
    return lignes


def rendu_markdown(document: Document) -> str:
    """Rend le document en Markdown.

    Args:
        document: Document assemblé par le moteur.

    Returns:
        Le document complet, mention et manifeste compris.
    """
    lignes = [f"# {document.titre}", ""]
    if document.sous_titre:
        lignes += [f"*{document.sous_titre}*", ""]
    if document.mention:
        lignes += [f"> **{document.mention}**", ""]

    for section in document.sections:
        lignes += [f"## {section.titre}", ""]
        if section.lacune:
            lignes += ["*Section en lacune — aucune source mobilisable.*", ""]
        lignes += [section.corps, ""]

    for tableau in document.tableaux:
        lignes += _tableau(tableau)

    lignes += ["## Annexe — provenance", ""]
    lignes += _tableau(tableau_de_provenance(document))

    manifeste = document.manifeste
    lignes += [
        "## Annexe — manifeste de génération",
        "",
        f"- Modèle : {manifeste.modele} (version {manifeste.version_modele})",
        f"- Version applicative : {manifeste.version_app}",
        f"- Profil matériel : {manifeste.profil_materiel}",
        f"- Généré le : {manifeste.genere_le.isoformat()}",
        f"- Demandeur : {manifeste.demandeur}",
        f"- Sources mobilisées : {len(manifeste.documents_rag)}",
        "",
    ]
    return "\n".join(lignes)
```

- [ ] **Step 4: Lancer les tests**

Run: `cd api && python -m pytest tests/test_rendu_markdown.py -v --no-cov`
Expected: PASS — 10 tests

- [ ] **Step 5: Lint et commit**

```bash
cd api && python -m ruff format app/services/rendu/ tests/test_rendu_markdown.py && python -m ruff check app/ tests/
cd .. && git add api/app/services/rendu/ api/tests/test_rendu_markdown.py
git commit -m "feat(rendu): adaptateur Markdown, format de reference

Un Document entre, un format sort. La mention D5 est rendue AVANT le contenu — non
contournable veut dire en tete, pas en annexe. Provenance et manifeste sont dans le
document, pas a cote."
```

---

## Task 6: Rendu Word

**Files:**
- Modify: `api/pyproject.toml` (dépendance `python-docx`)
- Create: `api/app/services/rendu/word.py`
- Test: `api/tests/test_rendu_word.py`

**Interfaces:**
- Produit : `rendu_word(document: Document) -> bytes`

> **Reprends les conventions typographiques de `scripts/build_doc_agentique.py`** (`_set_font`, `heading`, `para`, couleurs `OR`/`DARK`/`GREY`) plutôt que d'en inventer d'autres. Lis ce fichier avant d'écrire.

- [ ] **Step 1: Ajouter la dépendance**

Dans `api/pyproject.toml`, section `dependencies`, avec justification comme `pypdf` :

```toml
    # Rendu des livrables V3 (chantier C3) en Word/Excel/PowerPoint. Trois
    # bibliothèques pures Python, sans dépendance système. Dépendances hors spec §2.1
    # autorisées par Waopron le 28/07/2026 (demande explicite de ces trois formats).
    "python-docx==1.1.*",
    "openpyxl==3.1.*",
    "python-pptx==1.0.*",
```

Run: `cd api && pip install -e ".[dev]"`

- [ ] **Step 2: Écrire les tests**

Créer `api/tests/test_rendu_word.py` :

```python
"""Tests de l'adaptateur Word."""

from __future__ import annotations

import io
from datetime import UTC, datetime

from docx import Document as DocxDocument

from app.models.constat import NiveauConfiance
from app.models.rapport import Affirmation, Document, Manifeste, Section, Tableau
from app.services.rendu.word import rendu_word


def _document(mention: str = "") -> Document:
    return Document(
        titre="Étude de filière — le cacao",
        sous_titre="Analyse documentée",
        sections=(
            Section(
                titre="Contexte",
                corps="Un paragraphe analytique.",
                affirmations=(
                    Affirmation(
                        texte="La production avoisine 2,2 millions de tonnes.",
                        source="CNRA",
                        date="2025-10-01",
                        methode="rag",
                        confiance=NiveauConfiance.MOYENNE,
                    ),
                ),
            ),
        ),
        tableaux=(
            Tableau(titre="Prix", entetes=("Campagne", "Prix"), lignes=(("2025-2026", "1 500"),)),
        ),
        manifeste=Manifeste(
            modele="opencacao-8b",
            version_modele="1.1.0",
            version_app="0.6.75",
            profil_materiel="cpu",
            genere_le=datetime(2026, 7, 29, 10, 0, tzinfo=UTC),
            demandeur="appareil-a",
        ),
        mention=mention,
    )


def _textes(octets: bytes) -> list[str]:
    docx = DocxDocument(io.BytesIO(octets))
    return [p.text for p in docx.paragraphs]


def test_le_rendu_est_un_docx_ouvrable():
    """Le fichier doit s ouvrir : un docx invalide se voit a la premiere demo."""
    octets = rendu_word(_document())
    assert octets[:2] == b"PK"  # conteneur ZIP OOXML
    assert _textes(octets)


def test_le_titre_figure_dans_le_document():
    assert "Étude de filière — le cacao" in _textes(rendu_word(_document()))


def test_le_titre_de_section_et_son_corps_figurent():
    textes = _textes(rendu_word(_document()))
    assert "Contexte" in textes
    assert "Un paragraphe analytique." in textes


def test_la_mention_precede_le_contenu():
    """D5 : en tete, pas en annexe."""
    textes = _textes(rendu_word(_document(mention="Document préparatoire.")))
    assert textes.index("Document préparatoire.") < textes.index("Contexte")


def test_les_tableaux_sont_de_vrais_tableaux_word():
    docx = DocxDocument(io.BytesIO(rendu_word(_document())))
    # Tableau de données + tableau de provenance.
    assert len(docx.tables) >= 2
    assert docx.tables[0].cell(0, 0).text == "Campagne"


def test_le_tableau_de_provenance_porte_chaque_affirmation():
    docx = DocxDocument(io.BytesIO(rendu_word(_document())))
    provenance = docx.tables[-1]
    assert provenance.cell(0, 0).text == "Section"
    assert provenance.cell(1, 2).text == "CNRA"


def test_le_manifeste_figure_dans_le_document():
    textes = " ".join(_textes(rendu_word(_document())))
    assert "opencacao-8b" in textes
    assert "0.6.75" in textes
```

- [ ] **Step 3: Lancer les tests pour vérifier qu'ils échouent**

Run: `cd api && python -m pytest tests/test_rendu_word.py -v --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.rendu.word'`

- [ ] **Step 4: Écrire l'adaptateur**

Créer `api/app/services/rendu/word.py` :

```python
"""Rendu Word — dossier de parcelle et étude de filière.

Les conventions typographiques (tailles, couleurs, hiérarchie de titres) reprennent
``scripts/build_doc_agentique.py`` : le projet a déjà une identité de document écrite,
on ne lui en invente pas une seconde.
"""

from __future__ import annotations

import io

from docx import Document as DocxDocument
from docx.shared import Pt, RGBColor

from app.application.provenance import tableau_de_provenance
from app.models.rapport import Document, Tableau

# Palette reprise de scripts/build_doc_agentique.py.
_ORANGE = RGBColor(0xEA, 0x5B, 0x13)
_SOMBRE = RGBColor(0x1F, 0x1F, 0x1F)
_GRIS = RGBColor(0x60, 0x60, 0x60)


def _paragraphe(
    docx, texte: str, *, taille: int = 11, gras: bool = False, couleur=_SOMBRE, italique=False
):
    """Ajoute un paragraphe à la mise en forme du projet."""
    paragraphe = docx.add_paragraph()
    run = paragraphe.add_run(texte)
    run.font.size = Pt(taille)
    run.font.bold = gras
    run.font.italic = italique
    run.font.color.rgb = couleur
    paragraphe.paragraph_format.space_after = Pt(6)
    return paragraphe


def _tableau_word(docx, tableau: Tableau) -> None:
    """Ajoute un tableau natif Word (et non une image ou du texte aligné)."""
    _paragraphe(docx, tableau.titre, taille=12, gras=True, couleur=_ORANGE)
    table = docx.add_table(rows=1, cols=len(tableau.entetes))
    table.style = "Table Grid"
    for colonne, entete in enumerate(tableau.entetes):
        cellule = table.cell(0, colonne)
        cellule.text = entete
        for paragraphe in cellule.paragraphs:
            for run in paragraphe.runs:
                run.font.bold = True
                run.font.size = Pt(10)
    for ligne in tableau.lignes:
        cellules = table.add_row().cells
        for colonne, valeur in enumerate(ligne):
            cellules[colonne].text = valeur
            for paragraphe in cellules[colonne].paragraphs:
                for run in paragraphe.runs:
                    run.font.size = Pt(10)


def rendu_word(document: Document) -> bytes:
    """Rend le document au format Word.

    Args:
        document: Document assemblé par le moteur.

    Returns:
        Les octets du fichier ``.docx``.
    """
    docx = DocxDocument()
    _paragraphe(docx, document.titre, taille=20, gras=True, couleur=_ORANGE)
    if document.sous_titre:
        _paragraphe(docx, document.sous_titre, taille=12, couleur=_GRIS, italique=True)
    if document.mention:
        # D5 : en tête, avant tout contenu, et visuellement distincte.
        _paragraphe(docx, document.mention, taille=11, gras=True, couleur=_ORANGE)

    for section in document.sections:
        _paragraphe(docx, section.titre, taille=15, gras=True, couleur=_ORANGE)
        if section.lacune:
            _paragraphe(
                docx,
                "Section en lacune — aucune source mobilisable.",
                taille=10,
                couleur=_GRIS,
                italique=True,
            )
        _paragraphe(docx, section.corps)

    for tableau in document.tableaux:
        _tableau_word(docx, tableau)

    manifeste = document.manifeste
    _paragraphe(docx, "Annexe — manifeste de génération", taille=15, gras=True, couleur=_ORANGE)
    for ligne in (
        f"Modèle : {manifeste.modele} (version {manifeste.version_modele})",
        f"Version applicative : {manifeste.version_app}",
        f"Profil matériel : {manifeste.profil_materiel}",
        f"Généré le : {manifeste.genere_le.isoformat()}",
        f"Demandeur : {manifeste.demandeur}",
    ):
        _paragraphe(docx, ligne, taille=10, couleur=_GRIS)

    _tableau_word(docx, tableau_de_provenance(document))

    tampon = io.BytesIO()
    docx.save(tampon)
    return tampon.getvalue()
```

- [ ] **Step 5: Lancer les tests**

Run: `cd api && python -m pytest tests/test_rendu_word.py -v --no-cov`
Expected: PASS — 7 tests

- [ ] **Step 6: Lint et commit**

```bash
cd api && python -m ruff format app/services/rendu/word.py tests/test_rendu_word.py && python -m ruff check app/ tests/
cd .. && git add api/pyproject.toml api/app/services/rendu/word.py api/tests/test_rendu_word.py
git commit -m "feat(rendu): adaptateur Word et dependances des formats bureautiques

python-docx, openpyxl et python-pptx : pures Python, sans dependance systeme,
autorisees par Waopron le 28/07/2026. Les conventions typographiques reprennent
build_doc_agentique.py — le projet a deja une identite de document."
```

---

## Task 7: Rendu Excel et PowerPoint

**Files:**
- Create: `api/app/services/rendu/tableur.py`
- Create: `api/app/services/rendu/diapositives.py`
- Test: `api/tests/test_rendu_tableur.py`, `api/tests/test_rendu_diapositives.py`

**Interfaces:**
- Produit : `rendu_excel(document: Document) -> bytes`, `rendu_pptx(document: Document) -> bytes`

- [ ] **Step 1: Écrire les tests Excel**

Créer `api/tests/test_rendu_tableur.py` :

```python
"""Tests de l'adaptateur Excel."""

from __future__ import annotations

import io
from datetime import UTC, datetime

from openpyxl import load_workbook

from app.models.constat import NiveauConfiance
from app.models.rapport import Affirmation, Document, Manifeste, Section, Tableau
from app.services.rendu.tableur import rendu_excel


def _document() -> Document:
    return Document(
        titre="Étude de filière — le cacao",
        sous_titre="Analyse documentée",
        sections=(
            Section(
                titre="Contexte",
                corps="Un paragraphe analytique.",
                affirmations=(
                    Affirmation(
                        texte="La production avoisine 2,2 millions de tonnes.",
                        source="CNRA",
                        date="2025-10-01",
                        methode="rag",
                        confiance=NiveauConfiance.MOYENNE,
                    ),
                ),
            ),
        ),
        tableaux=(
            Tableau(titre="Prix", entetes=("Campagne", "Prix"), lignes=(("2025-2026", "1 500"),)),
        ),
        manifeste=Manifeste(
            modele="opencacao-8b",
            version_modele="1.1.0",
            version_app="0.6.75",
            profil_materiel="cpu",
            genere_le=datetime(2026, 7, 29, 10, 0, tzinfo=UTC),
            demandeur="appareil-a",
        ),
    )


def _classeur(octets: bytes):
    return load_workbook(io.BytesIO(octets))


def test_le_rendu_est_un_xlsx_ouvrable():
    assert _classeur(rendu_excel(_document())).sheetnames


def test_la_provenance_a_sa_propre_feuille():
    """Spec §8.4 : feuille dediee dans l export Excel."""
    assert "Provenance" in _classeur(rendu_excel(_document())).sheetnames


def test_le_manifeste_a_sa_propre_feuille():
    assert "Manifeste" in _classeur(rendu_excel(_document())).sheetnames


def test_les_tableaux_de_donnees_ont_leur_feuille():
    classeur = _classeur(rendu_excel(_document()))
    assert "Prix" in classeur.sheetnames
    assert classeur["Prix"].cell(row=1, column=1).value == "Campagne"
    assert classeur["Prix"].cell(row=2, column=2).value == "1 500"


def test_la_feuille_de_provenance_porte_chaque_affirmation():
    feuille = _classeur(rendu_excel(_document()))["Provenance"]
    assert feuille.cell(row=1, column=1).value == "Section"
    assert feuille.cell(row=2, column=3).value == "CNRA"


def test_le_manifeste_est_lisible_en_paires_cle_valeur():
    feuille = _classeur(rendu_excel(_document()))["Manifeste"]
    paires = {feuille.cell(row=i, column=1).value: feuille.cell(row=i, column=2).value
              for i in range(1, feuille.max_row + 1)}
    assert paires["Modèle"] == "opencacao-8b"
    assert paires["Version applicative"] == "0.6.75"


def test_un_titre_de_tableau_trop_long_ne_casse_pas_le_classeur():
    """Excel plafonne un nom de feuille a 31 caracteres."""
    document = _document()
    long = Document(
        titre=document.titre,
        sous_titre=document.sous_titre,
        sections=document.sections,
        tableaux=(Tableau(titre="T" * 60, entetes=("A",), lignes=(("x",),)),),
        manifeste=document.manifeste,
    )
    assert all(len(nom) <= 31 for nom in _classeur(rendu_excel(long)).sheetnames)
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `cd api && python -m pytest tests/test_rendu_tableur.py -v --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.rendu.tableur'`

- [ ] **Step 3: Écrire l'adaptateur Excel**

Créer `api/app/services/rendu/tableur.py` :

```python
"""Rendu Excel — annexes de données et tableau de provenance.

La provenance a sa **feuille dédiée** (spec §8.4) : c'est le format dans lequel un
auditeur voudra la trier et la filtrer.
"""

from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.styles import Font

from app.application.provenance import tableau_de_provenance
from app.models.rapport import Document, Tableau

# Excel refuse un nom de feuille de plus de 31 caractères, et les caractères \/*?:[]
_LONGUEUR_MAX_FEUILLE = 31
_INTERDITS_FEUILLE = str.maketrans({c: "-" for c in "\\/*?:[]"})


def _nom_de_feuille(titre: str, defaut: str) -> str:
    """Rend un titre utilisable comme nom de feuille Excel."""
    propre = (titre or defaut).translate(_INTERDITS_FEUILLE).strip()
    return (propre or defaut)[:_LONGUEUR_MAX_FEUILLE]


def _ecrire_tableau(feuille, tableau: Tableau) -> None:
    """Écrit un tableau (en-têtes en gras) dans une feuille."""
    feuille.append(list(tableau.entetes))
    for cellule in feuille[1]:
        cellule.font = Font(bold=True)
    for ligne in tableau.lignes:
        feuille.append(list(ligne))


def rendu_excel(document: Document) -> bytes:
    """Rend le document au format Excel.

    Args:
        document: Document assemblé par le moteur.

    Returns:
        Les octets du fichier ``.xlsx``.
    """
    classeur = Workbook()
    classeur.remove(classeur.active)

    for index, tableau in enumerate(document.tableaux, start=1):
        _ecrire_tableau(
            classeur.create_sheet(_nom_de_feuille(tableau.titre, f"Tableau {index}")), tableau
        )

    _ecrire_tableau(classeur.create_sheet("Provenance"), tableau_de_provenance(document))

    manifeste = document.manifeste
    feuille = classeur.create_sheet("Manifeste")
    for cle, valeur in (
        ("Modèle", manifeste.modele),
        ("Version du modèle", manifeste.version_modele),
        ("Version applicative", manifeste.version_app),
        ("Profil matériel", manifeste.profil_materiel),
        ("Généré le", manifeste.genere_le.isoformat()),
        ("Demandeur", manifeste.demandeur),
    ):
        feuille.append([cle, valeur])
    for ligne in feuille.iter_rows(min_col=1, max_col=1):
        ligne[0].font = Font(bold=True)

    tampon = io.BytesIO()
    classeur.save(tampon)
    return tampon.getvalue()
```

- [ ] **Step 4: Écrire les tests PowerPoint**

Créer `api/tests/test_rendu_diapositives.py` :

```python
"""Tests de l'adaptateur PowerPoint — c'est le livrable du moment de scène."""

from __future__ import annotations

import io
from datetime import UTC, datetime

from pptx import Presentation

from app.models.constat import NiveauConfiance
from app.models.rapport import Affirmation, Document, Manifeste, Section
from app.services.rendu.diapositives import rendu_pptx


def _document(mention: str = "", sections: int = 2) -> Document:
    return Document(
        titre="Étude de filière — le cacao",
        sous_titre="Analyse documentée",
        sections=tuple(
            Section(
                titre=f"Section {index}",
                corps="Un paragraphe analytique.",
                affirmations=(
                    Affirmation(
                        texte="La production avoisine 2,2 millions de tonnes.",
                        source="CNRA",
                        date="2025-10-01",
                        methode="rag",
                        confiance=NiveauConfiance.MOYENNE,
                    ),
                ),
            )
            for index in range(1, sections + 1)
        ),
        tableaux=(),
        manifeste=Manifeste(
            modele="opencacao-8b",
            version_modele="1.1.0",
            version_app="0.6.75",
            profil_materiel="cpu",
            genere_le=datetime(2026, 7, 29, 10, 0, tzinfo=UTC),
            demandeur="appareil-a",
        ),
        mention=mention,
    )


def _textes(octets: bytes) -> list[str]:
    presentation = Presentation(io.BytesIO(octets))
    return [
        forme.text_frame.text
        for diapositive in presentation.slides
        for forme in diapositive.shapes
        if forme.has_text_frame
    ]


def _nombre_de_diapositives(octets: bytes) -> int:
    return len(Presentation(io.BytesIO(octets)).slides)


def test_le_rendu_est_un_pptx_ouvrable():
    assert _nombre_de_diapositives(rendu_pptx(_document())) > 0


def test_une_diapositive_de_titre_ouvre_la_presentation():
    assert "Étude de filière — le cacao" in _textes(rendu_pptx(_document()))


def test_une_diapositive_par_section_plus_titre_et_manifeste():
    """1 titre + 2 sections + 1 manifeste = 4."""
    assert _nombre_de_diapositives(rendu_pptx(_document(sections=2))) == 4


def test_chaque_section_donne_son_titre_et_son_corps():
    textes = _textes(rendu_pptx(_document()))
    assert "Section 1" in textes
    assert any("Un paragraphe analytique." in texte for texte in textes)


def test_la_mention_figure_sur_la_diapositive_de_titre():
    """D5 : elle ne peut pas etre releguee en fin de deck."""
    presentation = Presentation(io.BytesIO(rendu_pptx(_document(mention="Document préparatoire."))))
    premiere = [
        forme.text_frame.text
        for forme in presentation.slides[0].shapes
        if forme.has_text_frame
    ]
    assert any("Document préparatoire." in texte for texte in premiere)


def test_la_derniere_diapositive_porte_le_manifeste():
    textes = _textes(rendu_pptx(_document()))
    assert any("opencacao-8b" in texte for texte in textes)
```

- [ ] **Step 5: Lancer les tests pour vérifier qu'ils échouent**

Run: `cd api && python -m pytest tests/test_rendu_diapositives.py -v --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.rendu.diapositives'`

- [ ] **Step 6: Écrire l'adaptateur PowerPoint**

Créer `api/app/services/rendu/diapositives.py` :

```python
"""Rendu PowerPoint — restitution institutionnelle.

**C'est le livrable du moment de scène** (spec §8.7) : faire générer en direct, devant
l'assemblée, la présentation qu'elle est en train de regarder. Il doit donc s'ouvrir
sans réparation et se lire de loin — d'où les corps de texte volontairement grands et
le découpage strict d'une section par diapositive.
"""

from __future__ import annotations

import io

from pptx import Presentation
from pptx.util import Pt

from app.models.rapport import Document

# Dispositions du thème par défaut de python-pptx.
_DISPOSITION_TITRE = 0
_DISPOSITION_TITRE_CONTENU = 1

# Une diapositive lue de loin ne tient pas 800 caractères : on tronque proprement.
_CORPS_MAX = 600


def _tronquer(texte: str) -> str:
    """Tronque un corps de section à la longueur lisible sur écran."""
    if len(texte) <= _CORPS_MAX:
        return texte
    return texte[: _CORPS_MAX - 1].rstrip() + "…"


def rendu_pptx(document: Document) -> bytes:
    """Rend le document au format PowerPoint.

    Args:
        document: Document assemblé par le moteur.

    Returns:
        Les octets du fichier ``.pptx``.
    """
    presentation = Presentation()

    ouverture = presentation.slides.add_slide(
        presentation.slide_layouts[_DISPOSITION_TITRE]
    )
    ouverture.shapes.title.text = document.titre
    sous_titre = document.sous_titre
    if document.mention:
        # D5 : la mention est sur la PREMIÈRE diapositive, pas reléguée en fin de deck.
        sous_titre = f"{sous_titre}\n{document.mention}" if sous_titre else document.mention
    if len(ouverture.placeholders) > 1:
        ouverture.placeholders[1].text = sous_titre

    for section in document.sections:
        diapositive = presentation.slides.add_slide(
            presentation.slide_layouts[_DISPOSITION_TITRE_CONTENU]
        )
        diapositive.shapes.title.text = section.titre
        cadre = diapositive.placeholders[1].text_frame
        cadre.text = _tronquer(section.corps)
        cadre.word_wrap = True
        for paragraphe in cadre.paragraphs:
            for run in paragraphe.runs:
                run.font.size = Pt(16)

    manifeste = document.manifeste
    finale = presentation.slides.add_slide(
        presentation.slide_layouts[_DISPOSITION_TITRE_CONTENU]
    )
    finale.shapes.title.text = "Manifeste de génération"
    finale.placeholders[1].text_frame.text = "\n".join(
        (
            f"Modèle : {manifeste.modele} (version {manifeste.version_modele})",
            f"Version applicative : {manifeste.version_app}",
            f"Profil matériel : {manifeste.profil_materiel}",
            f"Généré le : {manifeste.genere_le.isoformat()}",
            f"Sources mobilisées : {len(manifeste.documents_rag)}",
        )
    )

    tampon = io.BytesIO()
    presentation.save(tampon)
    return tampon.getvalue()
```

- [ ] **Step 7: Lancer les tests**

Run: `cd api && python -m pytest tests/test_rendu_tableur.py tests/test_rendu_diapositives.py -v --no-cov`
Expected: PASS — 13 tests

- [ ] **Step 8: Lint et commit**

```bash
cd api && python -m ruff format app/services/rendu/ tests/ && python -m ruff check app/ tests/
cd .. && git add api/app/services/rendu/tableur.py api/app/services/rendu/diapositives.py api/tests/test_rendu_tableur.py api/tests/test_rendu_diapositives.py
git commit -m "feat(rendu): adaptateurs Excel et PowerPoint

La provenance a sa feuille dediee dans le classeur : c est la que l auditeur voudra
la trier. Le PPTX est le livrable du moment de scene — il doit s ouvrir sans
reparation et se lire de loin."
```

---

## Task 8: Persistance des jobs

**Files:**
- Create: `api/app/core/rapports_store.py`
- Modify: `api/app/core/config.py` (chemin de la base), `api/app/domain/ports.py` (port)
- Test: `api/tests/test_rapports_store.py`

**Interfaces:**
- Produit :
  - `EtatRapport` (Enum) : `EN_ATTENTE`, `EN_COURS`, `TERMINE`, `ECHOUE`
  - `Rapport(identifiant, gabarit, sujet, demandeur, etat, sections_faites, sections_total, markdown, erreur, cree_le, maj_le)` — `frozen`
  - `RapportStore.creer(gabarit, sujet, demandeur) -> Rapport`
  - `RapportStore.obtenir(identifiant, demandeur) -> Rapport | None`
  - `RapportStore.lister(demandeur, limite=50) -> list[Rapport]`
  - `RapportStore.avancer(identifiant, faites, total) -> None`
  - `RapportStore.terminer(identifiant, markdown) -> None`
  - `RapportStore.echouer(identifiant, erreur) -> None`
  - `RapportStore.reprendre_orphelins() -> int`
  - `Settings.rapports_db_path: str = "/data/rapports.db"`

> **Moule à suivre : `api/app/core/sessions.py`.** `sqlite3` stdlib, migrations `PRAGMA user_version`, `asyncio.to_thread`, verrou applicatif en écriture, WAL, **initialisation tolérante aux pannes**. Reprends aussi la leçon de `parcelles_store.py` : la migration s'exécute sous `BEGIN IMMEDIATE`, instruction par instruction (`executescript` valide implicitement et relâcherait le verrou).

- [ ] **Step 1: Écrire les tests**

Créer `api/tests/test_rapports_store.py` :

```python
"""Tests de la persistance des jobs de rapport."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.rapports_store import EtatRapport, RapportStore

DEVICE = "appareil-a"


@pytest.fixture
async def store(tmp_path: Path) -> RapportStore:
    depot = RapportStore(tmp_path / "rapports.db")
    await depot.initialiser()
    return depot


async def test_un_job_nait_en_attente(store: RapportStore):
    rapport = await store.creer("etude_filiere", "le cacao", DEVICE)
    assert rapport.etat is EtatRapport.EN_ATTENTE
    assert rapport.sections_faites == 0


async def test_relire_un_job_par_son_identifiant(store: RapportStore):
    cree = await store.creer("etude_filiere", "le cacao", DEVICE)
    relu = await store.obtenir(cree.identifiant, DEVICE)
    assert relu is not None
    assert relu.sujet == "le cacao"


async def test_un_autre_appareil_ne_voit_pas_le_job(store: RapportStore):
    cree = await store.creer("etude_filiere", "le cacao", DEVICE)
    assert await store.obtenir(cree.identifiant, "appareil-b") is None


async def test_lister_ne_rend_que_les_jobs_du_demandeur(store: RapportStore):
    await store.creer("etude_filiere", "a", DEVICE)
    await store.creer("etude_filiere", "b", "appareil-b")
    assert [r.sujet for r in await store.lister(DEVICE)] == ["a"]


async def test_la_progression_est_persistee(store: RapportStore):
    cree = await store.creer("etude_filiere", "le cacao", DEVICE)
    await store.avancer(cree.identifiant, 2, 6)
    relu = await store.obtenir(cree.identifiant, DEVICE)
    assert relu is not None
    assert (relu.sections_faites, relu.sections_total) == (2, 6)
    assert relu.etat is EtatRapport.EN_COURS


async def test_terminer_conserve_le_markdown(store: RapportStore):
    cree = await store.creer("etude_filiere", "le cacao", DEVICE)
    await store.terminer(cree.identifiant, "# Étude")
    relu = await store.obtenir(cree.identifiant, DEVICE)
    assert relu is not None
    assert relu.etat is EtatRapport.TERMINE
    assert relu.markdown == "# Étude"


async def test_echouer_conserve_la_raison(store: RapportStore):
    cree = await store.creer("etude_filiere", "le cacao", DEVICE)
    await store.echouer(cree.identifiant, "inférence indisponible")
    relu = await store.obtenir(cree.identifiant, DEVICE)
    assert relu is not None
    assert relu.etat is EtatRapport.ECHOUE
    assert "indisponible" in relu.erreur


async def test_un_job_survit_a_un_redemarrage(tmp_path: Path):
    """Critere d acceptation : etat persiste, reprise ou echec propre."""
    chemin = tmp_path / "rapports.db"
    premier = RapportStore(chemin)
    await premier.initialiser()
    cree = await premier.creer("etude_filiere", "le cacao", DEVICE)
    await premier.avancer(cree.identifiant, 1, 6)

    second = RapportStore(chemin)
    await second.initialiser()
    relu = await second.obtenir(cree.identifiant, DEVICE)
    assert relu is not None
    assert relu.sections_faites == 1


async def test_les_jobs_en_cours_au_demarrage_sont_marques_echoues(tmp_path: Path):
    """Un job « en cours » apres redemarrage est orphelin : personne ne le reprendra."""
    chemin = tmp_path / "rapports.db"
    premier = RapportStore(chemin)
    await premier.initialiser()
    cree = await premier.creer("etude_filiere", "le cacao", DEVICE)
    await premier.avancer(cree.identifiant, 1, 6)

    second = RapportStore(chemin)
    await second.initialiser()
    assert await second.reprendre_orphelins() == 1
    relu = await second.obtenir(cree.identifiant, DEVICE)
    assert relu is not None
    assert relu.etat is EtatRapport.ECHOUE


async def test_les_operations_sur_un_depot_non_pret_ne_levent_pas(tmp_path: Path):
    depot = RapportStore(tmp_path / "jamais.db")
    assert await depot.obtenir("x", DEVICE) is None
    assert await depot.lister(DEVICE) == []
    assert await depot.reprendre_orphelins() == 0
    assert await depot.avancer("x", 1, 2) is None
    assert await depot.terminer("x", "#") is None
    assert await depot.echouer("x", "boom") is None
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `cd api && python -m pytest tests/test_rapports_store.py -v --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.core.rapports_store'`

- [ ] **Step 3: Écrire le dépôt**

Créer `api/app/core/rapports_store.py`, en suivant **exactement** le style de `api/app/core/parcelles_store.py` (dont la migration atomique est déjà corrigée) :

```python
"""Persistance des jobs de rapport (V3, chantier C3).

Même moule que :mod:`app.core.sessions` et :mod:`app.core.parcelles_store` : ``sqlite3``
stdlib, migrations versionnées par ``PRAGMA user_version`` appliquées sous
``BEGIN IMMEDIATE``, accès asynchrone par ``asyncio.to_thread``, écritures sérialisées
par un verrou applicatif, WAL, **initialisation tolérante aux pannes**.

**Pourquoi persister.** Une étude représente 10 à 30 générations : sur CPU, cela dépasse
largement toute requête HTTP raisonnable. Le job doit donc survivre à un redémarrage de
l'API — soit repris, soit **proprement déclaré échoué**. Un job resté « en cours » après
un redémarrage est orphelin : personne ne le reprendra, et le laisser dans cet état
ferait tourner un client en attente indéfiniment.
"""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from uuid import uuid4

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class EtatRapport(str, Enum):
    """Cycle de vie d'un job de rapport."""

    EN_ATTENTE = "en_attente"
    EN_COURS = "en_cours"
    TERMINE = "termine"
    ECHOUE = "echoue"


@dataclass(frozen=True)
class Rapport:
    """Un job de rapport et son état."""

    identifiant: str
    gabarit: str
    sujet: str
    demandeur: str
    etat: EtatRapport
    sections_faites: int
    sections_total: int
    markdown: str
    erreur: str
    cree_le: datetime
    maj_le: datetime


class RapportStore:
    """Dépôt SQLite des jobs de rapport."""

    _MIGRATIONS: tuple[str, ...] = (
        """
        CREATE TABLE IF NOT EXISTS rapports (
            id              TEXT PRIMARY KEY,
            gabarit         TEXT NOT NULL,
            sujet           TEXT NOT NULL,
            demandeur       TEXT NOT NULL,
            etat            TEXT NOT NULL,
            sections_faites INTEGER NOT NULL DEFAULT 0,
            sections_total  INTEGER NOT NULL DEFAULT 0,
            markdown        TEXT NOT NULL DEFAULT '',
            erreur          TEXT NOT NULL DEFAULT '',
            cree_le         TEXT NOT NULL,
            maj_le          TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_rapports_demandeur
            ON rapports(demandeur, cree_le DESC);
        CREATE INDEX IF NOT EXISTS idx_rapports_etat ON rapports(etat);
        """,
    )

    def __init__(self, chemin: Path) -> None:
        """Initialise le dépôt.

        Args:
            chemin: Chemin du fichier SQLite (créé si besoin).
        """
        self._chemin = chemin
        self._verrou = asyncio.Lock()
        self._pret = False

    @classmethod
    def from_settings(cls, settings: Settings) -> RapportStore:
        """Construit un dépôt à partir des paramètres applicatifs."""
        return cls(Path(settings.rapports_db_path))

    @property
    def pret(self) -> bool:
        """Indique si le schéma a pu être initialisé."""
        return self._pret

    async def initialiser(self) -> None:
        """Crée/migre le schéma. Tolérant aux pannes : ne lève jamais au démarrage."""
        try:
            await asyncio.to_thread(self._migrer)
            self._pret = True
            logger.info("rapports_prets", chemin=str(self._chemin))
        except (sqlite3.Error, OSError) as exc:
            self._pret = False
            logger.warning("rapports_init_echouee", chemin=str(self._chemin), error=str(exc))

    def _connexion(self) -> sqlite3.Connection:
        """Ouvre une connexion configurée (WAL)."""
        connexion = sqlite3.connect(self._chemin, timeout=10.0)
        connexion.row_factory = sqlite3.Row
        connexion.execute("PRAGMA journal_mode=WAL")
        return connexion

    @staticmethod
    def _instructions(script: str) -> tuple[str, ...]:
        """Découpe un script de migration en instructions exécutables une à une."""
        return tuple(
            instruction.strip() for instruction in script.split(";") if instruction.strip()
        )

    def _migrer(self) -> None:
        """Applique les migrations manquantes, sous verrou d'écriture exclusif.

        ``executescript`` validerait implicitement la transaction et relâcherait le
        verrou pris juste avant — leçon acquise sur ``parcelles_store``.
        """
        self._chemin.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connexion()) as connexion:
            connexion.isolation_level = None
            connexion.execute("BEGIN IMMEDIATE")
            try:
                version = int(connexion.execute("PRAGMA user_version").fetchone()[0])
                for indice in range(version, len(self._MIGRATIONS)):
                    for instruction in self._instructions(self._MIGRATIONS[indice]):
                        connexion.execute(instruction)
                    connexion.execute(f"PRAGMA user_version = {indice + 1}")
                connexion.execute("COMMIT")
            except sqlite3.Error:
                connexion.execute("ROLLBACK")
                raise

    @staticmethod
    def _ligne_en_rapport(ligne: sqlite3.Row) -> Rapport:
        """Reconstruit un rapport depuis une ligne SQL."""
        return Rapport(
            identifiant=ligne["id"],
            gabarit=ligne["gabarit"],
            sujet=ligne["sujet"],
            demandeur=ligne["demandeur"],
            etat=EtatRapport(ligne["etat"]),
            sections_faites=int(ligne["sections_faites"]),
            sections_total=int(ligne["sections_total"]),
            markdown=ligne["markdown"],
            erreur=ligne["erreur"],
            cree_le=datetime.fromisoformat(ligne["cree_le"]),
            maj_le=datetime.fromisoformat(ligne["maj_le"]),
        )

    async def creer(self, gabarit: str, sujet: str, demandeur: str) -> Rapport:
        """Crée un job en attente.

        Args:
            gabarit: Identifiant du gabarit demandé.
            sujet: Sujet du document.
            demandeur: Identifiant anonyme du demandeur.

        Returns:
            Le job créé.
        """
        horodatage = datetime.now(UTC)
        rapport = Rapport(
            identifiant=uuid4().hex,
            gabarit=gabarit,
            sujet=sujet,
            demandeur=demandeur,
            etat=EtatRapport.EN_ATTENTE,
            sections_faites=0,
            sections_total=0,
            markdown="",
            erreur="",
            cree_le=horodatage,
            maj_le=horodatage,
        )
        if not self._pret:
            return rapport
        async with self._verrou:
            await asyncio.to_thread(self._inserer, rapport)
        return rapport

    def _inserer(self, rapport: Rapport) -> None:
        """Insère un job (appelé dans un thread)."""
        with closing(self._connexion()) as connexion:
            connexion.execute(
                "INSERT INTO rapports (id, gabarit, sujet, demandeur, etat, sections_faites, "
                "sections_total, markdown, erreur, cree_le, maj_le) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    rapport.identifiant,
                    rapport.gabarit,
                    rapport.sujet,
                    rapport.demandeur,
                    rapport.etat.value,
                    rapport.sections_faites,
                    rapport.sections_total,
                    rapport.markdown,
                    rapport.erreur,
                    rapport.cree_le.isoformat(),
                    rapport.maj_le.isoformat(),
                ),
            )
            connexion.commit()

    async def obtenir(self, identifiant: str, demandeur: str) -> Rapport | None:
        """Retourne un job de ce demandeur, ou None."""
        if not self._pret:
            return None
        return await asyncio.to_thread(self._lire, identifiant, demandeur)

    def _lire(self, identifiant: str, demandeur: str) -> Rapport | None:
        """Lit un job (appelé dans un thread)."""
        with closing(self._connexion()) as connexion:
            ligne = connexion.execute(
                "SELECT * FROM rapports WHERE id = ? AND demandeur = ?", (identifiant, demandeur)
            ).fetchone()
        return self._ligne_en_rapport(ligne) if ligne else None

    async def lister(self, demandeur: str, limite: int = 50) -> list[Rapport]:
        """Liste les jobs d'un demandeur, les plus récents d'abord."""
        if not self._pret:
            return []
        return await asyncio.to_thread(self._lire_liste, demandeur, limite)

    def _lire_liste(self, demandeur: str, limite: int) -> list[Rapport]:
        """Lit les jobs d'un demandeur (appelé dans un thread)."""
        with closing(self._connexion()) as connexion:
            lignes = connexion.execute(
                "SELECT * FROM rapports WHERE demandeur = ? ORDER BY cree_le DESC LIMIT ?",
                (demandeur, limite),
            ).fetchall()
        return [self._ligne_en_rapport(ligne) for ligne in lignes]

    async def _majorer(self, identifiant: str, colonnes: dict[str, object]) -> None:
        """Applique une mise à jour partielle sur un job."""
        if not self._pret:
            return None
        colonnes["maj_le"] = datetime.now(UTC).isoformat()
        assignations = ", ".join(f"{cle} = ?" for cle in colonnes)
        valeurs = (*colonnes.values(), identifiant)
        async with self._verrou:
            await asyncio.to_thread(self._ecrire, assignations, valeurs)
        return None

    def _ecrire(self, assignations: str, valeurs: tuple) -> None:
        """Écrit une mise à jour (appelé dans un thread).

        ``assignations`` est construit à partir de noms de colonnes **littéraux** du
        module, jamais d'une donnée client ; les valeurs restent paramétrées.
        """
        with closing(self._connexion()) as connexion:
            connexion.execute(f"UPDATE rapports SET {assignations} WHERE id = ?", valeurs)
            connexion.commit()

    async def avancer(self, identifiant: str, faites: int, total: int) -> None:
        """Enregistre la progression d'un job."""
        return await self._majorer(
            identifiant,
            {"etat": EtatRapport.EN_COURS.value, "sections_faites": faites, "sections_total": total},
        )

    async def terminer(self, identifiant: str, markdown: str) -> None:
        """Marque un job terminé et conserve son rendu Markdown."""
        return await self._majorer(
            identifiant, {"etat": EtatRapport.TERMINE.value, "markdown": markdown}
        )

    async def echouer(self, identifiant: str, erreur: str) -> None:
        """Marque un job échoué et conserve la raison."""
        return await self._majorer(
            identifiant, {"etat": EtatRapport.ECHOUE.value, "erreur": erreur}
        )

    async def reprendre_orphelins(self) -> int:
        """Marque échoués les jobs restés « en cours » après un redémarrage.

        Returns:
            Le nombre de jobs assainis.
        """
        if not self._pret:
            return 0
        async with self._verrou:
            return await asyncio.to_thread(self._assainir)

    def _assainir(self) -> int:
        """Assainit les jobs orphelins (appelé dans un thread)."""
        with closing(self._connexion()) as connexion:
            curseur = connexion.execute(
                "UPDATE rapports SET etat = ?, erreur = ?, maj_le = ? WHERE etat IN (?, ?)",
                (
                    EtatRapport.ECHOUE.value,
                    "Interrompu par un redémarrage du service.",
                    datetime.now(UTC).isoformat(),
                    EtatRapport.EN_COURS.value,
                    EtatRapport.EN_ATTENTE.value,
                ),
            )
            connexion.commit()
            nombre = curseur.rowcount
        if nombre:
            logger.info("rapports_orphelins_assainis", nombre=nombre)
        return nombre
```

Ajouter dans `api/app/core/config.py`, à la suite des réglages des parcelles, et documenter le champ dans la docstring `Attributes` :

```python
    # --- Atelier de livrables (V3, chantier C3) ---
    # OFF par défaut : les routes ne sont montées que si le drapeau est levé, comme
    # pour les parcelles — on vérifie que le schéma se crée bien sur /data d'abord.
    rapports_enabled: bool = False
    rapports_db_path: str = "/data/rapports.db"
```

- [ ] **Step 4: Lancer les tests**

Run: `cd api && python -m pytest tests/test_rapports_store.py -v --no-cov`
Expected: PASS — 10 tests

- [ ] **Step 5: Lint et commit**

```bash
cd api && python -m ruff format app/core/rapports_store.py app/core/config.py tests/test_rapports_store.py && python -m ruff check app/ tests/
cd .. && git add api/app/core/rapports_store.py api/app/core/config.py api/tests/test_rapports_store.py
git commit -m "feat(rapport): persistance des jobs, un rapport survit a un redemarrage

Une etude represente 10 a 30 generations : le job doit survivre au redemarrage de
l API — repris, ou PROPREMENT declare echoue. Un job reste « en cours » est orphelin,
et le laisser ainsi ferait attendre un client indefiniment.

Migration sous BEGIN IMMEDIATE, instruction par instruction : executescript validerait
implicitement et relacherait le verrou (lecon de parcelles_store)."
```

---

## Task 9: Service, endpoints et flux SSE

**Files:**
- Create: `api/app/services/rapports.py`
- Create: `api/app/routers/rapports.py`
- Modify: `api/app/api_deps.py`, `api/app/main.py`, `deploy/k8s/api.yaml`
- Test: `api/tests/test_rapports_service.py`, `api/tests/test_rapports_api.py`

**Interfaces:**
- Consomme : `RapportStore` (T8), `MoteurRedaction` (T4), `charger_gabarit` (T3), les quatre adaptateurs de rendu (T5-T7).
- Produit :
  - `ServiceRapports(store, moteur_factory, collecteurs)` avec
    `async def creer(gabarit, sujet, demandeur) -> Rapport`,
    `async def executer(identifiant, demandeur) -> AsyncIterator[dict]`,
    `def exporter(document_markdown, format) -> tuple[bytes, str, str]`
  - Exceptions `GabaritInconnu` (réexportée), `RapportIntrouvable`
  - Routes `POST|GET /v1/rapports`, `GET /v1/rapports/{id}`, `GET /v1/rapports/{id}/stream`, `GET /v1/rapports/{id}/export`

> **Le flux SSE doit émettre son premier octet en moins d'une seconde** (critère d'acceptation, mesuré en prod). Contrainte héritée directement des 524 de juin : *une réponse longue sur CPU doit streamer un premier octet vite.* On émet donc un événement `progress` **avant** toute génération. Reprends la forme des événements de `api/app/application/flux.py` et le montage `StreamingResponse` de `api/app/routers/chat.py:115-118`.

- [ ] **Step 1: Écrire les tests du service**

Créer `api/tests/test_rapports_service.py` :

```python
"""Tests du service des rapports — exécution, flux d'événements, export."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.application.redaction import ContexteGeneration, MoteurRedaction
from app.core.rapports_store import EtatRapport, RapportStore
from app.models.constat import NiveauConfiance
from app.models.rapport import Affirmation
from app.services.rapports import RapportIntrouvable, ServiceRapports

DEVICE = "appareil-a"


class FausseInference:
    async def generer(self, question: str, **_: object) -> str:
        return "Un paragraphe analytique documenté."

    def generer_stream(self, *_: object, **__: object):
        raise NotImplementedError

    async def ready(self) -> bool:
        return True


class FauxCollecteur:
    async def collecter(self, sujet: str) -> tuple[Affirmation, ...]:
        return (
            Affirmation(
                texte="La production avoisine 2,2 millions de tonnes.",
                source="CNRA",
                date="2025-10-01",
                methode="rag",
                confiance=NiveauConfiance.MOYENNE,
            ),
        )


@pytest.fixture
async def store(tmp_path: Path) -> RapportStore:
    depot = RapportStore(tmp_path / "rapports.db")
    await depot.initialiser()
    return depot


def _service(store: RapportStore, inference=None) -> ServiceRapports:
    contexte = ContexteGeneration("opencacao-8b", "1.1.0", "0.6.75", "cpu")
    collecteurs = {nom: FauxCollecteur() for nom in ("rag", "prix", "meteo", "satellite", "parcelle", "constats")}
    return ServiceRapports(
        store,
        lambda: MoteurRedaction(inference or FausseInference(), collecteurs, contexte),
    )


async def test_creer_refuse_un_gabarit_inconnu(store: RapportStore):
    from app.services.gabarits import GabaritInconnu

    with pytest.raises(GabaritInconnu):
        await _service(store).creer("gabarit-fantome", "le cacao", DEVICE)


async def test_le_premier_evenement_arrive_avant_toute_generation(store: RapportStore):
    """Critere d acceptation : premier octet en moins d une seconde."""
    service = _service(store)
    rapport = await service.creer("bulletin_regional", "Daloa", DEVICE)
    flux = service.executer(rapport.identifiant, DEVICE)
    premier = await anext(flux)
    assert premier["type"] == "progress"
    await flux.aclose()


async def test_un_evenement_par_section_puis_un_evenement_final(store: RapportStore):
    service = _service(store)
    rapport = await service.creer("bulletin_regional", "Daloa", DEVICE)
    evenements = [evenement async for evenement in service.executer(rapport.identifiant, DEVICE)]
    types = [evenement["type"] for evenement in evenements]
    assert types[0] == "progress"
    assert types.count("section") == 3
    assert types[-1] == "final"


async def test_le_rapport_est_termine_et_persiste(store: RapportStore):
    service = _service(store)
    rapport = await service.creer("bulletin_regional", "Daloa", DEVICE)
    async for _ in service.executer(rapport.identifiant, DEVICE):
        pass
    relu = await store.obtenir(rapport.identifiant, DEVICE)
    assert relu is not None
    assert relu.etat is EtatRapport.TERMINE
    assert relu.markdown.startswith("# Bulletin régional")


async def test_un_rapport_d_un_autre_appareil_est_introuvable(store: RapportStore):
    service = _service(store)
    rapport = await service.creer("bulletin_regional", "Daloa", DEVICE)
    with pytest.raises(RapportIntrouvable):
        async for _ in service.executer(rapport.identifiant, "appareil-b"):
            pass


async def test_une_inference_qui_echoue_marque_le_job_echoue(store: RapportStore):
    """Un echec doit etre PROPRE : le client ne doit pas attendre indefiniment."""

    class InferenceCassee:
        async def generer(self, question: str, **_: object) -> str:
            raise RuntimeError("inférence indisponible")

        def generer_stream(self, *_: object, **__: object):
            raise NotImplementedError

        async def ready(self) -> bool:
            return False

    service = _service(store, InferenceCassee())
    rapport = await service.creer("bulletin_regional", "Daloa", DEVICE)
    evenements = [evenement async for evenement in service.executer(rapport.identifiant, DEVICE)]
    assert evenements[-1]["type"] == "error"
    relu = await store.obtenir(rapport.identifiant, DEVICE)
    assert relu is not None
    assert relu.etat is EtatRapport.ECHOUE


@pytest.mark.parametrize(
    "format_demande,extension,debut",
    [
        ("md", "md", b"# "),
        ("docx", "docx", b"PK"),
        ("xlsx", "xlsx", b"PK"),
        ("pptx", "pptx", b"PK"),
    ],
)
async def test_les_quatre_formats_sont_exportables(
    store: RapportStore, format_demande, extension, debut
):
    service = _service(store)
    rapport = await service.creer("bulletin_regional", "Daloa", DEVICE)
    async for _ in service.executer(rapport.identifiant, DEVICE):
        pass
    octets, nom, _type = await service.exporter(rapport.identifiant, DEVICE, format_demande)
    assert octets.startswith(debut)
    assert nom.endswith(f".{extension}")


async def test_un_format_inconnu_est_refuse(store: RapportStore):
    service = _service(store)
    rapport = await service.creer("bulletin_regional", "Daloa", DEVICE)
    async for _ in service.executer(rapport.identifiant, DEVICE):
        pass
    with pytest.raises(ValueError):
        await service.exporter(rapport.identifiant, DEVICE, "pdf")


async def test_exporter_un_rapport_non_termine_est_refuse(store: RapportStore):
    service = _service(store)
    rapport = await service.creer("bulletin_regional", "Daloa", DEVICE)
    with pytest.raises(RapportIntrouvable):
        await service.exporter(rapport.identifiant, DEVICE, "md")
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `cd api && python -m pytest tests/test_rapports_service.py -v --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.rapports'`

- [ ] **Step 3: Écrire le service**

Créer `api/app/services/rapports.py` :

```python
"""Service des rapports — exécution asynchrone et export (spec §8.6).

**Le synchrone est exclu.** Une étude représente 10 à 30 générations, et le time-out
edge Cloudflare (~100 s) l'interdirait de toute façon : la leçon des 524 de juin est
acquise. Le job est donc persisté, exécuté en flux, et **le premier événement part
avant toute génération** — un premier octet rapide est ce qui empêche la coupure.

Le document complet n'est **pas** re-généré à l'export : il est rendu une fois en
Markdown à la fin de l'exécution, et les autres formats sont produits à la demande
depuis le document conservé.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

from app.application.redaction import MoteurRedaction
from app.core.logging import get_logger
from app.core.rapports_store import EtatRapport, Rapport, RapportStore
from app.models.rapport import Document
from app.services.gabarits import GabaritInconnu, charger_gabarit
from app.services.rendu.diapositives import rendu_pptx
from app.services.rendu.markdown import rendu_markdown
from app.services.rendu.tableur import rendu_excel
from app.services.rendu.word import rendu_word

logger = get_logger(__name__)

TYPES_MIME = {
    "md": ("text/markdown; charset=utf-8", "md"),
    "docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "docx",
    ),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
    "pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "pptx",
    ),
}


class RapportIntrouvable(Exception):
    """Le rapport visé n'existe pas, n'appartient pas au demandeur, ou n'est pas prêt."""


class ServiceRapports:
    """Crée, exécute et exporte les rapports."""

    def __init__(self, store: RapportStore, moteur: Callable[[], MoteurRedaction]) -> None:
        """Initialise le service.

        Args:
            store: Dépôt de persistance des jobs.
            moteur: Fabrique du moteur de rédaction (une instance par exécution).
        """
        self._store = store
        self._moteur = moteur
        self._documents: dict[str, Document] = {}

    async def creer(self, gabarit: str, sujet: str, demandeur: str) -> Rapport:
        """Crée un job de rapport.

        Args:
            gabarit: Identifiant du gabarit.
            sujet: Sujet du document.
            demandeur: Identifiant anonyme du demandeur.

        Returns:
            Le job créé, en attente.

        Raises:
            GabaritInconnu: Le gabarit demandé n'existe pas.
        """
        charger_gabarit(gabarit)  # valide tôt : inutile de persister un job impossible
        return await self._store.creer(gabarit, sujet, demandeur)

    async def executer(self, identifiant: str, demandeur: str) -> AsyncIterator[dict]:
        """Exécute un job et émet ses événements au fil de la rédaction.

        Args:
            identifiant: Identifiant du job.
            demandeur: Identifiant anonyme du demandeur.

        Yields:
            Événements ``progress``, ``section``, puis ``final`` ou ``error``.

        Raises:
            RapportIntrouvable: Job inconnu ou appartenant à un autre appareil.
        """
        rapport = await self._store.obtenir(identifiant, demandeur)
        if rapport is None:
            raise RapportIntrouvable(identifiant)

        # Premier octet immédiat : c'est ce qui évite la coupure edge (524 de juin).
        yield {"type": "progress", "message": "Préparation du document…"}

        gabarit = charger_gabarit(rapport.gabarit)
        evenements: list[dict] = []

        async def _progression(faites: int, total: int, titre: str) -> None:
            await self._store.avancer(identifiant, faites, total)
            evenements.append(
                {"type": "section", "titre": titre, "faites": faites, "total": total}
            )

        try:
            document = await self._moteur().rediger(
                gabarit, rapport.sujet, demandeur, progression=_progression
            )
        except Exception as exc:
            await self._store.echouer(identifiant, str(exc))
            logger.warning("rapport_echoue", rapport=identifiant, error=str(exc))
            yield {"type": "error", "message": "La génération n'a pas abouti."}
            return

        for evenement in evenements:
            yield evenement

        markdown = rendu_markdown(document)
        self._documents[identifiant] = document
        await self._store.terminer(identifiant, markdown)
        logger.info("rapport_termine", rapport=identifiant, sections=len(document.sections))
        yield {"type": "final", "titre": document.titre, "sections": len(document.sections)}

    async def exporter(self, identifiant: str, demandeur: str, format_demande: str) -> tuple[bytes, str, str]:
        """Exporte un rapport terminé dans l'un des quatre formats.

        Args:
            identifiant: Identifiant du job.
            demandeur: Identifiant anonyme du demandeur.
            format_demande: ``md``, ``docx``, ``xlsx`` ou ``pptx``.

        Returns:
            Le triplet ``(octets, nom de fichier, type MIME)``.

        Raises:
            RapportIntrouvable: Job inconnu, d'un autre appareil, ou non terminé.
            ValueError: Format demandé hors des quatre pris en charge.
        """
        if format_demande not in TYPES_MIME:
            raise ValueError(format_demande)
        rapport = await self._store.obtenir(identifiant, demandeur)
        if rapport is None or rapport.etat is not EtatRapport.TERMINE:
            raise RapportIntrouvable(identifiant)

        type_mime, extension = TYPES_MIME[format_demande]
        nom = f"{rapport.gabarit}-{identifiant[:8]}.{extension}"
        if format_demande == "md":
            return rapport.markdown.encode("utf-8"), nom, type_mime

        document = self._documents.get(identifiant)
        if document is None:
            # Le document n'est plus en mémoire (redémarrage) : seul le Markdown a
            # été persisté. On le dit plutôt que de re-générer un document différent.
            raise RapportIntrouvable(identifiant)
        rendus = {"docx": rendu_word, "xlsx": rendu_excel, "pptx": rendu_pptx}
        return rendus[format_demande](document), nom, type_mime


__all__ = ["GabaritInconnu", "RapportIntrouvable", "ServiceRapports", "TYPES_MIME"]
```

- [ ] **Step 4: Lancer les tests du service**

Run: `cd api && python -m pytest tests/test_rapports_service.py -v --no-cov`
Expected: PASS

- [ ] **Step 5: Écrire les tests de l'API**

Créer `api/tests/test_rapports_api.py`. **Reprends exactement la fixture `client` de `api/tests/test_constats_api.py`** (mêmes variables d'environnement, `PREWARM_ENABLED=false`, `get_settings.cache_clear()`), en remplaçant `PARCELLES_*` par `RAPPORTS_ENABLED=true` et `RAPPORTS_DB_PATH` :

```python
"""Tests des endpoints de l'atelier de livrables."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

ENTETES = {"X-Device-Id": "appareil-a"}


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("RAPPORTS_ENABLED", "true")
    monkeypatch.setenv("RAPPORTS_DB_PATH", str(tmp_path / "rapports.db"))
    monkeypatch.setenv("SESSIONS_DB_PATH", str(tmp_path / "sessions.db"))
    monkeypatch.setenv("PREWARM_ENABLED", "false")
    from app.core.config import get_settings
    from app.main import create_app

    get_settings.cache_clear()
    with TestClient(create_app()) as client_test:
        yield client_test
    get_settings.cache_clear()


def test_creer_sans_entete_appareil_est_refuse(client: TestClient):
    reponse = client.post("/v1/rapports", json={"gabarit": "bulletin_regional", "sujet": "Daloa"})
    assert reponse.status_code == 400


def test_creer_avec_un_gabarit_inconnu_renvoie_404(client: TestClient):
    reponse = client.post(
        "/v1/rapports", json={"gabarit": "fantome", "sujet": "Daloa"}, headers=ENTETES
    )
    assert reponse.status_code == 404


def test_creer_rend_un_job_en_attente(client: TestClient):
    reponse = client.post(
        "/v1/rapports", json={"gabarit": "bulletin_regional", "sujet": "Daloa"}, headers=ENTETES
    )
    assert reponse.status_code == 201
    assert reponse.json()["etat"] == "en_attente"


def test_lister_ne_rend_que_les_jobs_de_l_appareil(client: TestClient):
    client.post(
        "/v1/rapports", json={"gabarit": "bulletin_regional", "sujet": "Daloa"}, headers=ENTETES
    )
    autre = client.get("/v1/rapports", headers={"X-Device-Id": "appareil-b"})
    assert autre.json() == []
    assert len(client.get("/v1/rapports", headers=ENTETES).json()) == 1


def test_obtenir_un_job_d_un_autre_appareil_renvoie_404(client: TestClient):
    identifiant = client.post(
        "/v1/rapports", json={"gabarit": "bulletin_regional", "sujet": "Daloa"}, headers=ENTETES
    ).json()["identifiant"]
    reponse = client.get(f"/v1/rapports/{identifiant}", headers={"X-Device-Id": "appareil-b"})
    assert reponse.status_code == 404


def test_le_flux_emet_un_premier_evenement_de_progression(client: TestClient):
    """Critere d acceptation : premier octet avant toute generation."""
    identifiant = client.post(
        "/v1/rapports", json={"gabarit": "bulletin_regional", "sujet": "Daloa"}, headers=ENTETES
    ).json()["identifiant"]
    with client.stream("GET", f"/v1/rapports/{identifiant}/stream", headers=ENTETES) as flux:
        assert flux.status_code == 200
        premiere = next(flux.iter_lines())
        charge = json.loads(premiere.removeprefix("data: "))
    assert charge["type"] == "progress"


def test_le_flux_d_un_job_inconnu_renvoie_404(client: TestClient):
    reponse = client.get("/v1/rapports/inexistant/stream", headers=ENTETES)
    assert reponse.status_code == 404


def test_exporter_un_job_non_termine_renvoie_404(client: TestClient):
    identifiant = client.post(
        "/v1/rapports", json={"gabarit": "bulletin_regional", "sujet": "Daloa"}, headers=ENTETES
    ).json()["identifiant"]
    reponse = client.get(f"/v1/rapports/{identifiant}/export?format=md", headers=ENTETES)
    assert reponse.status_code == 404


def test_exporter_dans_un_format_inconnu_renvoie_422(client: TestClient):
    identifiant = client.post(
        "/v1/rapports", json={"gabarit": "bulletin_regional", "sujet": "Daloa"}, headers=ENTETES
    ).json()["identifiant"]
    reponse = client.get(f"/v1/rapports/{identifiant}/export?format=pdf", headers=ENTETES)
    assert reponse.status_code == 422
```

- [ ] **Step 6: Écrire le routeur et le câblage**

Créer `api/app/routers/rapports.py` :

```python
"""Endpoints /v1/rapports — atelier de livrables (V3, C3).

Adaptateurs HTTP du service métier : **aucune règle ici**. On traduit les exceptions
en codes de statut, et c'est tout.

Cloisonnement par appareil, comme les parcelles : chaque requête porte un
``X-Device-Id`` obligatoire.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from app.api_deps import get_device_id_obligatoire, get_service_rapports
from app.models.rapport import RapportReponse
from app.services.gabarits import GabaritInconnu
from app.services.rapports import RapportIntrouvable, ServiceRapports

router = APIRouter(prefix="/v1", tags=["rapports"])


class CreerRapportRequest(BaseModel):
    """Demande de génération d'un livrable."""

    gabarit: str = Field(min_length=1, max_length=64)
    sujet: str = Field(min_length=1, max_length=200)


def _en_reponse(rapport) -> RapportReponse:
    """Projette un job sur son schéma d'API."""
    return RapportReponse(
        identifiant=rapport.identifiant,
        gabarit=rapport.gabarit,
        sujet=rapport.sujet,
        etat=rapport.etat.value,
        sections_faites=rapport.sections_faites,
        sections_total=rapport.sections_total,
    )


@router.post("/rapports", response_model=RapportReponse, status_code=status.HTTP_201_CREATED)
async def creer_rapport(
    payload: CreerRapportRequest,
    device_id: str = Depends(get_device_id_obligatoire),
    service: ServiceRapports = Depends(get_service_rapports),
) -> RapportReponse:
    """Crée un job de génération.

    Raises:
        HTTPException: 404 si le gabarit est inconnu.
    """
    try:
        rapport = await service.creer(payload.gabarit, payload.sujet, device_id)
    except GabaritInconnu as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Gabarit inconnu."
        ) from exc
    return _en_reponse(rapport)


@router.get("/rapports", response_model=list[RapportReponse])
async def lister_rapports(
    device_id: str = Depends(get_device_id_obligatoire),
    service: ServiceRapports = Depends(get_service_rapports),
) -> list[RapportReponse]:
    """Liste les jobs de l'appareil appelant."""
    return [_en_reponse(rapport) for rapport in await service.lister(device_id)]


@router.get("/rapports/{identifiant}", response_model=RapportReponse)
async def obtenir_rapport(
    identifiant: str,
    device_id: str = Depends(get_device_id_obligatoire),
    service: ServiceRapports = Depends(get_service_rapports),
) -> RapportReponse:
    """Retourne l'état d'un job.

    Raises:
        HTTPException: 404 si le job est inconnu ou d'un autre appareil.
    """
    rapport = await service.obtenir(identifiant, device_id)
    if rapport is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rapport inconnu.")
    return _en_reponse(rapport)


@router.get("/rapports/{identifiant}/stream")
async def flux_rapport(
    identifiant: str,
    device_id: str = Depends(get_device_id_obligatoire),
    service: ServiceRapports = Depends(get_service_rapports),
) -> StreamingResponse:
    """Diffuse la rédaction, section par section.

    Le premier événement part avant toute génération : une réponse longue sur CPU
    doit streamer un premier octet vite, sans quoi l'edge coupe (524 de juin).

    Raises:
        HTTPException: 404 si le job est inconnu ou d'un autre appareil.
    """
    if await service.obtenir(identifiant, device_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rapport inconnu.")

    async def _evenements() -> AsyncIterator[str]:
        async for evenement in service.executer(identifiant, device_id):
            yield f"data: {json.dumps(evenement, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        _evenements(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/rapports/{identifiant}/export")
async def exporter_rapport(
    identifiant: str,
    format: str = Query(default="md"),
    device_id: str = Depends(get_device_id_obligatoire),
    service: ServiceRapports = Depends(get_service_rapports),
) -> Response:
    """Exporte un rapport terminé.

    Raises:
        HTTPException: 422 si le format est inconnu, 404 si le rapport ne l'est pas
            encore, est inconnu ou appartient à un autre appareil.
    """
    try:
        octets, nom, type_mime = await service.exporter(identifiant, device_id, format)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Format inconnu : md, docx, xlsx ou pptx.",
        ) from exc
    except RapportIntrouvable as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Rapport inconnu ou non terminé."
        ) from exc
    return Response(
        content=octets,
        media_type=type_mime,
        headers={"Content-Disposition": f'attachment; filename="{nom}"'},
    )
```

Ajouter à `ServiceRapports` (Task 9, Step 3) les deux relais que le routeur consomme :

```python
    async def obtenir(self, identifiant: str, demandeur: str) -> Rapport | None:
        """Retourne un job de ce demandeur, ou None.

        Args:
            identifiant: Identifiant du job.
            demandeur: Identifiant anonyme du demandeur.

        Returns:
            Le job, ou ``None`` s'il n'existe pas pour ce demandeur.
        """
        return await self._store.obtenir(identifiant, demandeur)

    async def lister(self, demandeur: str) -> list[Rapport]:
        """Liste les jobs d'un demandeur, les plus récents d'abord.

        Args:
            demandeur: Identifiant anonyme du demandeur.

        Returns:
            Les jobs, ``[]`` si le dépôt n'est pas prêt.
        """
        return await self._store.lister(demandeur)
```

Dans `api/app/api_deps.py`, ajouter la dépendance sur le moule de `get_service_constats` :

```python
def get_service_rapports(request: Request) -> ServiceRapports:
    """Retourne le service des rapports stocké dans l'état de l'application."""
    return request.app.state.service_rapports
```

Dans `api/app/main.py`, à la suite du câblage du constat visuel :

```python
    app.state.rapports = RapportStore.from_settings(settings)
    if settings.rapports_enabled:
        await app.state.rapports.initialiser()
        # Un job « en cours » après un redémarrage est orphelin : personne ne le
        # reprendra. On l'assainit au démarrage plutôt que de laisser un client attendre.
        await app.state.rapports.reprendre_orphelins()
    app.state.service_rapports = ServiceRapports(
        app.state.rapports,
        lambda: MoteurRedaction(
            app.state.inference,
            _construire_collecteurs(app, settings),
            ContexteGeneration(
                modele=settings.model_name,
                version_modele=settings.model_version,
                version_app=__version__,
                profil_materiel=settings.profil_materiel,
            ),
        ),
    )
```

et, dans `create_app`, monter le routeur derrière son drapeau, comme les parcelles :

```python
    if settings.rapports_enabled:
        app.include_router(rapports.router)
```

Créer `api/app/services/collecteurs.py` — les enveloppes qui transforment les sources existantes en affirmations sourcées. **Tous les outils du projet partagent le même contrat** (`async def invoquer(**kwargs) -> dict[str, object]`, dict vide = source indisponible, vérifié sur `meteo.py:47`, `prix.py:33`, `satellite.py:50`), ce qui permet une seule enveloppe générique :

```python
"""Collecteurs — les sources existantes rendues citables (V3, chantier C3).

Le moteur de rédaction ne connaît que des ``Affirmation`` sourcées. Ce module fait la
conversion depuis ce que le projet possède déjà : le RAG, les outils météo/prix/
satellite, les parcelles et leurs constats.

**Un dict vide vaut « indisponible ».** C'est le contrat déjà tenu par tous les outils
(``outils/indisponible.py``) : la source ne ment pas, elle ne rend rien. Le moteur
traduit ce silence en constat de lacune, ce qui est exactement le comportement voulu
par D4 — on dit ce qui manque, on n'estime pas.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from app.core.logging import get_logger
from app.models.constat import NiveauConfiance
from app.models.rapport import Affirmation

logger = get_logger(__name__)


class OutilPort(Protocol):
    """Contrat commun des outils du projet."""

    async def invoquer(self, **kwargs: object) -> dict[str, object]:
        """Retourne les données de la source, ou un dict vide si indisponible."""
        ...


class CollecteurOutil:
    """Rend citable un outil existant (météo, prix, satellite)."""

    def __init__(self, outil: OutilPort, source: str, cle_argument: str = "") -> None:
        """Initialise le collecteur.

        Args:
            outil: Outil à envelopper.
            source: Nom de la source à citer dans le document.
            cle_argument: Nom de l'argument recevant le sujet (« localite »), ou
                chaîne vide si l'outil n'en prend aucun (le prix, par exemple).
        """
        self._outil = outil
        self._source = source
        self._cle = cle_argument

    async def collecter(self, sujet: str) -> tuple[Affirmation, ...]:
        """Invoque l'outil et convertit sa réponse en affirmations sourcées.

        Args:
            sujet: Sujet du document, passé à l'outil s'il attend un argument.

        Returns:
            Une affirmation par donnée rendue, vide si la source est indisponible.
        """
        arguments = {self._cle: sujet} if self._cle else {}
        donnees = await self._outil.invoquer(**arguments)
        if not donnees:
            logger.info("collecteur_source_vide", source=self._source)
            return ()
        date = str(donnees.get("date") or donnees.get("horodatage") or "")
        return tuple(
            Affirmation(
                texte=f"{cle} : {valeur}",
                source=self._source,
                date=date,
                methode=f"outil:{self._source.lower()}",
                confiance=NiveauConfiance.MOYENNE,
            )
            for cle, valeur in donnees.items()
            if cle not in {"date", "horodatage"} and valeur not in (None, "", [], {})
        )


class CollecteurRag:
    """Rend citables les passages du corpus documentaire."""

    def __init__(self, recuperateur, methode: str = "rag") -> None:
        """Initialise le collecteur.

        Args:
            recuperateur: ``RagRecuperateur`` exposant ``passages_pour``.
            methode: Méthode reportée dans la provenance.
        """
        self._recuperateur = recuperateur
        self._methode = methode

    async def collecter(self, sujet: str) -> tuple[Affirmation, ...]:
        """Retourne les passages pertinents, chacun avec sa source.

        Args:
            sujet: Sujet ou titre de section servant de requête.

        Returns:
            Une affirmation par passage retenu, vide si le RAG est absent.
        """
        if self._recuperateur is None:
            return ()
        passages = await self._recuperateur.passages_pour(sujet)
        return tuple(
            Affirmation(
                texte=passage.texte,
                source=passage.source,
                date="",
                methode=self._methode,
                confiance=(
                    NiveauConfiance.ELEVEE if passage.score >= 0.75 else NiveauConfiance.MOYENNE
                ),
            )
            for passage in passages
            if passage.source.strip()
        )


class CollecteurParcelle:
    """Rend citables les données déclarées d'une parcelle."""

    def __init__(self, service_parcelles, proprietaire: str) -> None:
        """Initialise le collecteur.

        Args:
            service_parcelles: Service métier des parcelles.
            proprietaire: Identifiant anonyme de l'appareil propriétaire.
        """
        self._service = service_parcelles
        self._proprietaire = proprietaire

    async def collecter(self, sujet: str) -> tuple[Affirmation, ...]:
        """Retourne les éléments connus de la parcelle désignée par le sujet.

        Args:
            sujet: Identifiant de la parcelle.

        Returns:
            Une affirmation par élément déclaré, vide si la parcelle est inconnue.
        """
        parcelle = await self._service.obtenir(sujet, self._proprietaire)
        if parcelle is None:
            return ()
        horodatage = parcelle.maj_le.isoformat() if parcelle.maj_le else ""
        elements = [
            ("Localité déclarée", parcelle.localite),
            ("Direction régionale de rattachement", parcelle.direction_regionale),
        ]
        if parcelle.geometrie is not None:
            elements.append(
                ("Superficie calculée (ha)", f"{parcelle.geometrie.superficie_ha:.2f}")
            )
        return tuple(
            Affirmation(
                texte=f"{libelle} : {valeur}",
                source="Déclaration du producteur",
                date=horodatage,
                methode="parcelle",
                confiance=NiveauConfiance.MOYENNE,
            )
            for libelle, valeur in elements
            if valeur
        )


def horodatage_courant() -> str:
    """Retourne l'horodatage UTC courant, pour le manifeste des outils."""
    return datetime.now(UTC).isoformat()
```

Puis, dans `api/app/main.py` :

```python
def _construire_collecteurs(app: FastAPI, settings: Settings) -> dict[str, object]:
    """Assemble les sources mobilisables par les gabarits.

    Une source absente n'est pas câblée : le moteur bascule alors la section en
    constat de lacune, ce qui est le comportement voulu (D4).

    Args:
        app: Application dont l'état porte les outils déjà construits.
        settings: Paramètres applicatifs.

    Returns:
        Les collecteurs indexés par le nom déclaré dans les gabarits.
    """
    from app.services.collecteurs import CollecteurOutil, CollecteurRag
    from app.services.outils.meteo import OutilMeteo
    from app.services.outils.prix import OutilPrix
    from app.services.outils.satellite import OutilSatellite

    return {
        "rag": CollecteurRag(app.state.rag),
        "meteo": CollecteurOutil(OutilMeteo.from_settings(settings), "Open-Meteo", "localite"),
        "prix": CollecteurOutil(OutilPrix.from_settings(settings), "Conseil du Café-Cacao"),
        "satellite": CollecteurOutil(
            OutilSatellite.from_settings(settings), "Global Forest Watch", "localite"
        ),
    }
```

> **Vérifie les fabriques réelles** (`OutilMeteo.from_settings`, `OutilPrix.from_settings`, `OutilSatellite.from_settings`) dans `api/app/services/outils/` et, si elles portent d'autres noms, **reprends la façon dont `application/registre.py` ou `main.py` construisent déjà ces outils** — ne les instancie pas d'une seconde manière. `parcelle` et `constats` ne sont **pas** câblés ici : ils dépendent du demandeur, et le gabarit `dossier_parcelle` les mobilisera lorsque l'atelier sera relié aux parcelles. En attendant, ces sections rendent un constat de lacune — comportement testé en Tâche 4 (`test_une_source_declaree_sans_collecteur_ne_leve_pas`). **Dis-le dans ton rapport.**

Écrire aussi `api/tests/test_collecteurs.py` :

```python
"""Tests des collecteurs — les sources existantes rendues citables."""

from __future__ import annotations

from app.services.collecteurs import CollecteurOutil, CollecteurRag
from app.services.rag import Passage


class OutilVide:
    async def invoquer(self, **kwargs: object) -> dict[str, object]:
        return {}


class OutilMeteoFactice:
    def __init__(self) -> None:
        self.arguments: list[dict] = []

    async def invoquer(self, **kwargs: object) -> dict[str, object]:
        self.arguments.append(dict(kwargs))
        return {"pluie_mm_7j": 42, "date": "2026-07-29", "vide": ""}


class FauxRag:
    def __init__(self, *passages: Passage) -> None:
        self.passages = list(passages)

    async def passages_pour(self, question: str) -> list[Passage]:
        return self.passages


async def test_une_source_indisponible_ne_rend_aucune_affirmation():
    """Dict vide = indisponible. Le moteur en fera un constat de lacune (D4)."""
    assert await CollecteurOutil(OutilVide(), "Open-Meteo", "localite").collecter("Daloa") == ()


async def test_les_donnees_deviennent_des_affirmations_sourcees():
    affirmations = await CollecteurOutil(OutilMeteoFactice(), "Open-Meteo", "localite").collecter(
        "Daloa"
    )
    assert len(affirmations) == 1
    assert affirmations[0].source == "Open-Meteo"
    assert affirmations[0].date == "2026-07-29"
    assert "42" in affirmations[0].texte


async def test_le_sujet_est_transmis_a_l_outil():
    outil = OutilMeteoFactice()
    await CollecteurOutil(outil, "Open-Meteo", "localite").collecter("Daloa")
    assert outil.arguments == [{"localite": "Daloa"}]


async def test_un_outil_sans_argument_est_invoque_a_vide():
    """Le prix officiel ne depend d aucune localite."""
    outil = OutilMeteoFactice()
    await CollecteurOutil(outil, "Conseil du Café-Cacao").collecter("Daloa")
    assert outil.arguments == [{}]


async def test_les_passages_rag_portent_leur_source():
    collecteur = CollecteurRag(FauxRag(Passage(texte="La production…", source="CNRA", score=0.8)))
    affirmations = await collecteur.collecter("production")
    assert affirmations[0].source == "CNRA"
    assert affirmations[0].methode == "rag"


async def test_un_passage_sans_source_est_ecarte():
    """Defense en profondeur : rien d insourcable n entre dans un document."""
    collecteur = CollecteurRag(FauxRag(Passage(texte="…", source="", score=0.9)))
    assert await collecteur.collecter("production") == ()


async def test_sans_rag_configure_aucune_affirmation():
    assert await CollecteurRag(None).collecter("production") == ()
```

Ajouter enfin à la ConfigMap `deploy/k8s/api.yaml` :

```yaml
  # Atelier de livrables (V3, chantier C3). OFF au premier déploiement : on vérifie
  # que le schéma SQLite se crée bien sur /data avant d'ouvrir les routes.
  RAPPORTS_ENABLED: "false"
  RAPPORTS_DB_PATH: "/data/rapports.db"
```

- [ ] **Step 7: Lancer les tests de l'API**

Run: `cd api && python -m pytest tests/test_rapports_api.py tests/test_rapports_service.py -v --no-cov`
Expected: PASS

- [ ] **Step 8: Suite complète, lint et commit**

Run: `cd api && python -m pytest -q` — couverture ≥ 99 %.

```bash
cd api && python -m ruff format app/ tests/ && python -m ruff check app/ tests/
cd .. && python -c "import yaml; list(yaml.safe_load_all(open('deploy/k8s/api.yaml', encoding='utf-8'))); print('YAML valide')"
git add api/app/services/rapports.py api/app/routers/rapports.py api/app/api_deps.py api/app/main.py api/tests/test_rapports_service.py api/tests/test_rapports_api.py deploy/k8s/api.yaml
git commit -m "feat(rapport): endpoints de l atelier et flux SSE section par section

Le synchrone est exclu : 10 a 30 generations par etude, et l edge coupe vers 100 s.
Le premier evenement part AVANT toute generation — c est ce qui evite le 524.

Un job « en cours » au demarrage est orphelin : on l assainit plutot que de laisser
un client attendre indefiniment. Routes derriere RAPPORTS_ENABLED, OFF par defaut."
```

---

## Recette de fin de chantier

Chaque ligne correspond à un critère d'acceptation de la spec §8.8.

- [ ] `cd api && python -m pytest -q` — vert, couverture ≥ 99 %
- [ ] `cd api && python -m ruff check app/ tests/` — aucune erreur
- [ ] Une étude en **Word, Excel et PPTX**, chacune contenant son **manifeste de génération**
- [ ] Une section privée de sources rend un **constat de lacune** ; **aucun chiffre sans provenance** — vérifié par un test qui échoue si une affirmation sort sans source
- [ ] Un dossier de parcelle porte la mention **« dossier préparatoire »** et ne conclut à aucune conformité
- [ ] Le flux SSE émet son **premier octet en moins d'une seconde** — vérifié par test, **à mesurer en prod** au moment de l'activation
- [ ] Un job **survit à un redémarrage** de l'API (état persisté, reprise ou échec propre)
- [ ] `security-review` lancé **par tâche**, retours traités (exigence permanente du 29/07/2026)
- [ ] Aucun test n'appelle le réseau ; **aucun dosage phytosanitaire nulle part**
- [ ] `CLAUDE_OpenCacao.md` §2.1 mis à jour avec les trois dépendances (**attente accord Waopron**)

Puis mettre à jour `docs/agents_v3.md` avec une section « L'atelier de livrables », dans le style pédagogique du document (*le concept*, *les décisions*, *le modèle mental*).

## Ce que ce chantier ne livre pas, délibérément

**La file nocturne par cron sur profil CPU** (spec §8.6) n'est pas dans ce plan. Les jobs sont exécutés à la demande, en flux ; la bascule en file nocturne avec notification par email suppose que l'atelier tourne réellement sur le nœud CPU, ce qui n'est pas la trajectoire retenue — **toute la V3 attend le passage GPU**. Le dépôt est prêt à l'accueillir (un job persisté, un état, une reprise) et l'ajout ne changera aucun contrat.

**Le rendu des figures** (graphiques) n'est pas livré : `matplotlib` n'est pas dans la spec §2.1 et n'a pas été autorisé. Les données passent par des tableaux, qui sont natifs dans les quatre formats.

**La reprise d'un job interrompu** est un échec propre, pas une reprise à la section près. Reprendre au milieu supposerait de persister chaque section rédigée ; le dépôt le permettrait, mais la valeur est faible face au coût, et un job relancé coûte quelques minutes de GPU.
