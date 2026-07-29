# Plateforme agentique V3 — Orchestrateur + framework + agents Cœur — Plan d'implémentation

> **Pour les workers agentiques :** SOUS-SKILL REQUISE : utiliser superpowers:subagent-driven-development (recommandé) ou superpowers:executing-plans pour implémenter ce plan tâche par tâche. Les étapes utilisent la syntaxe case à cocher (`- [ ]`).

**Goal :** Construire le socle agentique de la V3 — un *orchestrateur souverain* qui route chaque requête vers des *agents spécialisés* (RAG, Météo, Prix, Reporting) enregistrés dans un *registre dynamique*, en réutilisant les garde-fous, le cache et la journalisation de la V2.

**Architecture :** Clean architecture existante prolongée. Un nouveau **contrat d'agent** (`domain/agents.py`) définit l'interface que *tout* agent — les 4 du socle comme les 7+ à venir (Maladie, Satellite, Réglementation, Normes, ERP, AgroSense…) — doit respecter. Un **registre** (`application/registre.py`) collecte les agents ; un **routeur d'intention** (`application/routage.py`) choisit le(s) agent(s) pertinent(s) ; l'**orchestrateur** (`application/orchestrateur.py`) applique les garde-fous centralisés, dispatche, fusionne et journalise. Les agents concrets vivent dans `services/agents/` et appellent des **outils** (`services/outils/`) — RAG, client météo, client prix.

**Tech Stack :** Python 3.11+, FastAPI, Pydantic v2, httpx, NumPy (RAG), pytest + pytest-asyncio, ruff, structlog. Aucune nouvelle dépendance hors spec §2.1.

## Pourquoi ce plan est aussi un parcours d'apprentissage

Tu apprends l'IA agentique : chaque tâche ouvre sur un **🎓 Concept** (le pattern agentique en jeu), un **Pourquoi** (la décision de conception) et un **Pour aller plus loin**. L'ordre des tâches *est* la progression pédagogique :

1. **Le contrat d'agent** → ce qu'*est* un agent (capacité bornée + interface stable).
2. **Le registre** → composition dynamique (ajouter un agent sans toucher au reste).
3. **Le routeur** → routage d'intention (qui doit répondre ?).
4. **L'orchestrateur** → le chef d'orchestre (planifier → dispatcher → fusionner → garde-fous).
5. **Agent RAG** → transformer une capacité existante en agent.
6. **Agent Météo** → un agent qui *utilise un outil externe* (tool use).
7. **Agent Prix** → idem, données de marché.
8. **Agent Reporting** → un agent qui *compose la sortie d'autres agents* (multi-agent synthesis).
9. **Câblage API** → exposer la plateforme derrière un flag, sans casser la V2.

## Global Constraints

- **Périmètre métier : cacao UNIQUEMENT.** Les garde-fous (`services/guardrails.py`) restent l'autorité : vivrier/anacarde/médical/dosages → redirection ANADER. Les garde-fous sont appliqués **dans l'orchestrateur** (centralisés), jamais réimplémentés par agent. Décision Waopron juin 2026.
- **Aucune logique métier dans les routers.** Tout passe par `application/`.
- **Aucun service externe dans le pipeline de production** (OpenAI/Anthropic/Cohere). Les agents Météo/Prix appellent des **sources de données** (API météo/prix), pas des LLM tiers — et toujours derrière un port mockable.
- **Disclaimer ANADER systématique** sur chaque réponse modèle (déjà porté par `Conseil` + `_evenement_final`).
- Python 3.11+, `from __future__ import annotations` en tête de chaque module, typage systématique, docstrings Google.
- `ruff format` + `ruff check` doivent passer. Logging via `structlog` (`get_logger`), **jamais `print()`**.
- Couverture min. **80 %** sur `api/app/`. Inférence et appels réseau **mockés** en test (aucun appel réel en CI).
- TDD strict : test rouge → code minimal → test vert → commit. Commits fréquents, un par tâche minimum.
- Le contrat d'agent doit être **extensible à 10+ agents** : ajouter l'agent n°5..n°11 = écrire une classe qui implémente `AgentPort` + l'enregistrer. Aucune modification de l'orchestrateur, du registre ou du routeur ne doit être nécessaire pour ajouter un agent.
- **Rétrocompatibilité V2 :** la plateforme agentique est livrée **derrière un flag** (`agents_enabled`, OFF par défaut). Le chemin `ConseilService` existant reste intact tant que le flag est OFF.

---

## Structure de fichiers (vue d'ensemble)

| Fichier | Responsabilité |
|---|---|
| `api/app/domain/agents.py` | **Contrat** : `AgentRequete`, `AgentReponse`, `AgentPort` (Protocol), `Outil` (Protocol outil). Aucune dépendance framework. |
| `api/app/domain/exceptions.py` *(modif)* | Ajoute `AgentIndisponible`. |
| `api/app/application/registre.py` | `RegistreAgents` : enregistrement + lookup dynamique. |
| `api/app/application/routage.py` | `RouteurIntention` : score les agents, retourne le classement. |
| `api/app/application/orchestrateur.py` | `Orchestrateur` : garde-fous centralisés → routage → dispatch → fusion → journal. |
| `api/app/services/agents/__init__.py` | Package agents. |
| `api/app/services/agents/base.py` | `AgentBase` : mutualise l'appel inférence + post-traitement (DRY). |
| `api/app/services/agents/agent_rag.py` | `AgentRag` : conseil agronomique ancré (refonte du RAG V2 en agent). |
| `api/app/services/agents/agent_meteo.py` | `AgentMeteo` : conseil sensible au climat (utilise `OutilMeteo`). |
| `api/app/services/agents/agent_prix.py` | `AgentPrix` : marché/prix (utilise `OutilPrix`). |
| `api/app/services/agents/agent_reporting.py` | `AgentReporting` : synthèse narrative multi-agents. |
| `api/app/services/outils/__init__.py` | Package outils. |
| `api/app/services/outils/meteo.py` | `OutilMeteo` + `MeteoPort` : récupère prévisions (httpx, mockable). |
| `api/app/services/outils/prix.py` | `OutilPrix` + `PrixPort` : récupère prix/marché (httpx, mockable). |
| `api/app/core/config.py` *(modif)* | Flags `agents_enabled`, URLs outils météo/prix. |
| `api/app/api_deps.py` *(modif)* | `get_orchestrateur()` (construit registre + agents + routeur). |
| `api/app/routers/chat.py` *(modif)* | Branche l'orchestrateur quand `agents_enabled`. |
| `api/tests/agents/…` | Tests unitaires de chaque brique (registre, routeur, orchestrateur, chaque agent, chaque outil). |

**Décision de découpage :** le *contrat* (`domain/agents.py`) et l'*orchestration* (`application/`) sont purs (testables sans réseau). Les *agents* et *outils* (`services/`) sont les adaptateurs concrets. Cette frontière est ce qui rend la plateforme extensible : un nouvel agent n'est qu'un nouvel adaptateur.

---

### Task 1 : Le contrat d'agent (`domain/agents.py`)

