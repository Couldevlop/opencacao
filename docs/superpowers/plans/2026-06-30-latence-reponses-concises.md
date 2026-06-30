# Latence — réponses plus concises — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Réduire la latence de génération en faisant produire au modèle des réponses plus courtes (consigne ferme « 10 phrases maximum »), avec `max_tokens=400` comme plafond de sécurité, sans toucher aux garde-fous.

**Architecture:** Deux changements ciblés — une consigne de brièveté ferme dans `SYSTEM_PROMPT` (le levier réel) et l'alignement de `max_tokens` à 400 (code + déploiement) — puis déploiement via la routine et validation empirique en prod (la latence réelle n'est mesurable qu'en inférence réelle).

**Tech Stack:** Python 3.11+, pytest, FastAPI/Pydantic Settings, K8s ConfigMap, llama.cpp (GGUF, CPU).

## Global Constraints

- Python 3.11+, `from __future__ import annotations`, typage systématique, docstrings Google.
- `ruff format` + `ruff check` doivent passer ; imports triés par ruff.
- Couverture min. 97 % sur `api/app/` (gate CI `--cov-fail-under=97`) ; inférence et réseau mockés.
- Garde-fous métier NON négociables : la concision ne doit retirer AUCUNE règle (cacao-only, pas de dosage phytosanitaire, pas de source/numéro inventé). Ne jamais générer de dosage, même en test.
- Commits sans signature ni mention d'outil IA (`Co-Authored-By` interdit).
- Valeurs exactes : consigne « 10 phrases maximum » ; `max_tokens = 400` (uniforme, tous canaux).
- Commandes pytest/ruff lancées depuis `api/`.

---

### Task 1: Consigne de brièveté ferme dans `SYSTEM_PROMPT`

**Files:**
- Modify: `api/app/services/prompts.py` (constante `SYSTEM_PROMPT`, dernière puce)
- Test: `api/tests/test_prompts.py`

