# Latence — réduire les tokens d'entrée — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Réduire le préremplissage CPU (le vrai goulot de latence) en coupant les tokens d'entrée par requête sur 3 leviers indépendants : `cache_prompt` (réutilise le préfixe système), trim du `SYSTEM_PROMPT`, plafond de longueur des passages RAG.

**Architecture:** 3 unités indépendantes et testables isolément — `inference.py` (payload), `prompts.py` (constante), `rag.py` (formatage du contexte + config) — suivies d'un déploiement par paliers et d'une validation empirique en prod.

**Tech Stack:** Python 3.11+, pytest + pytest-asyncio, httpx MockTransport, FastAPI/Pydantic Settings, llama.cpp (GGUF, CPU), K8s ConfigMap.

## Global Constraints

- Python 3.11+, `from __future__ import annotations`, typage systématique, docstrings Google.
- `ruff format` + `ruff check` doivent passer ; imports triés par ruff.
- Couverture min. 97 % sur `api/app/` (gate `--cov-fail-under=97`) ; inférence, réseau et embeddings mockés.
- Garde-fous métier NON négociables : le trim du prompt ne supprime AUCUNE règle (cacao-only, jamais de dosage, jamais de source/chiffre/numéro inventé, clarification, multi-tours, brièveté). Ne jamais générer de dosage, même en test.
- Commits sans signature ni mention d'outil IA.
- Valeurs exactes : `cache_prompt = True` ; `rag_passage_max_chars = 480` ; brièveté « 10 phrases maximum ».
- Commandes pytest/ruff depuis `api/`.

---

### Task 1: `cache_prompt: true` dans les payloads d'inférence

**Files:**
- Modify: `api/app/services/inference.py` (payloads de `generer` et `generer_stream`)
- Test: `api/tests/test_inference.py`

**Interfaces:**
- Consumes: rien.
- Produces: les requêtes vers l'inférence incluent `"cache_prompt": True`.

- [ ] **Step 1 : Écrire les tests qui échouent**

Ajouter à la fin de `api/tests/test_inference.py` (les imports `httpx`, `InferenceClient`, la constante `_SSE` existent déjà dans le fichier) :

```python
async def test_cache_prompt_envoye_dans_le_payload() -> None:
    """Le flag cache_prompt est transmis à l'inférence (réutilisation du préfixe système)."""
    vu: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        vu.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url="http://inference:8000")
    client = InferenceClient("http://inference:8000", "opencacao-8b", 10.0, client=http)
    await client.generer("Question ?")
    assert vu.get("cache_prompt") is True
    await client.close()


async def test_cache_prompt_envoye_dans_le_payload_stream() -> None:
    """cache_prompt est aussi transmis sur le chemin streaming."""
    vu: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        vu.update(json.loads(request.content))
        return httpx.Response(
            200, text=_SSE, headers={"content-type": "text/event-stream"}
        )

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url="http://inference:8000")
    client = InferenceClient("http://inference:8000", "opencacao-8b", 10.0, client=http)
    _ = [m async for m in client.generer_stream("Question ?")]
    assert vu.get("cache_prompt") is True
    await client.close()
```

- [ ] **Step 2 : Lancer les tests pour vérifier l'échec**

Run: `cd api && pytest tests/test_inference.py::test_cache_prompt_envoye_dans_le_payload tests/test_inference.py::test_cache_prompt_envoye_dans_le_payload_stream -v`
Expected: FAIL (`cache_prompt` absent du payload → `vu.get("cache_prompt")` vaut `None`).

- [ ] **Step 3 : Ajouter `cache_prompt` aux deux payloads**

Dans `api/app/services/inference.py`, payload de `generer` — passer de :

```python
        payload = {
            "model": self._model_name,
            "messages": build_messages(question, contexte, historique),
            "max_tokens": max_tokens if max_tokens is not None else self._max_tokens,
            **self._params_decodage(temperature),
        }
```

à (ajouter la ligne `cache_prompt`) :