> 🎓 **Concept — Qu'est-ce qu'un « agent » ?** En IA agentique, un agent est une **capacité bornée derrière une interface stable** : il déclare *ce qu'il sait faire* (routage), reçoit une *requête normalisée*, et rend une *réponse normalisée*. Tout le reste de la plateforme ne connaît QUE cette interface — jamais l'implémentation. C'est l'application directe de l'inversion de dépendance (les ports du domaine) au monde agentique.
>
> **Pourquoi un `Protocol` et pas une classe de base ?** Le `Protocol` (typage structurel) laisse chaque agent libre de son héritage tout en garantissant le contrat — exactement le style déjà utilisé dans `domain/ports.py`. La classe de base `AgentBase` (Task 5) sera un *confort* optionnel, pas une obligation.
>
> **Pour aller plus loin :** compare `peut_traiter()` (un score d'auto-évaluation) au pattern « tool description » des frameworks d'agents (l'agent se décrit, le routeur décide). Ici on commence **déterministe** (mots-clés) pour rester souverain et testable, puis on pourra brancher un routage par embeddings.

**Files:**
- Create: `api/app/domain/agents.py`
- Test: `api/tests/agents/test_contrat_agent.py`
- Create: `api/tests/agents/__init__.py` (package de tests vide)

**Interfaces:**
- Produces :
  - `AgentRequete(question: str, langue: Langue, historique: list[dict[str, str]], fil_ancre: str, client_ip: str)` — dataclass frozen.
  - `AgentReponse(texte: str, sources: list[str], confiance: Confiance, agent: str, redirection_anader: bool = False)` — dataclass frozen.
  - `AgentPort` (Protocol) : attributs `nom: str`, `description: str`, `mots_cles: tuple[str, ...]` ; méthodes `async peut_traiter(requete: AgentRequete) -> float` (score 0..1), `async traiter(requete: AgentRequete) -> AgentReponse`.
  - `Outil` (Protocol) : `nom: str` ; `async invoquer(**kwargs: object) -> dict[str, object]`.

- [ ] **Step 1 : Écrire le test du contrat (rouge)**

```python
# api/tests/agents/test_contrat_agent.py
"""Le contrat d'agent : structures de données et conformité au Protocol."""

from __future__ import annotations

import pytest

from app.domain.agents import AgentPort, AgentReponse, AgentRequete
from app.models.domain import Confiance, Langue


def _requete(question: str = "Comment tailler un cacaoyer ?") -> AgentRequete:
    return AgentRequete(
        question=question,
        langue=Langue.FR,
        historique=[],
        fil_ancre=question,
        client_ip="test",
    )


class _AgentFactice:
    """Agent minimal conforme au contrat (sert à valider le Protocol)."""

    nom = "factice"
    description = "agent de test"
    mots_cles = ("test",)

    async def peut_traiter(self, requete: AgentRequete) -> float:
        return 1.0 if "test" in requete.question.lower() else 0.0

    async def traiter(self, requete: AgentRequete) -> AgentReponse:
        return AgentReponse(
            texte="ok",
            sources=[],
            confiance=Confiance.MOYENNE,
            agent=self.nom,
        )


def test_agent_factice_est_conforme_au_port() -> None:
    assert isinstance(_AgentFactice(), AgentPort)


def test_requete_est_immuable() -> None:
    requete = _requete()
    with pytest.raises(Exception):
        requete.question = "autre"  # type: ignore[misc]


@pytest.mark.asyncio
async def test_peut_traiter_retourne_un_score() -> None:
    agent = _AgentFactice()
    assert await agent.peut_traiter(_requete("un test")) == 1.0
    assert await agent.peut_traiter(_requete("autre chose")) == 0.0


@pytest.mark.asyncio
async def test_traiter_retourne_une_reponse_attribuee() -> None:
    reponse = await _AgentFactice().traiter(_requete())
    assert reponse.agent == "factice"
    assert reponse.confiance is Confiance.MOYENNE
```

- [ ] **Step 2 : Lancer le test (échec attendu)**

Run : `pytest api/tests/agents/test_contrat_agent.py -v`
Expected : FAIL — `ModuleNotFoundError: No module named 'app.domain.agents'`.

- [ ] **Step 3 : Écrire le contrat (code minimal)**

```python
# api/app/domain/agents.py
"""Contrat des agents de la plateforme V3 — structures et ports purs.

Un agent est une capacité bornée derrière une interface stable : il déclare ce
qu'il sait traiter (routage) et rend une réponse normalisée. L'orchestrateur, le
registre et le routeur ne dépendent QUE de ces abstractions — jamais d'un agent
concret. C'est l'inversion de dépendance de la clean architecture appliquée à
l'agentique : ajouter un agent (n°5..n°11) = implémenter ``AgentPort`` + l'enregistrer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from app.models.domain import Confiance, Langue


@dataclass(frozen=True)
class AgentRequete:
    """Requête normalisée transmise à un agent.

    Attributes:
        question: Dernière question du producteur (déjà validée par le DTO).
        langue: Langue de la requête.
        historique: Tours précédents [{"role", "content"}], ou liste vide.
        fil_ancre: Question ancrée sur le dernier tour utilisateur (anti-dérive
            multi-tours) — sert au routage et à la récupération.
        client_ip: IP cliente (rate-limit appliqué en amont par l'orchestrateur).
    """

    question: str
    langue: Langue
    fil_ancre: str
    client_ip: str
    historique: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class AgentReponse:
    """Réponse normalisée produite par un agent.

    Attributes:
        texte: Texte de la réponse.
        sources: Sources fiables citées.
        confiance: Niveau de confiance estimé.
        agent: Nom de l'agent qui a produit la réponse (traçabilité).
        redirection_anader: Vrai si la réponse oriente vers l'ANADER.
    """

    texte: str
    sources: list[str]
    confiance: Confiance
    agent: str
    redirection_anader: bool = False


@runtime_checkable
class AgentPort(Protocol):
    """Contrat que tout agent spécialisé doit respecter.

    Attributes:
        nom: Identifiant unique de l'agent (clé de registre).
        description: Phrase décrivant la capacité (lisible, sert au routage futur).
        mots_cles: Termes déclencheurs pour le routage déterministe initial.
    """

    nom: str
    description: str
    mots_cles: tuple[str, ...]

    async def peut_traiter(self, requete: AgentRequete) -> float:
        """Score d'aptitude [0..1] : à quel point cet agent est pertinent ?"""
        ...

    async def traiter(self, requete: AgentRequete) -> AgentReponse:
        """Produit une réponse. Lève AgentIndisponible si l'agent échoue."""
        ...


@runtime_checkable
class Outil(Protocol):
    """Contrat d'un outil invocable par un agent (météo, prix, RAG…).

    Un outil est une fonction nommée à entrée/sortie sérialisables : c'est le
    « tool use » de l'agentique. Toujours mockable (aucun appel réseau en test).
    """

    nom: str

    async def invoquer(self, **kwargs: object) -> dict[str, object]:
        """Exécute l'outil et retourne un dictionnaire de résultats."""
        ...
```

- [ ] **Step 4 : Lancer le test (vert)**

Run : `pytest api/tests/agents/test_contrat_agent.py -v`
Expected : PASS (4 tests).

- [ ] **Step 5 : Lint + commit**

```bash
ruff format api/app/domain/agents.py api/tests/agents/
ruff check api/app/domain/agents.py api/tests/agents/
git add api/app/domain/agents.py api/tests/agents/
git commit -m "feat(agents): contrat d'agent V3 (AgentRequete/Reponse/Port/Outil)"
```

---

### Task 2 : Le registre dynamique (`application/registre.py`)

> 🎓 **Concept — Composition dynamique.** Un registre est un *annuaire d'agents* : on y enregistre des instances et on les retrouve par nom ou par énumération. C'est ce qui rend la plateforme **ouverte à l'extension, fermée à la modification** (principe O de SOLID) : l'agent n°7 s'ajoute par `registre.enregistrer(AgentSatellite(...))`, sans qu'aucune autre brique ne change.
>
> **Pourquoi pas un simple `dict` ?** On veut une frontière explicite : refuser un doublon de nom, exposer une énumération stable, journaliser l'enregistrement (observabilité). Le registre est aussi l'endroit naturel où, plus tard, brancher un *registre dynamique* (agents découverts par configuration).

**Files:**
- Create: `api/app/application/registre.py`
- Test: `api/tests/agents/test_registre.py`

**Interfaces:**
- Consumes : `AgentPort` (Task 1).
- Produces : `RegistreAgents` avec `enregistrer(agent: AgentPort) -> None`, `obtenir(nom: str) -> AgentPort | None`, `tous() -> list[AgentPort]`, `noms() -> list[str]`. Lève `ValueError` sur nom dupliqué.

- [ ] **Step 1 : Écrire le test (rouge)**

```python
# api/tests/agents/test_registre.py
"""Le registre : enregistrement, lookup, refus des doublons."""

from __future__ import annotations

import pytest

from app.application.registre import RegistreAgents
from app.domain.agents import AgentReponse, AgentRequete
from app.models.domain import Confiance


class _Agent:
    def __init__(self, nom: str) -> None:
        self.nom = nom
        self.description = f"agent {nom}"
        self.mots_cles = (nom,)

    async def peut_traiter(self, requete: AgentRequete) -> float:
        return 0.0

    async def traiter(self, requete: AgentRequete) -> AgentReponse:
        return AgentReponse("", [], Confiance.FAIBLE, self.nom)


def test_enregistrer_puis_obtenir() -> None:
    registre = RegistreAgents()
    agent = _Agent("rag")
    registre.enregistrer(agent)
    assert registre.obtenir("rag") is agent


def test_obtenir_inconnu_retourne_none() -> None:
    assert RegistreAgents().obtenir("absent") is None


def test_doublon_de_nom_rejete() -> None:
    registre = RegistreAgents()
    registre.enregistrer(_Agent("rag"))
    with pytest.raises(ValueError, match="déjà enregistré"):
        registre.enregistrer(_Agent("rag"))


def test_tous_et_noms() -> None:
    registre = RegistreAgents()
    registre.enregistrer(_Agent("rag"))
    registre.enregistrer(_Agent("meteo"))
    assert set(registre.noms()) == {"rag", "meteo"}
    assert len(registre.tous()) == 2
```

- [ ] **Step 2 : Lancer le test (échec attendu)**

Run : `pytest api/tests/agents/test_registre.py -v`
Expected : FAIL — `ModuleNotFoundError: No module named 'app.application.registre'`.

- [ ] **Step 3 : Écrire le registre (code minimal)**

```python
# api/app/application/registre.py
"""Registre dynamique des agents de la plateforme V3.

Annuaire ouvert à l'extension : enregistrer un agent suffit à le rendre routable.
Aucune brique d'orchestration ne change quand on ajoute l'agent n°5..n°11.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.domain.agents import AgentPort

logger = get_logger(__name__)


class RegistreAgents:
    """Collecte et expose les agents spécialisés disponibles."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentPort] = {}

    def enregistrer(self, agent: AgentPort) -> None:
        """Enregistre un agent. Lève ValueError si le nom est déjà pris."""
        if agent.nom in self._agents:
            raise ValueError(f"Agent « {agent.nom} » déjà enregistré")
        self._agents[agent.nom] = agent
        logger.info("agent_enregistre", agent=agent.nom)

    def obtenir(self, nom: str) -> AgentPort | None:
        """Retourne l'agent de nom donné, ou None s'il est inconnu."""
        return self._agents.get(nom)

    def tous(self) -> list[AgentPort]:
        """Retourne tous les agents enregistrés."""
        return list(self._agents.values())

    def noms(self) -> list[str]:
        """Retourne les noms des agents enregistrés."""
        return list(self._agents)
```

- [ ] **Step 4 : Lancer le test (vert)**

Run : `pytest api/tests/agents/test_registre.py -v`
Expected : PASS (4 tests).

- [ ] **Step 5 : Lint + commit**

```bash
ruff format api/app/application/registre.py api/tests/agents/test_registre.py
ruff check api/app/application/registre.py api/tests/agents/test_registre.py
git add api/app/application/registre.py api/tests/agents/test_registre.py
git commit -m "feat(agents): registre dynamique des agents"
```

---

### Task 3 : Le routeur d'intention (`application/routage.py`)

> 🎓 **Concept — Routage d'intention.** Le cœur de toute plateforme multi-agents : « *qui* doit répondre à cette requête ? ». On commence par un routeur **déterministe** : chaque agent s'auto-évalue (`peut_traiter`) et le routeur classe les agents par score. C'est explicable, testable, souverain (aucun appel LLM pour router). On garde la porte ouverte à un routage sémantique (embeddings) ou par LLM ultérieurement — l'interface du routeur ne changera pas.
>
> **Pourquoi un classement et pas un seul gagnant ?** Certaines requêtes mobilisent **plusieurs** agents (« quel temps pour traiter, et à quel prix vendre ? »). Le routeur renvoie une liste ordonnée ; l'orchestrateur décidera combien en activer (Task 4).
>
> **Pour aller plus loin :** ce pattern « score puis sélection » est la base du *planner* dans les architectures multi-agents (ReAct, plan-and-execute). Ici, le plan est plat (un tour) ; en V3+, l'orchestrateur pourra enchaîner des étapes.

**Files:**
- Create: `api/app/application/routage.py`
- Test: `api/tests/agents/test_routage.py`

**Interfaces:**
- Consumes : `RegistreAgents` (Task 2), `AgentRequete`, `AgentPort` (Task 1).
- Produces : `RouteurIntention(registre: RegistreAgents, seuil: float = 0.3)` avec `async classer(requete: AgentRequete) -> list[tuple[AgentPort, float]]` (trié décroissant, scores ≥ seuil) et `async meilleur(requete: AgentRequete) -> AgentPort | None`.

- [ ] **Step 1 : Écrire le test (rouge)**

```python
# api/tests/agents/test_routage.py
"""Le routeur d'intention : classement des agents par score."""

from __future__ import annotations

import pytest

from app.application.registre import RegistreAgents
from app.application.routage import RouteurIntention
from app.domain.agents import AgentReponse, AgentRequete
from app.models.domain import Confiance, Langue


class _AgentScore:
    def __init__(self, nom: str, score: float) -> None:
        self.nom = nom
        self.description = nom
        self.mots_cles = (nom,)
        self._score = score

    async def peut_traiter(self, requete: AgentRequete) -> float:
        return self._score

    async def traiter(self, requete: AgentRequete) -> AgentReponse:
        return AgentReponse("", [], Confiance.FAIBLE, self.nom)


def _requete() -> AgentRequete:
    return AgentRequete("q", Langue.FR, "q", "ip", [])


def _routeur(*scores: tuple[str, float]) -> RouteurIntention:
    registre = RegistreAgents()
    for nom, score in scores:
        registre.enregistrer(_AgentScore(nom, score))
    return RouteurIntention(registre, seuil=0.3)


@pytest.mark.asyncio
async def test_classe_par_score_decroissant() -> None:
    routeur = _routeur(("rag", 0.5), ("meteo", 0.9), ("prix", 0.1))
    classement = await routeur.classer(_requete())
    assert [a.nom for a, _ in classement] == ["meteo", "rag"]  # prix sous le seuil


@pytest.mark.asyncio
async def test_meilleur_retourne_le_plus_pertinent() -> None:
    routeur = _routeur(("rag", 0.5), ("meteo", 0.9))
    meilleur = await routeur.meilleur(_requete())
    assert meilleur is not None
    assert meilleur.nom == "meteo"


@pytest.mark.asyncio
async def test_meilleur_none_si_tous_sous_le_seuil() -> None:
    routeur = _routeur(("rag", 0.1), ("meteo", 0.0))
    assert await routeur.meilleur(_requete()) is None
```

- [ ] **Step 2 : Lancer le test (échec attendu)**

Run : `pytest api/tests/agents/test_routage.py -v`
Expected : FAIL — `ModuleNotFoundError: No module named 'app.application.routage'`.

- [ ] **Step 3 : Écrire le routeur (code minimal)**

```python
# api/app/application/routage.py
"""Routeur d'intention : choisit le(s) agent(s) pertinent(s) pour une requête.

Routage déterministe : chaque agent s'auto-évalue (``peut_traiter``) et le routeur
classe par score. Explicable, testable, souverain (aucun appel LLM). L'interface
restera stable si on bascule plus tard vers un routage sémantique.
"""

from __future__ import annotations

from app.application.registre import RegistreAgents
from app.core.logging import get_logger
from app.domain.agents import AgentPort, AgentRequete

logger = get_logger(__name__)


class RouteurIntention:
    """Classe les agents enregistrés par pertinence pour une requête."""

    def __init__(self, registre: RegistreAgents, seuil: float = 0.3) -> None:
        """Initialise le routeur.

        Args:
            registre: Registre des agents disponibles.
            seuil: Score minimal pour qu'un agent soit retenu.
        """
        self._registre = registre
        self._seuil = seuil

    async def classer(self, requete: AgentRequete) -> list[tuple[AgentPort, float]]:
        """Retourne les agents dont le score >= seuil, du plus pertinent au moins."""
        scores: list[tuple[AgentPort, float]] = []
        for agent in self._registre.tous():
            score = await agent.peut_traiter(requete)
            if score >= self._seuil:
                scores.append((agent, score))
        scores.sort(key=lambda paire: paire[1], reverse=True)
        logger.info("routage", classement=[(a.nom, round(s, 2)) for a, s in scores])
        return scores

    async def meilleur(self, requete: AgentRequete) -> AgentPort | None:
        """Retourne l'agent le plus pertinent, ou None si aucun n'atteint le seuil."""
        classement = await self.classer(requete)
        return classement[0][0] if classement else None
```

- [ ] **Step 4 : Lancer le test (vert)**

Run : `pytest api/tests/agents/test_routage.py -v`
Expected : PASS (3 tests).

- [ ] **Step 5 : Lint + commit**

```bash
ruff format api/app/application/routage.py api/tests/agents/test_routage.py
ruff check api/app/application/routage.py api/tests/agents/test_routage.py
git add api/app/application/routage.py api/tests/agents/test_routage.py
git commit -m "feat(agents): routeur d'intention déterministe (classement par score)"
```

---

### Task 4 : L'orchestrateur (`application/orchestrateur.py`)

> 🎓 **Concept — Le chef d'orchestre.** L'orchestrateur est le *cas d'usage central* de la V3, équivalent agentique de `ConseilService` : il enchaîne **garde-fous d'entrée (centralisés)** → **routage** → **dispatch vers l'agent** → **garde-fou de sortie** → **journalisation**. Les garde-fous métier ne sont PAS dans les agents : centralisés ici, ils s'appliquent identiquement à tous les agents (y compris ceux à venir). C'est la garantie de souveraineté « cacao uniquement » quel que soit l'agent sollicité.
>
> **Pourquoi réutiliser `guardrails` / `journal` / `Conseil` ?** DRY : la V3 ne réinvente pas les garde-fous éprouvés ni le schéma de sortie. L'orchestrateur produit un `Conseil` (entité existante) — donc le router et le post-traitement restent compatibles.
>
> **Repli de sécurité :** si aucun agent ne dépasse le seuil de routage, on retombe sur l'agent RAG par défaut (le conseil agronomique généraliste). Jamais d'impasse.

**Files:**
- Create: `api/app/application/orchestrateur.py`
- Modify: `api/app/domain/exceptions.py` (ajouter `AgentIndisponible`)
- Test: `api/tests/agents/test_orchestrateur.py`

**Interfaces:**
- Consumes : `RouteurIntention` (Task 3), `RegistreAgents` (Task 2), `AgentRequete`/`AgentReponse` (Task 1), `JournalPort`/`CachePort` (`domain/ports.py`), `guardrails` (`evaluer`/`verifier_reponse`/`REFUS_PHYTO`/`Refus.categorie`/`Refus.message`), `Conseil`/`Confiance`/`Langue`, `RateLimitDepasse`.
- Produces : `Orchestrateur(routeur, journal, cache, agent_defaut: str = "rag")` avec `async traiter(question, langue, client_ip, historique=None) -> Conseil`.

- [ ] **Step 1 : Ajouter l'exception (rouge via import)**

Ajouter dans `api/app/domain/exceptions.py` :

```python
class AgentIndisponible(Exception):
    """Levée quand un agent échoue à produire une réponse."""
```

- [ ] **Step 2 : Écrire le test de l'orchestrateur (rouge)**

```python
# api/tests/agents/test_orchestrateur.py
"""L'orchestrateur : garde-fous centralisés, routage, dispatch, journalisation."""

from __future__ import annotations

import pytest

from app.application.orchestrateur import Orchestrateur
from app.application.registre import RegistreAgents
from app.application.routage import RouteurIntention
from app.domain.agents import AgentReponse, AgentRequete
from app.models.domain import Confiance, Langue


class _AgentEspion:
    def __init__(self, nom: str, score: float, texte: str) -> None:
        self.nom = nom
        self.description = nom
        self.mots_cles = (nom,)
        self._score = score
        self._texte = texte
        self.recue: AgentRequete | None = None

    async def peut_traiter(self, requete: AgentRequete) -> float:
        return self._score

    async def traiter(self, requete: AgentRequete) -> AgentReponse:
        self.recue = requete
        return AgentReponse(self._texte, ["CNRA"], Confiance.ELEVEE, self.nom)


class _JournalFactice:
    def __init__(self) -> None:
        self.interactions: list[tuple] = []

    async def enregistrer_interaction(self, *args: object) -> str:
        self.interactions.append(args)
        return "id-1"

    async def enregistrer_feedback(self, interaction_id: str, vote: str) -> None: ...
    async def enregistrer_visite(self, *a: object) -> None: ...


class _CacheFactice:
    def __init__(self, limite: bool = False) -> None:
        self._limite = limite

    async def get_cached(self, q: str, lg: str) -> str | None:
        return None

    async def set_cached(self, q: str, lg: str, payload: str) -> None: ...
    async def get_semantic(self, lg, emb, th):
        return None

    async def index_semantic(self, q, lg, emb) -> None: ...
    async def hit_rate_limit(self, ip: str) -> bool:
        return self._limite

    async def ping(self) -> bool:
        return True


def _orchestrateur(*agents, journal=None, cache=None, defaut="rag") -> Orchestrateur:
    registre = RegistreAgents()
    for a in agents:
        registre.enregistrer(a)
    routeur = RouteurIntention(registre, seuil=0.3)
    return Orchestrateur(
        routeur,
        journal or _JournalFactice(),
        cache or _CacheFactice(),
        agent_defaut=defaut,
    )


@pytest.mark.asyncio
async def test_route_vers_agent_le_plus_pertinent() -> None:
    rag = _AgentEspion("rag", 0.4, "conseil RAG")
    meteo = _AgentEspion("meteo", 0.9, "conseil météo")
    orch = _orchestrateur(rag, meteo)
    conseil = await orch.traiter("quel temps pour traiter ?", Langue.FR, "ip")
    assert conseil.reponse == "conseil météo"
    assert meteo.recue is not None
    assert rag.recue is None


@pytest.mark.asyncio
async def test_repli_sur_agent_defaut_si_aucun_routage() -> None:
    rag = _AgentEspion("rag", 0.0, "conseil RAG")
    orch = _orchestrateur(rag, defaut="rag")
    conseil = await orch.traiter("question vague", Langue.FR, "ip")
    assert conseil.reponse == "conseil RAG"  # repli RAG


@pytest.mark.asyncio
async def test_garde_fou_entree_court_circuite_les_agents() -> None:
    rag = _AgentEspion("rag", 1.0, "ne devrait pas répondre")
    orch = _orchestrateur(rag)
    # Question hors filière (maïs) → refus ANADER sans appeler d'agent.
    conseil = await orch.traiter("Comment cultiver le maïs ?", Langue.FR, "ip")
    assert conseil.redirection_anader is True
    assert rag.recue is None


@pytest.mark.asyncio
async def test_journalise_l_interaction() -> None:
    journal = _JournalFactice()
    rag = _AgentEspion("rag", 1.0, "conseil")
    orch = _orchestrateur(rag, journal=journal)
    conseil = await orch.traiter("comment tailler le cacaoyer ?", Langue.FR, "ip")
    assert conseil.interaction_id == "id-1"
    assert len(journal.interactions) == 1


@pytest.mark.asyncio
async def test_rate_limit_avant_inference() -> None:
    from app.domain.exceptions import RateLimitDepasse

    rag = _AgentEspion("rag", 1.0, "conseil")
    orch = _orchestrateur(rag, cache=_CacheFactice(limite=True))
    with pytest.raises(RateLimitDepasse):
        await orch.traiter("comment tailler le cacaoyer ?", Langue.FR, "ip")
```

- [ ] **Step 3 : Lancer le test (échec attendu)**

Run : `pytest api/tests/agents/test_orchestrateur.py -v`
Expected : FAIL — `ModuleNotFoundError: No module named 'app.application.orchestrateur'`.

- [ ] **Step 4 : Écrire l'orchestrateur (code minimal)**

```python
# api/app/application/orchestrateur.py
"""Orchestrateur souverain : cas d'usage central de la plateforme agentique V3.

Enchaîne garde-fous d'entrée (centralisés) → routage d'intention → dispatch vers
l'agent retenu → garde-fou de sortie → journalisation. Les garde-fous métier ne
sont jamais réimplémentés par agent : centralisés ici, ils s'appliquent à tous les
agents — actuels comme à venir (Maladie, Satellite, Réglementation…).
"""

from __future__ import annotations

from dataclasses import replace

from app.application.routage import RouteurIntention
from app.core.logging import get_logger
from app.domain.agents import AgentRequete
from app.domain.entities import Conseil
from app.domain.exceptions import RateLimitDepasse
from app.domain.ports import CachePort, JournalPort
from app.models.domain import Confiance, Langue
from app.services import guardrails

logger = get_logger(__name__)


class Orchestrateur:
    """Pilote le traitement d'une requête par les agents spécialisés."""

    def __init__(
        self,
        routeur: RouteurIntention,
        journal: JournalPort,
        cache: CachePort,
        agent_defaut: str = "rag",
    ) -> None:
        """Initialise l'orchestrateur.

        Args:
            routeur: Routeur d'intention (classe les agents).
            journal: Port de journalisation des interactions.
            cache: Port de cache/rate-limit (rate-limit avant inférence réelle).
            agent_defaut: Nom de l'agent de repli si aucun routage n'aboutit.
        """
        self._routeur = routeur
        self._journal = journal
        self._cache = cache
        self._agent_defaut = agent_defaut

    async def traiter(
        self,
        question: str,
        langue: Langue,
        client_ip: str,
        historique: list[dict[str, str]] | None = None,
    ) -> Conseil:
        """Produit un conseil en routant la requête vers l'agent pertinent.

        Raises:
            RateLimitDepasse: Si le quota par IP est dépassé.
            AgentIndisponible: Si l'agent retenu échoue (propagée).
        """
        historique = historique or []
        fil = _fil_ancre(question, historique)

        # 1. Garde-fous d'entrée CENTRALISÉS : refus sans solliciter d'agent.
        refus = guardrails.evaluer(fil)
        if refus is not None:
            logger.info("garde_fou_declenche", categorie=refus.categorie.value)
            conseil = Conseil(refus.message, Confiance.ELEVEE, [], redirection_anader=True)
            return await self._journaliser(question, langue, conseil)

        requete = AgentRequete(
            question=question,
            langue=langue,
            fil_ancre=fil,
            client_ip=client_ip,
            historique=historique,
        )

        # 2. Routage d'intention → agent (repli sur l'agent par défaut).
        agent = await self._routeur.meilleur(requete)
        if agent is None:
            agent = self._routeur_defaut()
        logger.info("dispatch", agent=agent.nom if agent else None)
        if agent is None:
            conseil = Conseil(
                "Service momentanément indisponible.", Confiance.FAIBLE, [], redirection_anader=True
            )
            return await self._journaliser(question, langue, conseil)

        # 3. Rate-limit UNIQUEMENT avant l'inférence réelle (équité : refus gratuits).
        if await self._cache.hit_rate_limit(client_ip):
            raise RateLimitDepasse

        # 4. Dispatch vers l'agent.
        reponse = await agent.traiter(requete)

        # 5. Garde-fou de SORTIE (défense en profondeur).
        if guardrails.verifier_reponse(reponse.texte) is not None:
            logger.warning("garde_fou_sortie_declenche", agent=agent.nom)
            conseil = Conseil(guardrails.REFUS_PHYTO, Confiance.ELEVEE, [], redirection_anader=True)
            return await self._journaliser(question, langue, conseil)

        conseil = Conseil(
            reponse=reponse.texte,
            confiance=reponse.confiance,
            sources=reponse.sources,
            redirection_anader=reponse.redirection_anader,
        )
        return await self._journaliser(question, langue, conseil)

    def _routeur_defaut(self):
        """Retourne l'agent de repli (RAG par défaut), ou None s'il est absent."""
        return self._routeur._registre.obtenir(self._agent_defaut)  # noqa: SLF001

    async def _journaliser(self, question: str, langue: Langue, conseil: Conseil) -> Conseil:
        """Journalise l'interaction et renvoie le conseil enrichi de son id."""
        interaction_id = await self._journal.enregistrer_interaction(
            question,
            langue.value,
            conseil.reponse,
            conseil.confiance.value,
            conseil.sources,
            conseil.redirection_anader,
        )
        return replace(conseil, interaction_id=interaction_id)


def _fil_ancre(question: str, historique: list[dict[str, str]]) -> str:
    """Ancre la question sur le dernier tour utilisateur (anti-dérive multi-tours)."""
    dernier_user = next(
        (t.get("content", "") for t in reversed(historique) if t.get("role") == "user"),
        "",
    )
    return f"{dernier_user} {question}".strip() if dernier_user else question
```

> **Note de refactor (DRY) :** `_fil_ancre` est dupliqué depuis `conseil_service.py`. Étape d'amélioration prévue en Task 9 : extraire `_fil_ancre` et `_texte_conversation` dans un module partagé `application/contexte.py` importé par les deux. On accepte la duplication temporaire ici pour garder chaque tâche testable isolément.

- [ ] **Step 5 : Lancer le test (vert)**

Run : `pytest api/tests/agents/test_orchestrateur.py -v`
Expected : PASS (5 tests). Si `test_garde_fou_entree_court_circuite_les_agents` échoue, vérifier que `guardrails.evaluer` redirige bien le maïs (règle hors-filière déjà en place).

- [ ] **Step 6 : Lint + commit**

```bash
ruff format api/app/application/orchestrateur.py api/app/domain/exceptions.py api/tests/agents/test_orchestrateur.py
ruff check api/app/application/orchestrateur.py api/app/domain/exceptions.py api/tests/agents/test_orchestrateur.py
git add api/app/application/orchestrateur.py api/app/domain/exceptions.py api/tests/agents/test_orchestrateur.py
git commit -m "feat(agents): orchestrateur souverain (garde-fous centralisés + dispatch)"
```

---

### Task 5 : Classe de base + Agent RAG (`services/agents/base.py`, `agent_rag.py`)

> 🎓 **Concept — Transformer une capacité existante en agent.** Le conseil agronomique ancré (RAG) existe déjà (`services/rag.py` + `inference.py`). L'« agentifier » = l'envelopper dans le contrat `AgentPort`. On en profite pour créer `AgentBase`, qui mutualise l'appel inférence + post-traitement (DRY) : les agents Météo/Prix/Reporting en hériteront. C'est le pattern « squelette d'agent » : la logique commune (appeler le LLM, extraire les sources, estimer la confiance) est factorisée ; chaque agent ne définit que sa *spécificité* (quel contexte injecter, comment scorer son aptitude).
>
> **Pourquoi le RAG est l'agent par défaut ?** C'est le conseil généraliste : quand le routeur ne sait pas trancher, répondre par l'agronomie ancrée sur sources officielles est toujours le bon repli.

**Files:**
- Create: `api/app/services/agents/__init__.py`
- Create: `api/app/services/agents/base.py`
- Create: `api/app/services/agents/agent_rag.py`
- Test: `api/tests/agents/test_agent_rag.py`

**Interfaces:**
- Consumes : `InferencePort` (`generer(question, contexte=, historique=) -> str`), `RagRecuperateur` (`contexte_pour(question) -> str | None`), `postprocess.extraire_sources`/`estimer_confiance`, `AgentRequete`/`AgentReponse`.
- Produces :
  - `AgentBase` : méthode protégée `async _generer(requete, contexte) -> AgentReponse` (appelle l'inférence, extrait sources, estime confiance, attribue `agent=self.nom`).
  - `AgentRag(inference, rag=None)` : `nom = "rag"`, `peut_traiter -> float`, `traiter -> AgentReponse`.

- [ ] **Step 1 : Écrire le test (rouge)**

```python
# api/tests/agents/test_agent_rag.py
"""Agent RAG : conseil agronomique ancré, sert aussi d'agent par défaut."""

from __future__ import annotations

import pytest

from app.domain.agents import AgentRequete
from app.models.domain import Langue
from app.services.agents.agent_rag import AgentRag


class _InferenceFactice:
    def __init__(self, texte: str) -> None:
        self._texte = texte
        self.contexte_recu: str | None = None

    async def generer(self, question, *, contexte=None, historique=None, **kw) -> str:
        self.contexte_recu = contexte
        return self._texte

    def generer_stream(self, *a, **k): ...
    async def ready(self) -> bool:
        return True


class _RagFactice:
    def __init__(self, contexte: str | None) -> None:
        self._contexte = contexte

    async def contexte_pour(self, question: str) -> str | None:
        return self._contexte


def _requete(q: str = "comment tailler le cacaoyer ?") -> AgentRequete:
    return AgentRequete(q, Langue.FR, q, "ip", [])


@pytest.mark.asyncio
async def test_agent_rag_repond_avec_contexte() -> None:
    inf = _InferenceFactice("Taillez en saison sèche (source : CNRA).")
    agent = AgentRag(inf, rag=_RagFactice("Extrait CNRA sur la taille."))
    reponse = await agent.traiter(_requete())
    assert reponse.agent == "rag"
    assert "CNRA" in reponse.sources
    assert inf.contexte_recu == "Extrait CNRA sur la taille."


@pytest.mark.asyncio
async def test_agent_rag_sans_rag_fonctionne() -> None:
    agent = AgentRag(_InferenceFactice("Conseil générique."), rag=None)
    reponse = await agent.traiter(_requete())
    assert reponse.texte == "Conseil générique."


@pytest.mark.asyncio
async def test_peut_traiter_score_eleve_par_defaut() -> None:
    # Agent généraliste : score non nul sur toute question cacao (sert de repli).
    agent = AgentRag(_InferenceFactice("x"), rag=None)
    assert await agent.peut_traiter(_requete()) >= 0.3
```

- [ ] **Step 2 : Lancer le test (échec attendu)**

Run : `pytest api/tests/agents/test_agent_rag.py -v`
Expected : FAIL — `ModuleNotFoundError: No module named 'app.services.agents'`.

- [ ] **Step 3 : Écrire `__init__.py`, `base.py`, `agent_rag.py`**

```python
# api/app/services/agents/__init__.py
"""Agents spécialisés concrets de la plateforme V3."""
```

```python
# api/app/services/agents/base.py
"""Squelette commun aux agents : appel inférence + post-traitement (DRY).

Chaque agent concret ne définit que sa spécificité (contexte à injecter, score
d'aptitude). La mécanique « appeler le LLM → extraire les sources → estimer la
confiance → attribuer la réponse » est mutualisée ici.
"""

from __future__ import annotations

from app.domain.agents import AgentReponse, AgentRequete
from app.domain.ports import InferencePort
from app.services import postprocess


class AgentBase:
    """Base optionnelle : génère une réponse ancrée via le port d'inférence."""

    nom: str = "base"
    description: str = ""
    mots_cles: tuple[str, ...] = ()

    def __init__(self, inference: InferencePort) -> None:
        """Initialise l'agent avec son port d'inférence."""
        self._inference = inference

    async def _generer(self, requete: AgentRequete, contexte: str | None) -> AgentReponse:
        """Appelle l'inférence avec un contexte donné et post-traite la sortie."""
        texte = await self._inference.generer(
            requete.question, contexte=contexte, historique=requete.historique
        )
        sources = postprocess.extraire_sources(texte)
        return AgentReponse(
            texte=texte,
            sources=sources,
            confiance=postprocess.estimer_confiance(sources),
            agent=self.nom,
        )
```

```python
# api/app/services/agents/agent_rag.py
"""Agent RAG : conseil agronomique ancré sur sources officielles.

Refonte du RAG V2 en agent. Sert aussi d'agent par défaut (repli généraliste)
quand le routeur ne sait pas trancher.
"""

from __future__ import annotations

from app.domain.agents import AgentReponse, AgentRequete
from app.domain.ports import InferencePort
from app.services.agents.base import AgentBase
from app.services.rag import RagRecuperateur


class AgentRag(AgentBase):
    """Conseil agronomique cacao ancré sur CNRA/ANADER/CCC/FIRCA."""

    nom = "rag"
    description = "Conseil agronomique cacao ancré sur les sources officielles."
    mots_cles = ()  # agent généraliste : pas de déclencheur spécifique

    def __init__(self, inference: InferencePort, rag: RagRecuperateur | None = None) -> None:
        """Initialise l'agent RAG.

        Args:
            inference: Port d'inférence.
            rag: Récupérateur de contexte documentaire, ou None (sans contexte).
        """
        super().__init__(inference)
        self._rag = rag

    async def peut_traiter(self, requete: AgentRequete) -> float:
        """Score de repli : l'agronomie générale couvre toute question cacao."""
        return 0.4

    async def traiter(self, requete: AgentRequete) -> AgentReponse:
        """Récupère le contexte RAG (best-effort) puis génère une réponse ancrée."""
        contexte = await self._rag.contexte_pour(requete.fil_ancre) if self._rag else None
        return await self._generer(requete, contexte)
```

- [ ] **Step 4 : Lancer le test (vert)**

Run : `pytest api/tests/agents/test_agent_rag.py -v`
Expected : PASS (3 tests).

- [ ] **Step 5 : Lint + commit**

```bash
ruff format api/app/services/agents/ api/tests/agents/test_agent_rag.py
ruff check api/app/services/agents/ api/tests/agents/test_agent_rag.py
git add api/app/services/agents/ api/tests/agents/test_agent_rag.py
git commit -m "feat(agents): AgentBase + AgentRag (RAG V2 agentifié, agent par défaut)"
```

---

### Task 6 : Outil Météo + Agent Météo (`services/outils/meteo.py`, `services/agents/agent_meteo.py`)

> 🎓 **Concept — Tool use (agent qui appelle un outil externe).** C'est *le* pattern fondateur de l'agentique : un agent ne se contente pas de « parler », il **agit** en appelant des outils qui ramènent des données fraîches. L'agent Météo récupère des prévisions via `OutilMeteo` (un port `MeteoPort` mockable, httpx en prod), puis **injecte ces données comme contexte** dans le prompt LLM — le modèle raisonne alors sur des faits, pas sur sa mémoire. Séparer l'*outil* (récupérer la donnée) de l'*agent* (raisonner dessus) est essentiel : on teste chacun isolément, et l'outil est réutilisable par d'autres agents.
>
> **Pourquoi un `MeteoPort` ?** Même raison que les autres ports : aucun appel réseau réel en test, et la source météo (Open-Meteo, API nationale…) est interchangeable. **Aucun LLM tiers** — seulement une source de données factuelles, conforme à la contrainte de souveraineté.
>
> **Routage :** `peut_traiter` retourne un score élevé quand la question contient des mots-clés météo (pluie, traitement, fenêtre, sécher, récolte, saison…). C'est le routage déterministe en action.

**Files:**
- Create: `api/app/services/outils/__init__.py`
- Create: `api/app/services/outils/meteo.py`
- Create: `api/app/services/agents/agent_meteo.py`
- Test: `api/tests/agents/test_agent_meteo.py`

**Interfaces:**
- Consumes : `InferencePort`, `AgentRequete`/`AgentReponse`, `Outil`.
- Produces :
  - `MeteoPort` (Protocol) : `async previsions(localite: str) -> dict[str, object]`.
  - `OutilMeteo(meteo: MeteoPort)` : `nom = "meteo"`, `async invoquer(localite: str) -> dict`.
  - `AgentMeteo(inference, outil: OutilMeteo, geo_defaut: str = "Côte d'Ivoire")` : `nom = "meteo"`, mots-clés météo, `peut_traiter`, `traiter`.

- [ ] **Step 1 : Écrire le test (rouge)**

```python
# api/tests/agents/test_agent_meteo.py
"""Agent Météo : tool use (récupère des prévisions, raisonne dessus)."""

from __future__ import annotations

import pytest

from app.domain.agents import AgentRequete
from app.models.domain import Langue
from app.services.agents.agent_meteo import AgentMeteo
from app.services.outils.meteo import OutilMeteo


class _InferenceFactice:
    def __init__(self) -> None:
        self.contexte_recu: str | None = None

    async def generer(self, question, *, contexte=None, historique=None, **kw) -> str:
        self.contexte_recu = contexte
        return "Évitez de traiter avant la pluie prévue demain."

    def generer_stream(self, *a, **k): ...
    async def ready(self) -> bool:
        return True


class _MeteoFactice:
    async def previsions(self, localite: str) -> dict:
        return {"localite": localite, "pluie_mm_24h": 12, "resume": "pluie demain"}


def _requete(q: str) -> AgentRequete:
    return AgentRequete(q, Langue.FR, q, "ip", [])


@pytest.mark.asyncio
async def test_peut_traiter_eleve_sur_question_meteo() -> None:
    agent = AgentMeteo(_InferenceFactice(), OutilMeteo(_MeteoFactice()))
    assert await agent.peut_traiter(_requete("dois-je traiter avant la pluie ?")) >= 0.7
    assert await agent.peut_traiter(_requete("quel prix du cacao ?")) < 0.3


@pytest.mark.asyncio
async def test_traiter_injecte_les_previsions_dans_le_contexte() -> None:
    inf = _InferenceFactice()
    agent = AgentMeteo(inf, OutilMeteo(_MeteoFactice()))
    reponse = await agent.traiter(_requete("quand traiter à Daloa ?"))
    assert reponse.agent == "meteo"
    assert inf.contexte_recu is not None
    assert "pluie" in inf.contexte_recu.lower()
```

- [ ] **Step 2 : Lancer le test (échec attendu)**

Run : `pytest api/tests/agents/test_agent_meteo.py -v`
Expected : FAIL — `ModuleNotFoundError: No module named 'app.services.outils'`.

- [ ] **Step 3 : Écrire l'outil et l'agent**

```python
# api/app/services/outils/__init__.py
"""Outils invocables par les agents (sources de données factuelles)."""
```

```python
# api/app/services/outils/meteo.py
"""Outil Météo : récupère des prévisions pour une localité.

L'outil isole l'accès à la source météo (port mockable). Aucun LLM tiers : une
source de données factuelles uniquement (souveraineté). En production, brancher un
``MeteoPort`` httpx vers une API météo ; en test, un double factice.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.core.logging import get_logger

logger = get_logger(__name__)


@runtime_checkable
class MeteoPort(Protocol):
    """Contrat d'une source de prévisions météo."""

    async def previsions(self, localite: str) -> dict[str, object]:
        """Retourne les prévisions pour une localité (résumé + indicateurs)."""
        ...


class OutilMeteo:
    """Outil agent : enveloppe une source météo derrière le contrat Outil."""

    nom = "meteo"

    def __init__(self, meteo: MeteoPort) -> None:
        """Initialise l'outil avec sa source de prévisions."""
        self._meteo = meteo

    async def invoquer(self, **kwargs: object) -> dict[str, object]:
        """Récupère les prévisions pour la localité passée en argument."""
        localite = str(kwargs.get("localite", ""))
        try:
            return await self._meteo.previsions(localite)
        except Exception:  # noqa: BLE001 — best-effort, l'agent dégrade proprement
            logger.warning("outil_meteo_echec", localite=localite)
            return {}
```

```python
# api/app/services/agents/agent_meteo.py
"""Agent Météo : conseil sensible au climat (fenêtres de traitement/récolte).

Tool use : récupère des prévisions via OutilMeteo puis les injecte comme contexte
factuel dans le prompt. Le modèle raisonne sur des données fraîches, pas sa mémoire.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.domain.agents import AgentReponse, AgentRequete
from app.domain.ports import InferencePort
from app.services.agents.base import AgentBase
from app.services.outils.meteo import OutilMeteo

logger = get_logger(__name__)

_MOTS_METEO = (
    "pluie",
    "pleuvoir",
    "meteo",
    "météo",
    "temps",
    "traiter",
    "traitement",
    "secher",
    "sécher",
    "sechage",
    "séchage",
    "recolte",
    "récolte",
    "saison",
    "fenetre",
    "fenêtre",
    "humidite",
    "humidité",
)


class AgentMeteo(AgentBase):
    """Conseil agronomique tenant compte des prévisions météo locales."""

    nom = "meteo"
    description = "Conseil sensible au climat : fenêtres de traitement et de récolte."
    mots_cles = _MOTS_METEO

    def __init__(
        self,
        inference: InferencePort,
        outil: OutilMeteo,
        geo_defaut: str = "Côte d'Ivoire",
    ) -> None:
        """Initialise l'agent Météo.

        Args:
            inference: Port d'inférence.
            outil: Outil de récupération des prévisions.
            geo_defaut: Localité par défaut si aucune n'est détectée.
        """
        super().__init__(inference)
        self._outil = outil
        self._geo_defaut = geo_defaut

    async def peut_traiter(self, requete: AgentRequete) -> float:
        """Score élevé si la question évoque la météo ou une fenêtre d'action."""
        texte = requete.fil_ancre.lower()
        touches = sum(1 for mot in self.mots_cles if mot in texte)
        if touches == 0:
            return 0.0
        return min(0.7 + 0.1 * touches, 1.0)

    async def traiter(self, requete: AgentRequete) -> AgentReponse:
        """Récupère les prévisions et génère un conseil sensible au climat."""
        localite = _detecter_localite(requete.fil_ancre) or self._geo_defaut
        previsions = await self._outil.invoquer(localite=localite)
        contexte = _formater_previsions(localite, previsions)
        return await self._generer(requete, contexte)


def _detecter_localite(texte: str) -> str | None:
    """Détection minimale de localité (préfixe « à <Ville> »). Heuristique simple.

    Note : une détection robuste réutilisera l'annuaire ``services/contacts.py``
    (60 zones connues). Pour le socle, on reste minimal et testable.
    """
    import re

    match = re.search(r"\bà\s+([A-ZÉÈÀ][\wÀ-ÿ-]+)", texte)
    return match.group(1) if match else None


def _formater_previsions(localite: str, previsions: dict[str, object]) -> str | None:
    """Met en forme les prévisions en contexte injectable, ou None si vide."""
    if not previsions:
        return None
    resume = previsions.get("resume", "")
    pluie = previsions.get("pluie_mm_24h", "?")
    return f"Prévisions météo pour {localite} : {resume} (pluie 24h : {pluie} mm)."
```

- [ ] **Step 4 : Lancer le test (vert)**

Run : `pytest api/tests/agents/test_agent_meteo.py -v`
Expected : PASS (2 tests).

- [ ] **Step 5 : Lint + commit**

```bash
ruff format api/app/services/outils/ api/app/services/agents/agent_meteo.py api/tests/agents/test_agent_meteo.py
ruff check api/app/services/outils/ api/app/services/agents/agent_meteo.py api/tests/agents/test_agent_meteo.py
git add api/app/services/outils/ api/app/services/agents/agent_meteo.py api/tests/agents/test_agent_meteo.py
git commit -m "feat(agents): OutilMeteo + AgentMeteo (tool use, contexte météo injecté)"
```

---

### Task 7 : Outil Prix + Agent Prix (`services/outils/prix.py`, `services/agents/agent_prix.py`)

> 🎓 **Concept — Réutiliser le pattern tool use.** L'agent Prix est le jumeau de l'agent Météo : même structure (outil + port mockable + injection de contexte), domaine différent (prix/marché/change cacao). L'écrire après Météo montre que **le pattern se réplique** — c'est exactement ce qui permettra d'ajouter les agents n°5..n°11 en suivant le même moule. Quand tu écris celui-ci, remarque combien tu réutilises la mécanique déjà acquise : c'est le signe que le framework tient.

**Files:**
- Create: `api/app/services/outils/prix.py`
- Create: `api/app/services/agents/agent_prix.py`
- Test: `api/tests/agents/test_agent_prix.py`

**Interfaces:**
- Consumes : `InferencePort`, `AgentRequete`/`AgentReponse`.
- Produces :
  - `PrixPort` (Protocol) : `async cours() -> dict[str, object]`.
  - `OutilPrix(prix: PrixPort)` : `nom = "prix"`, `async invoquer() -> dict`.
  - `AgentPrix(inference, outil: OutilPrix)` : `nom = "prix"`, mots-clés prix, `peut_traiter`, `traiter`.

- [ ] **Step 1 : Écrire le test (rouge)**

```python
# api/tests/agents/test_agent_prix.py
"""Agent Prix : tool use sur les données de marché du cacao."""

from __future__ import annotations

import pytest

from app.domain.agents import AgentRequete
from app.models.domain import Langue
from app.services.agents.agent_prix import AgentPrix
from app.services.outils.prix import OutilPrix


class _InferenceFactice:
    def __init__(self) -> None:
        self.contexte_recu: str | None = None

    async def generer(self, question, *, contexte=None, historique=None, **kw) -> str:
        self.contexte_recu = contexte
        return "Le prix bord-champ garanti est de 1800 FCFA/kg."

    def generer_stream(self, *a, **k): ...
    async def ready(self) -> bool:
        return True


class _PrixFactice:
    async def cours(self) -> dict:
        return {"prix_bord_champ_fcfa_kg": 1800, "campagne": "2025-2026"}


def _requete(q: str) -> AgentRequete:
    return AgentRequete(q, Langue.FR, q, "ip", [])


@pytest.mark.asyncio
async def test_peut_traiter_eleve_sur_question_prix() -> None:
    agent = AgentPrix(_InferenceFactice(), OutilPrix(_PrixFactice()))
    assert await agent.peut_traiter(_requete("à combien se vend le cacao ?")) >= 0.7
    assert await agent.peut_traiter(_requete("comment tailler le cacaoyer ?")) < 0.3


@pytest.mark.asyncio
async def test_traiter_injecte_le_cours_dans_le_contexte() -> None:
    inf = _InferenceFactice()
    agent = AgentPrix(inf, OutilPrix(_PrixFactice()))
    reponse = await agent.traiter(_requete("quel est le prix du cacao ?"))
    assert reponse.agent == "prix"
    assert inf.contexte_recu is not None
    assert "1800" in inf.contexte_recu
```

- [ ] **Step 2 : Lancer le test (échec attendu)**

Run : `pytest api/tests/agents/test_agent_prix.py -v`
Expected : FAIL — `ModuleNotFoundError: No module named 'app.services.outils.prix'`.

- [ ] **Step 3 : Écrire l'outil et l'agent**

```python
# api/app/services/outils/prix.py
"""Outil Prix : récupère le cours/prix de référence du cacao.

Source de données factuelle (CCC, marché), jamais un LLM tiers. Port mockable.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.core.logging import get_logger

logger = get_logger(__name__)


@runtime_checkable
class PrixPort(Protocol):
    """Contrat d'une source de prix/marché du cacao."""

    async def cours(self) -> dict[str, object]:
        """Retourne le cours courant (prix bord-champ, campagne, change…)."""
        ...


class OutilPrix:
    """Outil agent : enveloppe une source de prix derrière le contrat Outil."""

    nom = "prix"

    def __init__(self, prix: PrixPort) -> None:
        """Initialise l'outil avec sa source de prix."""
        self._prix = prix

    async def invoquer(self, **kwargs: object) -> dict[str, object]:
        """Récupère le cours courant (best-effort : {} si la source échoue)."""
        try:
            return await self._prix.cours()
        except Exception:  # noqa: BLE001
            logger.warning("outil_prix_echec")
            return {}
```

```python
# api/app/services/agents/agent_prix.py
"""Agent Prix : aide à la commercialisation (prix/marché/change du cacao).

Tool use : récupère le cours via OutilPrix et l'injecte comme contexte factuel.
"""

from __future__ import annotations

from app.domain.agents import AgentReponse, AgentRequete
from app.domain.ports import InferencePort
from app.services.agents.base import AgentBase
from app.services.outils.prix import OutilPrix

_MOTS_PRIX = (
    "prix",
    "vendre",
    "vente",
    "marche",
    "marché",
    "fcfa",
    "cours",
    "kilo",
    "kg",
    "bord-champ",
    "bord champ",
    "campagne",
    "acheteur",
    "commercialisation",
)


class AgentPrix(AgentBase):
    """Synthèses et alertes d'aide à la commercialisation du cacao."""

    nom = "prix"
    description = "Prix/marché/change du cacao : aide à la commercialisation."
    mots_cles = _MOTS_PRIX

    def __init__(self, inference: InferencePort, outil: OutilPrix) -> None:
        """Initialise l'agent Prix.

        Args:
            inference: Port d'inférence.
            outil: Outil de récupération du cours.
        """
        super().__init__(inference)
        self._outil = outil

    async def peut_traiter(self, requete: AgentRequete) -> float:
        """Score élevé si la question évoque le prix, la vente ou le marché."""
        texte = requete.fil_ancre.lower()
        touches = sum(1 for mot in self.mots_cles if mot in texte)
        if touches == 0:
            return 0.0
        return min(0.7 + 0.1 * touches, 1.0)

    async def traiter(self, requete: AgentRequete) -> AgentReponse:
        """Récupère le cours et génère une synthèse de commercialisation."""
        cours = await self._outil.invoquer()
        contexte = _formater_cours(cours)
        return await self._generer(requete, contexte)


def _formater_cours(cours: dict[str, object]) -> str | None:
    """Met en forme le cours en contexte injectable, ou None si vide."""
    if not cours:
        return None
    prix = cours.get("prix_bord_champ_fcfa_kg", "?")
    campagne = cours.get("campagne", "")
    return f"Prix bord-champ de référence : {prix} FCFA/kg (campagne {campagne})."
```

- [ ] **Step 4 : Lancer le test (vert)**

Run : `pytest api/tests/agents/test_agent_prix.py -v`
Expected : PASS (2 tests).

- [ ] **Step 5 : Lint + commit**

```bash
ruff format api/app/services/outils/prix.py api/app/services/agents/agent_prix.py api/tests/agents/test_agent_prix.py
ruff check api/app/services/outils/prix.py api/app/services/agents/agent_prix.py api/tests/agents/test_agent_prix.py
git add api/app/services/outils/prix.py api/app/services/agents/agent_prix.py api/tests/agents/test_agent_prix.py
git commit -m "feat(agents): OutilPrix + AgentPrix (tool use, contexte marché injecté)"
```

---

### Task 8 : Agent Reporting (`services/agents/agent_reporting.py`)

> 🎓 **Concept — Synthèse multi-agents.** Jusqu'ici, un seul agent répond. L'agent Reporting est différent : il **compose la sortie de plusieurs agents** en une synthèse narrative. C'est le premier pas vers l'orchestration *multi-agents* (plusieurs agents contribuent à une réponse), par opposition au simple routage *vers un* agent. Il reçoit les réponses déjà produites par d'autres agents (RAG + Météo + Prix) et demande au LLM d'en faire un rapport décisionnel cohérent.
>
> **Pourquoi le construire en dernier ?** Parce qu'il *dépend* des autres : il illustre que, une fois le framework en place, un agent peut lui-même consommer le travail d'agents pairs. C'est le germe des architectures « agent superviseur ».
>
> **Pour aller plus loin :** compare ce pattern à l'orchestration « fan-out / fan-in » (lancer N agents en parallèle, fusionner). Ici on garde une fusion séquentielle simple ; la généralisation (exécution parallèle pilotée par l'orchestrateur) est une évolution V3+ explicitement hors socle.

**Files:**
- Create: `api/app/services/agents/agent_reporting.py`
- Test: `api/tests/agents/test_agent_reporting.py`

**Interfaces:**
- Consumes : `InferencePort`, `AgentReponse`/`AgentRequete`.
- Produces : `AgentReporting(inference)` avec `nom = "reporting"`, `mots_cles` (rapport, synthèse, bilan…), `peut_traiter`, `traiter`, et `async synthetiser(requete, contributions: list[AgentReponse]) -> AgentReponse`.

- [ ] **Step 1 : Écrire le test (rouge)**

```python
# api/tests/agents/test_agent_reporting.py
"""Agent Reporting : synthèse narrative de contributions multi-agents."""

from __future__ import annotations

import pytest

from app.domain.agents import AgentReponse, AgentRequete
from app.models.domain import Confiance, Langue
from app.services.agents.agent_reporting import AgentReporting


class _InferenceFactice:
    def __init__(self) -> None:
        self.contexte_recu: str | None = None

    async def generer(self, question, *, contexte=None, historique=None, **kw) -> str:
        self.contexte_recu = contexte
        return "Synthèse : conditions favorables et prix porteur."

    def generer_stream(self, *a, **k): ...
    async def ready(self) -> bool:
        return True


def _requete(q: str = "fais-moi un bilan") -> AgentRequete:
    return AgentRequete(q, Langue.FR, q, "ip", [])


@pytest.mark.asyncio
async def test_peut_traiter_eleve_sur_demande_de_rapport() -> None:
    agent = AgentReporting(_InferenceFactice())
    assert await agent.peut_traiter(_requete("fais-moi une synthèse")) >= 0.7
    assert await agent.peut_traiter(_requete("comment tailler ?")) < 0.3


@pytest.mark.asyncio
async def test_synthetiser_fusionne_les_contributions() -> None:
    inf = _InferenceFactice()
    agent = AgentReporting(inf)
    contributions = [
        AgentReponse("Pluie demain.", ["meteo"], Confiance.MOYENNE, "meteo"),
        AgentReponse("Prix 1800 FCFA/kg.", ["CCC"], Confiance.ELEVEE, "prix"),
    ]
    reponse = await agent.synthetiser(_requete(), contributions)
    assert reponse.agent == "reporting"
    # Les contributions sont passées au LLM comme contexte de synthèse.
    assert "Pluie demain." in (inf.contexte_recu or "")
    assert "1800" in (inf.contexte_recu or "")
    # Les sources des contributions sont agrégées.
    assert "meteo" in reponse.sources and "CCC" in reponse.sources
```

- [ ] **Step 2 : Lancer le test (échec attendu)**

Run : `pytest api/tests/agents/test_agent_reporting.py -v`
Expected : FAIL — `ModuleNotFoundError`.

- [ ] **Step 3 : Écrire l'agent Reporting**

```python
# api/app/services/agents/agent_reporting.py
"""Agent Reporting : synthèse narrative de contributions multi-agents.

Premier pas vers l'orchestration multi-agents : compose la sortie de plusieurs
agents (RAG, Météo, Prix) en un rapport décisionnel cohérent.
"""

from __future__ import annotations

from app.domain.agents import AgentReponse, AgentRequete
from app.domain.ports import InferencePort
from app.models.domain import Confiance
from app.services.agents.base import AgentBase

_MOTS_REPORTING = (
    "rapport",
    "synthese",
    "synthèse",
    "bilan",
    "resume",
    "résumé",
    "tableau de bord",
    "recapitulatif",
    "récapitulatif",
    "point complet",
)


class AgentReporting(AgentBase):
    """Compose une synthèse narrative à partir d'autres réponses d'agents."""

    nom = "reporting"
    description = "Rapports et synthèses narratives multi-agents."
    mots_cles = _MOTS_REPORTING

    def __init__(self, inference: InferencePort) -> None:
        """Initialise l'agent Reporting."""
        super().__init__(inference)

    async def peut_traiter(self, requete: AgentRequete) -> float:
        """Score élevé si l'utilisateur demande une synthèse ou un bilan."""
        texte = requete.fil_ancre.lower()
        touches = sum(1 for mot in self.mots_cles if mot in texte)
        if touches == 0:
            return 0.0
        return min(0.7 + 0.1 * touches, 1.0)

    async def traiter(self, requete: AgentRequete) -> AgentReponse:
        """Sans contributions fournies, se comporte comme une synthèse simple."""
        return await self._generer(requete, contexte=None)

    async def synthetiser(
        self, requete: AgentRequete, contributions: list[AgentReponse]
    ) -> AgentReponse:
        """Fusionne plusieurs réponses d'agents en une synthèse narrative.

        Args:
            requete: Requête originale.
            contributions: Réponses produites par d'autres agents.

        Returns:
            Une réponse de synthèse attribuée à l'agent reporting, dont les
            sources agrègent celles des contributions.
        """
        contexte = _formater_contributions(contributions)
        base = await self._generer(requete, contexte)
        sources = _agréger_sources(contributions)
        return AgentReponse(
            texte=base.texte,
            sources=sources,
            confiance=_confiance_min(contributions) or base.confiance,
            agent=self.nom,
        )


def _formater_contributions(contributions: list[AgentReponse]) -> str | None:
    """Met en forme les contributions en contexte de synthèse, ou None si vide."""
    if not contributions:
        return None
    lignes = [f"[{c.agent}] {c.texte}" for c in contributions]
    return "Éléments à synthétiser :\n" + "\n".join(lignes)


def _agréger_sources(contributions: list[AgentReponse]) -> list[str]:
    """Union ordonnée des sources de toutes les contributions (sans doublon)."""
    vues: list[str] = []
    for contribution in contributions:
        for source in contribution.sources:
            if source not in vues:
                vues.append(source)
    return vues


def _confiance_min(contributions: list[AgentReponse]) -> Confiance | None:
    """Confiance la plus basse parmi les contributions (prudence), ou None."""
    if not contributions:
        return None
    ordre = {Confiance.FAIBLE: 0, Confiance.MOYENNE: 1, Confiance.ELEVEE: 2}
    return min((c.confiance for c in contributions), key=lambda c: ordre[c])
```

- [ ] **Step 4 : Lancer le test (vert)**

Run : `pytest api/tests/agents/test_agent_reporting.py -v`
Expected : PASS (2 tests).

- [ ] **Step 5 : Lint + commit**

```bash
ruff format api/app/services/agents/agent_reporting.py api/tests/agents/test_agent_reporting.py
ruff check api/app/services/agents/agent_reporting.py api/tests/agents/test_agent_reporting.py
git add api/app/services/agents/agent_reporting.py api/tests/agents/test_agent_reporting.py
git commit -m "feat(agents): AgentReporting (synthèse narrative multi-agents)"
```

---

### Task 9 : Câblage API derrière un flag (`config.py`, `api_deps.py`, `routers/chat.py`)

> 🎓 **Concept — Livrer sans casser (feature flag + composition racine).** Une plateforme agentique se met en service **progressivement**. On l'expose derrière `agents_enabled` (OFF par défaut) : tant qu'il est OFF, la V2 (`ConseilService`) reste seule en production. La *composition racine* (`api_deps.get_orchestrateur`) est le seul endroit où l'on **assemble** le graphe d'objets : créer le registre, instancier chaque agent avec ses ports concrets, les enregistrer, construire le routeur puis l'orchestrateur. C'est le pattern « composition root » : tout le câblage en un lieu, le reste du code n'en sait rien.
>
> **Pourquoi ne pas brancher d'API météo/prix réelle maintenant ?** Le socle livre les *agents* et leur *contrat d'outil*. Les `MeteoPort`/`PrixPort` concrets (httpx vers une vraie source) sont des tâches de données, indépendantes, à faire ensuite — d'ici là on enregistre Météo/Prix avec un adaptateur « indisponible » qui renvoie `{}` (l'agent dégrade alors en conseil générique). Cela garde le socle 100 % testable et déployable sans dépendance externe.

**Files:**
- Modify: `api/app/core/config.py` (ajouter `agents_enabled: bool = False`)
- Modify: `api/app/api_deps.py` (ajouter `get_orchestrateur`)
- Modify: `api/app/routers/chat.py` (router vers l'orchestrateur si `agents_enabled`)
- Create: `api/app/services/outils/indisponible.py` (`MeteoIndisponible`, `PrixIndisponible`)
- Test: `api/tests/agents/test_cablage_orchestrateur.py`
- Test: `api/tests/test_chat_agents.py` (intégration FastAPI via `dependency_overrides`)

**Interfaces:**
- Consumes : tout le socle (Tasks 1-8), `get_settings`, `app.state.{inference,cache,journal,rag}`.
- Produces : `get_orchestrateur(request) -> Orchestrateur`, branchement conditionnel dans le router chat.

- [ ] **Step 1 : Lire les fichiers à modifier pour caler les points d'insertion**

Run : ouvrir `api/app/core/config.py` (repérer la classe `Settings` et un flag existant, ex. `semantic_cache_enabled`, pour copier le style), `api/app/routers/chat.py` (repérer la dépendance `get_dialogue_service`/`get_conseil_service` et le handler du POST chat).

- [ ] **Step 2 : Écrire le test de câblage (rouge)**

```python
# api/tests/agents/test_cablage_orchestrateur.py
"""La composition racine assemble un orchestrateur fonctionnel."""

from __future__ import annotations

import pytest

from app.application.orchestrateur import Orchestrateur
from app.services.outils.indisponible import MeteoIndisponible, PrixIndisponible


@pytest.mark.asyncio
async def test_outils_indisponibles_renvoient_vide() -> None:
    assert await MeteoIndisponible().previsions("Daloa") == {}
    assert await PrixIndisponible().cours() == {}


def test_construction_orchestrateur_enregistre_les_quatre_agents() -> None:
    # Construit le graphe via la fabrique testable (sans FastAPI).
    from app.api_deps import _construire_orchestrateur

    inference = object()  # ports factices : on ne teste que le câblage
    orch = _construire_orchestrateur(inference=inference, cache=object(), journal=object(), rag=None)
    assert isinstance(orch, Orchestrateur)
    noms = orch._routeur._registre.noms()  # noqa: SLF001
    assert set(noms) == {"rag", "meteo", "prix", "reporting"}
```

- [ ] **Step 3 : Ajouter le flag de config**

Dans `api/app/core/config.py`, classe `Settings`, à côté de `semantic_cache_enabled` :

```python
    agents_enabled: bool = False
    """Active la plateforme agentique V3 (orchestrateur + agents). OFF par défaut."""
```

- [ ] **Step 4 : Créer les adaptateurs outils « indisponibles »**

```python
# api/app/services/outils/indisponible.py
"""Adaptateurs d'outils « indisponibles » : renvoient un résultat vide.

Permettent d'enregistrer les agents Météo/Prix dans le socle sans dépendance
externe. L'agent dégrade alors proprement en conseil générique. À remplacer par
des adaptateurs httpx réels (tâche de données ultérieure).
"""

from __future__ import annotations


class MeteoIndisponible:
    """Source météo neutre (aucune donnée)."""

    async def previsions(self, localite: str) -> dict[str, object]:
        """Retourne un dictionnaire vide (pas de prévisions)."""
        return {}


class PrixIndisponible:
    """Source prix neutre (aucune donnée)."""

    async def cours(self) -> dict[str, object]:
        """Retourne un dictionnaire vide (pas de cours)."""
        return {}
```

- [ ] **Step 5 : Ajouter la fabrique + la dépendance dans `api_deps.py`**

```python
# Ajouts en tête de api/app/api_deps.py
from app.application.orchestrateur import Orchestrateur
from app.application.registre import RegistreAgents
from app.application.routage import RouteurIntention
from app.services.agents.agent_meteo import AgentMeteo
from app.services.agents.agent_prix import AgentPrix
from app.services.agents.agent_rag import AgentRag
from app.services.agents.agent_reporting import AgentReporting
from app.services.outils.indisponible import MeteoIndisponible, PrixIndisponible
from app.services.outils.meteo import OutilMeteo
from app.services.outils.prix import OutilPrix


def _construire_orchestrateur(inference, cache, journal, rag) -> Orchestrateur:
    """Assemble le graphe agentique (composition racine, testable).

    Args:
        inference: Port d'inférence.
        cache: Port de cache/rate-limit.
        journal: Port de journalisation.
        rag: Récupérateur RAG, ou None.

    Returns:
        Un orchestrateur prêt à traiter, avec les 4 agents Cœur enregistrés.
    """
    registre = RegistreAgents()
    registre.enregistrer(AgentRag(inference, rag=rag))
    registre.enregistrer(AgentMeteo(inference, OutilMeteo(MeteoIndisponible())))
    registre.enregistrer(AgentPrix(inference, OutilPrix(PrixIndisponible())))
    registre.enregistrer(AgentReporting(inference))
    routeur = RouteurIntention(registre)
    return Orchestrateur(routeur, journal, cache, agent_defaut="rag")


def get_orchestrateur(request: Request) -> Orchestrateur:
    """Construit l'orchestrateur depuis les ports en état d'application."""
    return _construire_orchestrateur(
        inference=request.app.state.inference,
        cache=request.app.state.cache,
        journal=request.app.state.journal,
        rag=getattr(request.app.state, "rag", None),
    )
```

- [ ] **Step 6 : Brancher le router chat derrière le flag**

Dans `api/app/routers/chat.py`, dans le handler du POST de conseil (tour unique non-streaming, le plus simple à brancher d'abord) : si `settings.agents_enabled`, utiliser `get_orchestrateur(request).traiter(...)` au lieu du service de conseil. Conserver strictement le même schéma de réponse (`Conseil` → DTO existant). Exemple de structure (adapter aux noms réels du handler) :

```python
    settings = get_settings()
    if settings.agents_enabled:
        orchestrateur = get_orchestrateur(request)
        conseil = await orchestrateur.traiter(
            payload.question, payload.langue, client_ip, historique=payload.historique
        )
    else:
        conseil = await conseil_service.conseiller(
            payload.question, payload.langue, client_ip, payload.historique
        )
    # … mapping Conseil -> DTO de réponse inchangé …
```

- [ ] **Step 7 : Écrire le test d'intégration FastAPI (rouge → vert)**

```python
# api/tests/test_chat_agents.py
"""Intégration : le POST de conseil route via l'orchestrateur quand le flag est ON."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

# Réutiliser les fixtures/fabriques de test existantes (app + overrides d'inférence).
# Voir api/tests/conftest.py pour le pattern de construction de l'app de test.


@pytest.mark.skip(reason="à activer en branchant les fixtures app de conftest.py")
def test_chat_via_orchestrateur(client: TestClient) -> None:
    reponse = client.post("/v1/chat", json={"question": "comment tailler le cacaoyer ?"})
    assert reponse.status_code == 200
    assert "disclaimer" in reponse.json()
```

> **Note :** ce test d'intégration dépend des fixtures de `api/tests/conftest.py` (construction de l'app + override des ports d'inférence). L'implémenteur doit lire `conftest.py`, calquer une fixture qui force `agents_enabled=True`, retirer le `skip`, et vérifier que la réponse a le même schéma que le chemin V2. Les tests unitaires (Tasks 1-8) couvrent déjà la logique ; ce test verrouille la non-régression du contrat HTTP.

- [ ] **Step 8 : Lancer toute la suite**

Run : `pytest api/ -q`
Expected : tous les tests passent (les ~489 existants + les nouveaux). Vérifier la couverture ≥ 80 % : `pytest api/ --cov=app --cov-report=term-missing`.

- [ ] **Step 9 : Lint + commit**

```bash
ruff format api/app/ api/tests/
ruff check api/app/ api/tests/
git add api/app/core/config.py api/app/api_deps.py api/app/routers/chat.py api/app/services/outils/indisponible.py api/tests/
git commit -m "feat(agents): câblage orchestrateur derrière flag agents_enabled (OFF par défaut)"
```

---

### Task 10 : Refactor DRY + documentation du socle

> 🎓 **Concept — Consolider après avoir fait marcher.** Maintenant que tout passe au vert, on rembourse la dette technique signalée (la duplication de `_fil_ancre`) et on documente l'architecture pour que l'ajout des agents n°5..n°11 soit évident. « Faire marcher, puis faire propre. »

**Files:**
- Create: `api/app/application/contexte.py` (`fil_ancre`, `texte_conversation` — extraits des deux usages)
- Modify: `api/app/application/orchestrateur.py` et `api/app/application/conseil_service.py` (importer depuis `contexte`)
- Create: `docs/agents_v3.md` (architecture + recette « ajouter un agent en 4 étapes »)
- Test: `api/tests/agents/test_contexte.py`

- [ ] **Step 1 : Test du module contexte partagé (rouge)**

```python
# api/tests/agents/test_contexte.py
from __future__ import annotations

from app.application.contexte import fil_ancre, texte_conversation


def test_fil_ancre_prefixe_dernier_tour_user() -> None:
    hist = [{"role": "user", "content": "je traite mon cacao"}, {"role": "assistant", "content": "ok"}]
    assert fil_ancre("quelle dose ?", hist) == "je traite mon cacao quelle dose ?"


def test_fil_ancre_tour_unique_inchange() -> None:
    assert fil_ancre("comment tailler ?", []) == "comment tailler ?"


def test_texte_conversation_concatene_les_tours_user() -> None:
    hist = [{"role": "user", "content": "à Daloa"}, {"role": "assistant", "content": "ok"}]
    assert texte_conversation("et le prix ?", hist) == "à Daloa et le prix ?"
```

- [ ] **Step 2 : Lancer (rouge)** — Run : `pytest api/tests/agents/test_contexte.py -v` → FAIL (module absent).

- [ ] **Step 3 : Créer `application/contexte.py`** en déplaçant `_fil_ancre`/`_texte_conversation` (depuis `conseil_service.py`) en fonctions publiques `fil_ancre`/`texte_conversation` (mêmes corps, voir `conseil_service.py:495-527`).

- [ ] **Step 4 : Remplacer les usages** dans `orchestrateur.py` (supprimer le `_fil_ancre` local, importer `from app.application.contexte import fil_ancre`) et dans `conseil_service.py` (idem, garder des alias privés si besoin pour limiter le diff).

- [ ] **Step 5 : Lancer toute la suite** — Run : `pytest api/ -q` → tout vert (non-régression).

- [ ] **Step 6 : Écrire `docs/agents_v3.md`** — schéma orchestrateur→routeur→agents, et la recette : « **Ajouter un agent (ex. Maladie) en 4 étapes** : 1) écrire `services/agents/agent_maladie.py` héritant d'`AgentBase`, avec `nom`, `mots_cles`, `peut_traiter`, `traiter` ; 2) (si besoin) un outil dans `services/outils/` ; 3) l'enregistrer dans `_construire_orchestrateur` ; 4) un fichier de test `api/tests/agents/test_agent_maladie.py`. Aucune autre modification. » Lier ce doc depuis `docs/architecture.md` et la roadmap (epic A).

- [ ] **Step 7 : Lint + commit**

```bash
ruff format api/app/application/ api/tests/agents/test_contexte.py docs/agents_v3.md
ruff check api/app/application/ api/tests/agents/test_contexte.py
git add api/app/application/contexte.py api/app/application/orchestrateur.py api/app/application/conseil_service.py api/tests/agents/test_contexte.py docs/agents_v3.md docs/architecture.md
git commit -m "refactor(agents): contexte partagé (DRY) + doc d'extension du socle agentique"
```

---

## Auto-revue (effectuée à l'écriture du plan)

**1. Couverture de la roadmap epic A :**
- A1 Orchestrateur → Task 4 ✓
- A2 Framework + registre → Tasks 1 (contrat), 2 (registre), 3 (routeur), 5 (AgentBase) ✓
- A3 Agent RAG → Task 5 ✓
- A4 Agent Météo → Task 6 ✓
- A5 Agent Prix → Task 7 ✓
- A6 Agent Reporting (Should) → Task 8 ✓ (inclus comme 4ᵉ agent, démonstration multi-agents)
- A7-A12 (Maladie, Satellite, Réglementation, Normes, ERP, AgroSense) → **hors socle**, mais le plan garantit leur ajout sans refactor (Task 10, doc d'extension) ✓

**2. Scan placeholders :** aucun « TODO/TBD/à compléter » dans le code des tâches. Le seul `@pytest.mark.skip` (Task 9, test d'intégration) est explicitement justifié et accompagné des instructions pour le lever (dépend des fixtures `conftest.py` propres au dépôt, non lisibles à l'écriture du plan).

**3. Cohérence des types :** `AgentRequete`/`AgentReponse` (champs et types) sont identiques de la Task 1 à la Task 10. `AgentPort` (`nom`, `description`, `mots_cles`, `peut_traiter`, `traiter`) est respecté par tous les agents. `Conseil`, `Confiance`, `Langue`, `guardrails.evaluer/verifier_reponse/REFUS_PHYTO`, `RagRecuperateur.contexte_pour`, `InferencePort.generer`, `postprocess.extraire_sources/estimer_confiance` sont utilisés conformément à leurs signatures réelles vérifiées dans le code.

**Point d'attention pour l'implémenteur :** `AgentRequete` est défini avec `historique` en dernier (champ avec défaut) ; les tests le construisent en positionnel `AgentRequete(q, Langue.FR, fil, ip, [])`. Respecter cet ordre (`question, langue, fil_ancre, client_ip, historique`).

---

## Handoff d'exécution

Plan complet, sauvegardé dans `docs/superpowers/plans/2026-06-29-v3-orchestrateur-agents.md`. Deux options d'exécution :

1. **Subagent-Driven (recommandé)** — un subagent neuf par tâche, revue entre les tâches, itération rapide. SOUS-SKILL : superpowers:subagent-driven-development.
2. **Inline** — exécution dans cette session avec points de contrôle. SOUS-SKILL : superpowers:executing-plans.