**Interfaces:**
- Consumes: rien (modification d'une constante de chaîne).
- Produces: `SYSTEM_PROMPT` contient « 10 phrases maximum » et conserve les règles critiques.

- [ ] **Step 1 : Écrire les tests qui échouent**

Ajouter dans `api/tests/test_prompts.py` (l'import `build_messages` existe déjà ; ajouter l'import de `SYSTEM_PROMPT`). En tête du fichier, étendre l'import :

```python
from app.services.prompts import SYSTEM_PROMPT, build_messages
```

Puis ajouter ces deux tests à la fin du fichier :

```python
def test_system_prompt_consigne_brievete_ferme() -> None:
    # Le levier de latence : une consigne ferme de brièveté (pas la molle « reste concis »).
    assert "10 phrases maximum" in SYSTEM_PROMPT


def test_system_prompt_conserve_les_regles_critiques() -> None:
    # Non-régression : la concision ne doit effacer AUCUN garde-fou métier.
    assert "UNIQUEMENT le cacao" in SYSTEM_PROMPT
    assert "dosages précis" in SYSTEM_PROMPT
    assert "jamais toi-même un numéro" in SYSTEM_PROMPT
```

- [ ] **Step 2 : Lancer les tests pour vérifier l'échec**

Run: `cd api && pytest tests/test_prompts.py::test_system_prompt_consigne_brievete_ferme -v`
Expected: FAIL (la chaîne « 10 phrases maximum » n'est pas encore dans le prompt).

- [ ] **Step 3 : Modifier la dernière puce de `SYSTEM_PROMPT`**

Dans `api/app/services/prompts.py`, remplacer la dernière ligne de la constante :

- Avant :

```python
    "- Reste concis : va à l'essentiel, surtout pour une réponse par SMS."
```

- Après :

```python
    "- Sois bref : réponds en 10 phrases maximum, va droit au but, sans rappel "
    "général ni reformulation de la question. Mieux vaut une réponse courte et "
    "juste qu'une longue."
```

> Ne RIEN changer d'autre dans le module (les règles cacao-only, dosages, sources,
> numéro, multi-tours, clarification et la fonction `build_messages` restent intactes).

- [ ] **Step 4 : Lancer les tests pour vérifier le succès**

Run: `cd api && pytest tests/test_prompts.py -v`
Expected: PASS (tous les tests du fichier, dont les 2 nouveaux).

- [ ] **Step 5 : Lint**

Run: `cd api && ruff check app/services/prompts.py tests/test_prompts.py && ruff format --check app/services/prompts.py tests/test_prompts.py`
Expected: no errors.

- [ ] **Step 6 : Commit**

```bash
git add api/app/services/prompts.py api/tests/test_prompts.py
git commit -m "feat(prompts): consigne ferme de brièveté (10 phrases max) pour réduire la latence"
```

---

### Task 2: `max_tokens = 400` (config + déploiement)

**Files:**
- Modify: `api/app/core/config.py` (`inference_max_tokens` défaut)
- Modify: `deploy/k8s/api.yaml` (`INFERENCE_MAX_TOKENS`)
- Test: `api/tests/test_inference.py`

**Interfaces:**
- Consumes: `Settings` (Pydantic) de `app.core.config`.
- Produces: `Settings().inference_max_tokens == 400` ; ConfigMap prod aligné.

- [ ] **Step 1 : Écrire le test qui échoue**

Ajouter dans `api/tests/test_inference.py` (l'import `Settings` est déjà utilisé dans ce fichier) ce test à la fin :

```python
def test_inference_max_tokens_defaut_400() -> None:
    # Plafond de sécurité aligné sur la cible du chantier latence (réponses concises).
    from app.core.config import Settings

    assert Settings().inference_max_tokens == 400
```

- [ ] **Step 2 : Lancer le test pour vérifier l'échec**

Run: `cd api && pytest tests/test_inference.py::test_inference_max_tokens_defaut_400 -v`
Expected: FAIL (le défaut vaut encore 512).

> Remarque : si une variable d'environnement `INFERENCE_MAX_TOKENS` est présente
> dans le shell, elle surchargerait le défaut. Lancer sans cette variable.

- [ ] **Step 3 : Abaisser le défaut dans la config**

Dans `api/app/core/config.py`, remplacer :

```python
    inference_max_tokens: int = 512
```

par :

```python
    inference_max_tokens: int = 400
```

- [ ] **Step 4 : Aligner le déploiement**

Dans `deploy/k8s/api.yaml`, remplacer :

```yaml
  INFERENCE_MAX_TOKENS: "384"
```

par :

```yaml
  INFERENCE_MAX_TOKENS: "400"
```

- [ ] **Step 5 : Lancer le test pour vérifier le succès**

Run: `cd api && pytest tests/test_inference.py -v`
Expected: PASS (tous les tests du fichier, dont le nouveau).

- [ ] **Step 6 : Lint**

Run: `cd api && ruff check app/core/config.py tests/test_inference.py && ruff format --check app/core/config.py tests/test_inference.py`
Expected: no errors.

- [ ] **Step 7 : Commit**

```bash
git add api/app/core/config.py deploy/k8s/api.yaml api/tests/test_inference.py
git commit -m "config(latence): max_tokens 384->400 (plafond de sécurité, code+déploiement alignés)"
```

---

### Task 3: Vérification globale, déploiement & validation empirique

**Files:** aucun changement de code (validation transverse + runbook).

- [ ] **Step 1 : Suite complète + couverture**

Run: `cd api && pytest -q`
Expected: PASS ; couverture ≥ 97 %.

- [ ] **Step 2 : Lint global**

Run: `cd api && ruff check app tests && ruff format --check app tests`
Expected: no errors.

- [ ] **Step 3 : Livrer via la routine de sprint**

```bash
git push origin develop
gh pr create --base main --head develop --title "feat(latence): réponses concises (10 phrases, max_tokens 400)" --body "Consigne ferme de brièveté + max_tokens 400. Validation empirique de latence en prod après déploiement."
gh pr merge <num> --merge
git checkout develop && git merge --ff-only origin/main && git push origin develop
```

Attendre le succès de `release.yml` (`gh run watch <id> --exit-status`) et relever le nouveau tag (`git fetch --tags && git tag -l "v0.6.*" | sort -V | tail -1`).

- [ ] **Step 4 : Déployer en prod**

```bash
KUBECONFIG=kubeconfig-hetzner.yaml NS=opencacao bash deploy/scripts/roll-image.sh <X.Y.Z>
```

Vérifier : `APP_VERSION` aligné, pods `Running`, `GET /v1/health` = `{"status":"ok"}`.

> `roll-image.sh` purge `cache:chat:*` et change `APP_VERSION` → les réponses
> verbeuses cachées et le pré-chauffage FAQ sont régénérés concis au démarrage.

- [ ] **Step 5 : Validation empirique (mesure avant/après)**

Lancer ~8 questions représentatives via `POST /v1/chat` (corps JSON UTF-8 depuis un
fichier pour éviter les soucis d'encodage shell Windows) et relever pour chacune
la latence (`curl -w "%{time_total}"`) et la longueur de réponse :

1. Agronomie/RAG : « Comment lutter contre la pourriture brune du cacaoyer ? »
2. Prix : « Quel est le prix du cacao bord-champ ? »
3. Météo (localité) : « Quelles précipitations à Daloa cette semaine ? »
4. Contact ANADER : « Je veux le contact ANADER à Soubré. »
5. Multi-tours/clarification : « Comment traiter mes arbres ? » (doit demander des précisions)
6. Hors-filière : « Comment cultiver le maïs ? » (doit rediriger ANADER)
7. Taille : « Comment tailler un cacaoyer adulte ? »
8. Ombrage : « Quels arbres d'ombrage pour ma cacaoyère ? »

Pour chaque réponse, vérifier MANUELLEMENT : non tronquée (finit proprement),
disclaimer présent, garde-fou respecté (refus #6, redirection contact #4), info
utile préservée.

**Critère de succès** : latence médiane en baisse vs baseline (~38 s) SANS perte de
justesse, de garde-fou ni de disclaimer. Consigner les mesures (avant/après).

- [ ] **Step 6 : Repli si nécessaire (à chaud, sans rebuild)**

Si des réponses sont tronquées ou trop sèches :

```bash
KUBECONFIG=kubeconfig-hetzner.yaml kubectl -n opencacao patch configmap api-config \
  --type merge -p '{"data":{"INFERENCE_MAX_TOKENS":"480"}}'
KUBECONFIG=kubeconfig-hetzner.yaml kubectl -n opencacao rollout restart deployment/api
```

(Et/ou assouplir la consigne « 10 » → « 12-15 phrases » au prochain build.)
Noter : `rollout restart` ne purge PAS Redis ; purger manuellement si besoin de
réévaluer les réponses cachées.

---

## Self-Review

**Spec coverage :**
- Consigne ferme « 10 phrases maximum » dans `SYSTEM_PROMPT` → Task 1. ✓
- `max_tokens = 400` (config + déploiement) → Task 2. ✓
- Non-régression des règles critiques → test Task 1. ✓
- Défaut `inference_max_tokens == 400` → test Task 2. ✓
- Sécurités (disclaimer/sources/garde-fous, purge cache) → respectées (aucun code touché ; purge via roll-image, Task 3 step 4). ✓
- Validation empirique en prod (~8 questions, critère de succès) → Task 3 steps 5-6. ✓

**Placeholder scan :** `<num>`/`<X.Y.Z>`/`<id>` sont des valeurs runtime (n° de PR, tag de release, run id) connues seulement à l'exécution — non substituables à l'avance ; tout le reste est concret.

**Type consistency :** `inference_max_tokens` (int, 400) cohérent entre config, test et déploiement ; `SYSTEM_PROMPT` (str) référencé de façon cohérente.