```python
        payload = {
            "model": self._model_name,
            "messages": build_messages(question, contexte, historique),
            "max_tokens": max_tokens if max_tokens is not None else self._max_tokens,
            # Réutilise le KV du préfixe commun (message système constant) d'une requête
            # à l'autre -> le prompt système n'est plus re-prérempli (latence CPU).
            "cache_prompt": True,
            **self._params_decodage(temperature),
        }
```

Et dans `generer_stream`, le payload :

```python
        payload = {
            "model": self._model_name,
            "messages": build_messages(question, contexte, historique),
            "max_tokens": max_tokens if max_tokens is not None else self._max_tokens,
            "stream": True,
            **self._params_decodage(temperature),
        }
```

devient :

```python
        payload = {
            "model": self._model_name,
            "messages": build_messages(question, contexte, historique),
            "max_tokens": max_tokens if max_tokens is not None else self._max_tokens,
            "stream": True,
            "cache_prompt": True,
            **self._params_decodage(temperature),
        }
```

- [ ] **Step 4 : Lancer les tests pour vérifier le succès**

Run: `cd api && pytest tests/test_inference.py -v`
Expected: PASS (tous, dont les 2 nouveaux).

- [ ] **Step 5 : Lint**

Run: `cd api && ruff check app/services/inference.py tests/test_inference.py && ruff format --check app/services/inference.py tests/test_inference.py`
Expected: no errors.

- [ ] **Step 6 : Commit**

```bash
git add api/app/services/inference.py api/tests/test_inference.py
git commit -m "perf(inference): cache_prompt pour réutiliser le préfixe système (latence)"
```

---

### Task 2: Trim du `SYSTEM_PROMPT` (532 → ~290 tokens)

**Files:**
- Modify: `api/app/services/prompts.py` (constante `SYSTEM_PROMPT`)
- Test: `api/tests/test_prompts.py`

**Interfaces:**
- Consumes: rien.
- Produces: `SYSTEM_PROMPT` condensé (< 1300 car.) conservant toutes les règles.

- [ ] **Step 1 : Écrire/mettre à jour les tests**

Dans `api/tests/test_prompts.py`, ajouter ce test (les tests existants
`test_system_prompt_consigne_brievete_ferme` et
`test_system_prompt_conserve_les_regles_critiques` doivent CONTINUER à passer) :

```python
def test_system_prompt_condense() -> None:
    # Trim pour réduire le préremplissage : nettement plus court qu'avant (2129 car.),
    # mais toutes les règles préservées (cf. test_system_prompt_conserve_les_regles_critiques).
    assert len(SYSTEM_PROMPT) < 1300
    assert "invente" in SYSTEM_PROMPT
```

- [ ] **Step 2 : Lancer le test pour vérifier l'échec**

Run: `cd api && pytest tests/test_prompts.py::test_system_prompt_condense -v`
Expected: FAIL (le prompt actuel fait 2129 car. > 1300).

- [ ] **Step 3 : Remplacer la constante `SYSTEM_PROMPT`**

Dans `api/app/services/prompts.py`, remplacer INTÉGRALEMENT la constante `SYSTEM_PROMPT`
par cette version condensée (toutes les règles conservées, formulées plus court) :

```python
SYSTEM_PROMPT = (
    "Tu es OpenCacao, assistant de conseil agronomique pour les producteurs de "
    "cacao de Côte d'Ivoire. Réponds en français simple, clair et bienveillant, "
    "adapté à un producteur qui n'est pas expert.\n"
    "Règles :\n"
    "- Tu traites UNIQUEMENT le cacao. Toute autre culture (maïs, manioc, igname, "
    "riz, anacarde, hévéa, palmier…) ou tout autre sujet : explique poliment que ce "
    "n'est pas ton domaine et oriente vers l'agent ANADER local. (Arbres d'ombrage "
    "et cultures associées acceptés UNIQUEMENT au service d'une plantation de cacao.)\n"
    "- Ne donne jamais de dosages précis de produits phytosanitaires : oriente vers "
    "l'agent ANADER local.\n"
    "- N'invente JAMAIS une source, une date, un chiffre ni un nom d'organisme ; ne "
    "cite une source (CNRA, ANADER, Conseil du Café-Cacao, FAO, FIRCA) que si elle "
    "figure dans le contexte fourni ou si tu en es certain.\n"
    "- Ne donne jamais toi-même un numéro de téléphone ni une adresse : demande la "
    "ville du producteur ; les coordonnées ANADER de sa zone sont ajoutées "
    "automatiquement.\n"
    "- En conversation à plusieurs tours, garde le MÊME sujet et résous les références "
    "(« le », « ça », « ce traitement »…) d'après l'échange en cours.\n"
    "- Si une information essentielle manque (localité, symptômes observés…), pose UNE "
    "question de clarification simple avant de répondre, au lieu de deviner.\n"
    "- Sois bref : réponds en 10 phrases maximum, va droit au but, sans rappel général "
    "ni reformulation de la question."
)
```

> Ne PAS toucher `CONTEXTE_PROMPT`, `_dialogue_alternant`, ni `build_messages`.

- [ ] **Step 4 : Lancer les tests pour vérifier le succès**

Run: `cd api && pytest tests/test_prompts.py -v`
Expected: PASS — y compris `test_system_prompt_consigne_brievete_ferme`
(« 10 phrases maximum ») et `test_system_prompt_conserve_les_regles_critiques`
(« UNIQUEMENT le cacao », « dosages précis », « jamais toi-même un numéro »).

- [ ] **Step 5 : Lint**

Run: `cd api && ruff check app/services/prompts.py tests/test_prompts.py && ruff format --check app/services/prompts.py tests/test_prompts.py`
Expected: no errors.

- [ ] **Step 6 : Commit**

```bash
git add api/app/services/prompts.py api/tests/test_prompts.py
git commit -m "perf(prompts): condenser le prompt système (réduit le préremplissage CPU)"
```

---

### Task 3: Plafond de longueur des passages RAG

**Files:**
- Modify: `api/app/core/config.py` (nouveau réglage)
- Modify: `api/app/services/rag.py` (`tronquer_passage`, `formater_contexte`, `RagRecuperateur`)
- Modify: `api/app/main.py:155-164` (passer le réglage)
- Modify: `deploy/k8s/api.yaml` (ConfigMap)
- Test: `api/tests/test_rag.py`

**Interfaces:**
- Consumes: `Settings.rag_passage_max_chars` (int) ; `rag.Passage(texte, source, score)` (dataclass existante).
- Produces:
  - `rag.tronquer_passage(texte: str, max_chars: int) -> str`
  - `rag.formater_contexte(passages, max_chars: int | None = None) -> str`
  - `RagRecuperateur.__init__(..., passage_max_chars: int = 0)`

- [ ] **Step 1 : Écrire les tests qui échouent**

Ajouter dans `api/tests/test_rag.py` (le module est importé sous le nom `rag` ; si ce
n'est pas déjà le cas, ajouter `from app.services import rag` en tête) :

```python
def test_tronquer_passage_court_inchange() -> None:
    assert rag.tronquer_passage("Texte court.", 480) == "Texte court."


def test_tronquer_passage_long_coupe_a_la_phrase() -> None:
    texte = ("a" * 300) + ". " + ("b" * 300)  # > 480 ; fin de phrase à ~301
    r = rag.tronquer_passage(texte, 480)
    assert len(r) <= 482
    assert r.endswith("…")
    contenu = r[:-2].rstrip()
    assert texte.startswith(contenu)  # pas de réécriture, pas de mot coupé
    assert contenu.endswith(".")  # coupé à une fin de phrase


def test_tronquer_passage_repli_sur_espace() -> None:
    texte = "mot " * 300  # > 480, aucune fin de phrase
    r = rag.tronquer_passage(texte, 480)
    assert len(r) <= 482
    assert r.endswith("…")
    contenu = r[:-2].rstrip()
    assert texte.startswith(contenu)
    assert contenu.endswith("mot")  # coupé à une frontière de mot


def test_formater_contexte_tronque_les_longs_garde_les_courts() -> None:
    passages = [
        rag.Passage(texte="x" * 1000, source="CNRA", score=0.9),
        rag.Passage(texte="Court.", source="ANADER", score=0.8),
    ]
    sortie = rag.formater_contexte(passages, max_chars=480)
    assert "[1]" in sortie and "[2]" in sortie  # les 3->2 passages conservés
    assert "Court." in sortie  # passage court inchangé
    assert "…" in sortie  # passage long tronqué
    assert ("x" * 1000) not in sortie  # le long a bien été coupé


def test_formater_contexte_sans_max_chars_inchange() -> None:
    passages = [rag.Passage(texte="y" * 1000, source="CNRA", score=0.9)]
    assert ("y" * 1000) in rag.formater_contexte(passages)


def test_rag_passage_max_chars_defaut() -> None:
    from app.core.config import Settings

    assert Settings().rag_passage_max_chars == 480
```

- [ ] **Step 2 : Lancer les tests pour vérifier l'échec**

Run: `cd api && pytest tests/test_rag.py -k "tronquer or formater_contexte or passage_max_chars" -v`
Expected: FAIL (`tronquer_passage` n'existe pas ; `formater_contexte` n'a pas de
paramètre `max_chars` ; `rag_passage_max_chars` absent de la config).

- [ ] **Step 3 : Ajouter le réglage de config**

Dans `api/app/core/config.py`, juste après le bloc RAG existant (après
`rag_hybride_enabled`), ajouter :

```python
    # Plafond de longueur d'un passage RAG injecté (réduit les tokens d'entrée -> la
    # latence de préremplissage CPU). Coupe à une frontière de phrase. Réglable à chaud.
    rag_passage_max_chars: int = 480
```

- [ ] **Step 4 : Implémenter la troncature dans `rag.py`**

Dans `api/app/services/rag.py` (`re` est déjà importé), ajouter avant
`formater_contexte` :

```python
_FIN_PHRASE = re.compile(r"[.!?\n]")


def tronquer_passage(texte: str, max_chars: int) -> str:
    """Tronque un passage à ``max_chars``, de préférence à une frontière de phrase.

    Réduit les tokens d'entrée injectés au modèle sans couper un mot en deux : coupe à
    la dernière fin de phrase (``.!?\\n``) de la fenêtre si elle est assez tardive,
    sinon au dernier espace. Ajoute « … » si tronqué. Passage déjà court : inchangé.

    Args:
        texte: Le texte du passage.
        max_chars: Longueur maximale visée (caractères).

    Returns:
        Le texte tronqué (suffixé « … »), ou le texte d'origine s'il est déjà court.
    """
    if len(texte) <= max_chars:
        return texte
    fenetre = texte[:max_chars]
    coupures = [m.end() for m in _FIN_PHRASE.finditer(fenetre)]
    if coupures and coupures[-1] >= max_chars // 2:
        return fenetre[: coupures[-1]].rstrip() + " …"
    espace = fenetre.rfind(" ")
    contenu = fenetre[:espace] if espace > 0 else fenetre
    return contenu.rstrip() + " …"
```

Puis remplacer `formater_contexte` par :

```python
def formater_contexte(passages: list[Passage], max_chars: int | None = None) -> str:
    """Met en forme les passages récupérés pour injection dans le prompt.

    Si ``max_chars`` est fourni, chaque passage est tronqué à cette longueur (à une
    frontière de phrase) pour réduire les tokens d'entrée — donc le préremplissage CPU.
    Sans ``max_chars`` : comportement inchangé (rétrocompat).
    """
    blocs = []
    for numero, passage in enumerate(passages, start=1):
        source = f" (source : {passage.source})" if passage.source else ""
        texte = tronquer_passage(passage.texte, max_chars) if max_chars else passage.texte
        blocs.append(f"[{numero}]{source} {texte}")
    return "\n\n".join(blocs)
```

- [ ] **Step 5 : Câbler le réglage dans `RagRecuperateur`**

Dans `api/app/services/rag.py`, `RagRecuperateur.__init__` : ajouter le paramètre
`passage_max_chars: int = 0` à la fin des kwargs et le stocker. Le bloc d'init
devient :

```python
        hybride: bool = True,
        passage_max_chars: int = 0,
    ) -> None:
        ...
        self._hybride = hybride
        self._passage_max_chars = passage_max_chars
```

(ajouter la ligne dans la signature et la ligne `self._passage_max_chars = ...` à la
fin du corps de `__init__`, après `self._hybride = hybride`).

Puis dans `contexte_pour`, remplacer la dernière ligne :

```python
        return formater_contexte(passages)
```

par :

```python
        return formater_contexte(passages, self._passage_max_chars or None)
```

- [ ] **Step 6 : Passer le réglage à la construction**

Dans `api/app/main.py` (construction du `RagRecuperateur`, ~ligne 155), ajouter le
kwarg `passage_max_chars` :

```python
    return embeddings, RagRecuperateur(
        embeddings,
        index,
        settings.rag_top_k,
        settings.rag_min_similarite,
        candidats=settings.rag_candidats,
        poids_lexical=settings.rag_poids_lexical,
        seuil_lexical=settings.rag_seuil_lexical,
        hybride=settings.rag_hybride_enabled,
        passage_max_chars=settings.rag_passage_max_chars,
    )
```

- [ ] **Step 7 : Aligner le déploiement**

Dans `deploy/k8s/api.yaml`, après la ligne `RAG_SEUIL_LEXICAL`, ajouter :

```yaml
  # Plafond de longueur d'un passage RAG (chantier latence) : coupe le gras des
  # passages pour réduire les tokens d'entrée. Réglable à chaud.
  RAG_PASSAGE_MAX_CHARS: "480"
```

- [ ] **Step 8 : Lancer les tests pour vérifier le succès**

Run: `cd api && pytest tests/test_rag.py -v`
Expected: PASS (tous, dont les nouveaux).

- [ ] **Step 9 : Lint + parse YAML**

Run: `cd api && ruff check app/core/config.py app/services/rag.py app/main.py tests/test_rag.py && ruff format --check app/core/config.py app/services/rag.py app/main.py tests/test_rag.py`
Run: `cd .. && python -c "import yaml; list(yaml.safe_load_all(open('deploy/k8s/api.yaml',encoding='utf-8'))); print('yaml OK')"`
Expected: no errors ; `yaml OK`.

- [ ] **Step 10 : Commit**

```bash
git add api/app/core/config.py api/app/services/rag.py api/app/main.py deploy/k8s/api.yaml api/tests/test_rag.py
git commit -m "perf(rag): plafonner la longueur des passages injectés (réduit le préremplissage)"
```

---

### Task 4: Vérification globale, déploiement par paliers & validation empirique

**Files:** aucun changement de code (validation transverse + runbook).

- [ ] **Step 1 : Suite complète + lint global**

Run: `cd api && pytest -q`
Expected: PASS ; couverture ≥ 97 %.
Run: `cd api && ruff check app tests && ruff format --check app tests`
Expected: no errors.

- [ ] **Step 2 : Livrer via la routine de sprint**

```bash
git push origin develop
gh pr create --base main --head develop --title "perf(latence): réduire les tokens d'entrée (cache_prompt + prompt + RAG)" --body "3 leviers : cache_prompt, trim prompt système, cap longueur passages RAG. Validation empirique par paliers en prod."
gh pr merge <num> --merge
git checkout develop && git merge --ff-only origin/main && git push origin develop
```

Attendre `release.yml` (`gh run watch <id> --exit-status`) et relever le tag
(`git fetch --tags && git tag -l "v0.6.*" | sort -V | tail -1`).

- [ ] **Step 3 : Déployer + appliquer le nouveau réglage ConfigMap**

> ⚠️ `roll-image.sh` ne synchronise PAS la ConfigMap (seulement APP_VERSION + image).
> Le nouveau `RAG_PASSAGE_MAX_CHARS` doit être patché explicitement.

```bash
KUBECONFIG=kubeconfig-hetzner.yaml NS=opencacao bash deploy/scripts/roll-image.sh <X.Y.Z>
KUBECONFIG=kubeconfig-hetzner.yaml kubectl -n opencacao patch configmap api-config \
  --type merge -p '{"data":{"RAG_PASSAGE_MAX_CHARS":"480"}}'
KUBECONFIG=kubeconfig-hetzner.yaml kubectl -n opencacao rollout restart deployment/api
KUBECONFIG=kubeconfig-hetzner.yaml kubectl -n opencacao rollout status deployment/api --timeout=180s
KUBECONFIG=kubeconfig-hetzner.yaml kubectl -n opencacao exec deploy/redis -- sh -c "redis-cli --scan --pattern 'cache:chat:*' | xargs -r redis-cli del"
```

Vérifier : `APP_VERSION` à jour, `RAG_PASSAGE_MAX_CHARS=480` live, pods `Running`,
`GET /v1/health` = `{"status":"ok"}`.

- [ ] **Step 4 : Validation empirique (mesure + recall + garde-fous)**

Lancer ~8 questions via `POST /v1/chat` (corps JSON UTF-8 depuis un fichier — éviter
les soucis d'encodage shell Windows), mesurer la latence (`curl -w "%{time_total}"`)
et inspecter chaque réponse. Pour bénéficier du préfixe caché (`cache_prompt`),
mesurer la latence sur la **2ᵉ** exécution d'une même série (la 1ʳᵉ amorce le cache).

Questions repères : (1) « Comment lutter contre la pourriture brune du cacaoyer ? »
(2) « Quel est le prix du cacao bord-champ ? » (3) « Quelles précipitations à Daloa ? »
(4) « Je veux le contact ANADER à Soubré. » (5) « Comment cultiver le maïs ? » (refus)
(6) « Comment tailler un cacaoyer adulte ? » (7) « Quels arbres d'ombrage pour ma
cacaoyère ? » (8) « Quand récolter le cacao ? ».

Vérifier pour chaque : latence vs baseline (~38 s) ; **recall** (les réponses RAG
citent toujours les bonnes sources, l'info clé reste présente malgré la troncature) ;
garde-fous (#5 refusé/redirigé) ; disclaimer présent ; non-troncature de la réponse.

**Critère de succès** : latence médiane des réponses générées en baisse nette vs
~38 s, SANS perte de recall, de garde-fou ni de disclaimer. Consigner les mesures.

- [ ] **Step 5 : Repli si nécessaire (à chaud)**

Si le recall souffre : remonter `RAG_PASSAGE_MAX_CHARS` (ex. 480 → 700) puis
`rollout restart deployment/api` + purge `cache:chat:*`.

---

## Self-Review

**Spec coverage :**
- Levier 1 `cache_prompt` (generer + generer_stream) → Task 1. ✓
- Levier 2 trim `SYSTEM_PROMPT` (toutes règles préservées) → Task 2 + tests de non-régression. ✓
- Levier 3 cap passages RAG (`tronquer_passage`, `formater_contexte`, config, câblage, deploy) → Task 3. ✓
- Validation empirique par paliers + recall + repli → Task 4. ✓
- Couverture ≥ 97 %, lint → Task 4 step 1. ✓

**Placeholder scan :** `<num>`/`<X.Y.Z>`/`<id>` = valeurs runtime (n° PR, tag, run id), non substituables à l'avance ; tout le reste est du code concret.

**Type consistency :** `tronquer_passage(str, int) -> str`, `formater_contexte(list[Passage], int | None) -> str`, `RagRecuperateur(..., passage_max_chars: int = 0)`, `rag_passage_max_chars: int = 480` — cohérents entre Task 3, ses tests et la construction `main.py`. `Passage(texte, source, score)` = dataclass existante. `cache_prompt` (bool) cohérent entre payload et tests.
