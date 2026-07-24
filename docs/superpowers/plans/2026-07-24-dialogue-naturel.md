# Plan d'implémentation — Dialogue naturel hybride

> **Pour les workers agentiques :** SOUS-COMPÉTENCE REQUISE : utiliser superpowers:subagent-driven-development (recommandé) ou superpowers:executing-plans pour implémenter ce plan tâche par tâche. Les étapes utilisent des cases à cocher (`- [ ]`).

**But :** rendre le dialogue naturel (formulation par le modèle) sur les questions de clarification, tout en gardant le déclenchement déterministe et les garde-fous souverains, derrière un drapeau désactivé par défaut.

**Architecture :** on généralise le pattern déjà utilisé par les agents Météo/Prix (consigne déterministe → le modèle formule). `clarification.py` sépare la *détection* (déterministe, inchangée) de la *formulation* (modèle). Le prompt système est réchauffé. Un drapeau `dialogue_naturel_enabled` bascule entre le nouveau comportement et l'ancien (scripté + prompt strict).

**Stack :** Python 3.11, FastAPI, httpx, llama.cpp (OpenAI-compatible), pytest + pytest-asyncio. `ruff` pour lint/format.

## Contraintes globales

- **Latence minimale** : la génération de clarification est plafonnée à `CLARIF_MAX_TOKENS = 80`, réutilise le préfixe KV chaud (`cache_prompt=True`, déjà dans le client), n'appelle **jamais** le RAG, et diffuse en flux (premier octet immédiat).
- **Souveraineté inchangée** : toutes les règles de `SYSTEM_PROMPT` (cacao-only, anti-fabrication, ANADER, jamais de dosage, jamais de numéro inventé) restent mot pour mot. Les refus restent déterministes.
- **Drapeau désactivé par défaut** : `dialogue_naturel_enabled: bool = False`. Comportement d'aujourd'hui à l'identique tant qu'il est `false`.
- **Détection inchangée** : les 17 tests de `test_clarification.py` (via `analyser()`) doivent rester verts.
- Python typé (`from __future__ import annotations`), docstrings Google, `ruff format` + `ruff check`. Pas de `print`, logs via `structlog`.
- Tests : `pytest`, inférence mockée (aucun appel réseau réel). Couverture ≥ 97 % sur `api/app/`.

---

### Task 1 : Drapeau de configuration

**Files:**
- Modify: `api/app/core/config.py` (près de `semantic_cache_enabled`, ~ligne 121)
- Test: `api/tests/test_config.py`

**Interfaces:**
- Produces: `Settings.dialogue_naturel_enabled: bool` (défaut `False`), variable d'env `DIALOGUE_NATUREL_ENABLED`.

- [ ] **Step 1 : Test qui échoue**

Ajouter à `api/tests/test_config.py` :

```python
def test_dialogue_naturel_desactive_par_defaut() -> None:
    from app.core.config import Settings

    assert Settings().dialogue_naturel_enabled is False
```

- [ ] **Step 2 : Vérifier l'échec**

Run: `python -m pytest api/tests/test_config.py::test_dialogue_naturel_desactive_par_defaut -v --no-cov`
Expected: FAIL (`AttributeError: ... 'dialogue_naturel_enabled'`)

- [ ] **Step 3 : Implémenter**

Dans `api/app/core/config.py`, sous `semantic_cache_enabled: bool = False`, ajouter :

```python
    # Dialogue naturel (formulation des clarifications par le modèle). Désactivé par
    # défaut : à activer après validation manuelle (déploiement en deux temps). Voir
    # docs/superpowers/specs/2026-07-24-dialogue-naturel-design.md.
    dialogue_naturel_enabled: bool = False
```

- [ ] **Step 4 : Vérifier le succès**

Run: `python -m pytest api/tests/test_config.py::test_dialogue_naturel_desactive_par_defaut -v --no-cov`
Expected: PASS

- [ ] **Step 5 : Commit**

```bash
git add api/app/core/config.py api/tests/test_config.py
git commit -m "feat(config): drapeau dialogue_naturel_enabled (defaut false)"
```

---

### Task 2 : Prompt système réchauffé + prompt strict + paramètres `build_messages`

**Files:**
- Modify: `api/app/services/prompts.py`
- Test: `api/tests/test_prompts.py`

**Interfaces:**
- Produces :
  - `SYSTEM_PROMPT: str` — version **réchauffée** (défaut du module).
  - `SYSTEM_PROMPT_STRICT: str` — l'actuel, mot pour mot (repli drapeau off).
  - `build_messages(question, contexte=None, historique=None, *, system_prompt=SYSTEM_PROMPT, consigne=None) -> list[dict]`.
- Consumes : rien.

- [ ] **Step 1 : Tests qui échouent**

Remplacer dans `api/tests/test_prompts.py` l'assertion `assert "10 phrases maximum" in SYSTEM_PROMPT` (ligne ~63) et ajouter les tests suivants :

```python
def test_system_prompt_rechauffe_garde_les_garde_fous() -> None:
    from app.services.prompts import SYSTEM_PROMPT

    assert "UNIQUEMENT le cacao" in SYSTEM_PROMPT
    assert "dosages précis" in SYSTEM_PROMPT
    assert "N'invente JAMAIS" in SYSTEM_PROMPT
    assert "ANADER" in SYSTEM_PROMPT
    # Ton réchauffé : plus de consigne sèche « sans rappel ni reformulation ».
    assert "sans rappel ni reformulation" not in SYSTEM_PROMPT


def test_system_prompt_strict_conserve_a_l_identique() -> None:
    from app.services.prompts import SYSTEM_PROMPT_STRICT

    assert "10 phrases maximum" in SYSTEM_PROMPT_STRICT
    assert "sans rappel ni reformulation" in SYSTEM_PROMPT_STRICT


def test_build_messages_avec_consigne_de_clarification() -> None:
    from app.services.prompts import build_messages

    msgs = build_messages("Mes feuilles jaunissent", consigne="Pose une question brève.")
    assert msgs[0]["role"] == "system"
    assert "Pose une question brève." in msgs[-1]["content"]
    assert "Mes feuilles jaunissent" in msgs[-1]["content"]
    # La consigne remplace le contexte RAG : pas de bloc « base de connaissances ».
    assert "base de connaissances" not in msgs[-1]["content"]


def test_build_messages_choisit_le_prompt_systeme() -> None:
    from app.services.prompts import SYSTEM_PROMPT_STRICT, build_messages

    msgs = build_messages("q", system_prompt=SYSTEM_PROMPT_STRICT)
    assert msgs[0]["content"] == SYSTEM_PROMPT_STRICT
```

- [ ] **Step 2 : Vérifier l'échec**

Run: `python -m pytest api/tests/test_prompts.py -v --no-cov`
Expected: FAIL (`SYSTEM_PROMPT_STRICT` inexistant ; `consigne`/`system_prompt` inconnus).

- [ ] **Step 3 : Implémenter**

Dans `api/app/services/prompts.py` :

1) Renommer l'actuel `SYSTEM_PROMPT` en `SYSTEM_PROMPT_STRICT` (garder son texte **exact**), puis définir le nouveau `SYSTEM_PROMPT` réchauffé juste après :

```python
SYSTEM_PROMPT_STRICT = (
    "Tu es OpenCacao, assistant de conseil agronomique pour les producteurs de "
    "cacao de Côte d'Ivoire. Réponds en français simple et bienveillant, pour un "
    "producteur non expert.\n"
    "Règles :\n"
    "- Tu traites UNIQUEMENT le cacao. Toute autre culture (maïs, manioc, igname, "
    "anacarde, hévéa, palmier…) ou autre sujet : dis poliment que ce n'est pas ton "
    "domaine et oriente vers l'agent ANADER local. (Ombrage et cultures associées "
    "acceptés UNIQUEMENT au service d'une plantation de cacao.)\n"
    "- Ne donne jamais de dosages précis de produits phytosanitaires : oriente vers "
    "l'agent ANADER local.\n"
    "- N'invente JAMAIS une source, une date, un chiffre ni un nom d'organisme ; ne "
    "cite une source (CNRA, ANADER, Conseil du Café-Cacao, FAO, FIRCA) que si elle "
    "figure dans le contexte fourni.\n"
    "- Ne donne jamais toi-même un numéro de téléphone ni une adresse : demande la "
    "ville du producteur ; les coordonnées ANADER sont ajoutées automatiquement.\n"
    "- En conversation, garde le MÊME sujet et résous les références («le», «ça», «ce "
    "traitement»…) d'après l'échange en cours.\n"
    "- Si une information essentielle manque (localité, symptômes…), pose UNE question "
    "de clarification avant de répondre, au lieu de deviner.\n"
    "- Sois bref : 10 phrases maximum, va droit au but, sans rappel général ni "
    "reformulation de la question."
)

SYSTEM_PROMPT = (
    "Tu es OpenCacao, un conseiller agronomique qui accompagne les producteurs de "
    "cacao de Côte d'Ivoire. Parle en français simple et chaleureux, comme un agent "
    "ANADER sur le terrain qui prend le temps d'écouter un producteur.\n"
    "Règles :\n"
    "- Tu traites UNIQUEMENT le cacao. Toute autre culture (maïs, manioc, igname, "
    "anacarde, hévéa, palmier…) ou autre sujet : dis poliment que ce n'est pas ton "
    "domaine et oriente vers l'agent ANADER local. (Ombrage et cultures associées "
    "acceptés UNIQUEMENT au service d'une plantation de cacao.)\n"
    "- Ne donne jamais de dosages précis de produits phytosanitaires : oriente vers "
    "l'agent ANADER local.\n"
    "- N'invente JAMAIS une source, une date, un chiffre ni un nom d'organisme ; ne "
    "cite une source (CNRA, ANADER, Conseil du Café-Cacao, FAO, FIRCA) que si elle "
    "figure dans le contexte fourni.\n"
    "- Ne donne jamais toi-même un numéro de téléphone ni une adresse : demande la "
    "ville du producteur ; les coordonnées ANADER sont ajoutées automatiquement.\n"
    "- En conversation, garde le MÊME sujet et résous les références («le», «ça», «ce "
    "traitement»…) d'après l'échange en cours.\n"
    "- Si une information essentielle manque (localité, symptômes…), pose UNE question "
    "naturelle et bienveillante avant de conseiller, au lieu de deviner.\n"
    "- Reste concis (vise 6 à 10 phrases) et parle naturellement, sans jargon ni "
    "remplissage."
)
```

2) Adapter `build_messages` (remplacer la signature et le corps existants) :

```python
def build_messages(
    question: str,
    contexte: str | None = None,
    historique: list[dict[str, str]] | None = None,
    *,
    system_prompt: str = SYSTEM_PROMPT,
    consigne: str | None = None,
) -> list[dict[str, str]]:
    """Construit la liste de messages pour l'API d'inférence.

    Args:
        question: Dernière question du producteur.
        contexte: Extraits récupérés (RAG) à injecter, ou None.
        historique: Tours précédents ``[{"role": ..., "content": ...}]``.
        system_prompt: Message système à utiliser (réchauffé ou strict).
        consigne: Consigne de clarification. Si fournie, elle REMPLACE le contexte
            RAG : le modèle doit poser une question, pas répondre (pas de RAG).

    Returns:
        Liste de messages : system + dialogue à rôles alternés finissant par user.
    """
    if consigne is not None:
        contenu_user = f"{consigne}\n\nMessage du producteur : {question}"
    elif contexte:
        contenu_user = f"{CONTEXTE_PROMPT.format(contexte=contexte)}\n\nQuestion : {question}"
    else:
        contenu_user = f"{FALLBACK_SANS_CONTEXTE}\n\nQuestion : {question}"
    dialogue = _dialogue_alternant(historique or [], contenu_user)
    return [{"role": "system", "content": system_prompt}, *dialogue]
```

- [ ] **Step 4 : Vérifier le succès**

Run: `python -m pytest api/tests/test_prompts.py -v --no-cov`
Expected: PASS (tous)

- [ ] **Step 5 : Commit**

```bash
git add api/app/services/prompts.py api/tests/test_prompts.py
git commit -m "feat(prompts): prompt systeme rechauffe + strict + consigne/build_messages"
```

---

### Task 3 : `clarification.detecter_theme` + `consigne_theme` + `besoin_localite`

**Files:**
- Modify: `api/app/services/clarification.py`
- Test: `api/tests/test_clarification.py`

**Interfaces:**
- Produces :
  - `detecter_theme(question, historique) -> str | None` : `"contact"`, `"symptome"`, `"traitement"`, `"rendement"`, `"fertilisation"`, `"plantation"`, ou `None`.
  - `consigne_theme(theme: str, besoin_localite: bool) -> str`.
  - `besoin_localite(question, historique) -> bool`.
- Consumes : helpers privés existants (`_normaliser`, `_fil_utilisateur`, `_repondre_directement`, `_detecter`, `_derniere_reponse_est_clarification`), `contacts`.

- [ ] **Step 1 : Tests qui échouent**

Ajouter à `api/tests/test_clarification.py` :

```python
def test_detecter_theme_symptome() -> None:
    assert clarification.detecter_theme("Mes feuilles jaunissent", None) == "symptome"


def test_detecter_theme_contact_sans_ville() -> None:
    assert clarification.detecter_theme("Je veux le numéro de l'ANADER", None) == "contact"


def test_detecter_theme_question_factuelle_est_none() -> None:
    assert clarification.detecter_theme("Quand récolter les cabosses ?", None) is None


def test_detecter_theme_anti_boucle() -> None:
    historique = [
        {"role": "user", "content": "Mes feuilles jaunissent"},
        {"role": "assistant", "content": "Répondez-moi et je vous conseillerai au mieux."},
        {"role": "user", "content": "Sur les feuilles"},
    ]
    assert clarification.detecter_theme("Sur les feuilles", historique) is None


def test_consigne_theme_ajoute_la_localite_si_besoin() -> None:
    sans = clarification.consigne_theme("symptome", besoin_localite=False)
    avec = clarification.consigne_theme("symptome", besoin_localite=True)
    assert "localit" in avec.lower()
    assert "localit" not in sans.lower()


def test_besoin_localite_vrai_si_aucune_ville() -> None:
    assert clarification.besoin_localite("Mes feuilles jaunissent", None) is True
    assert clarification.besoin_localite("Mes feuilles jaunissent à Daloa", None) is False
```

- [ ] **Step 2 : Vérifier l'échec**

Run: `python -m pytest api/tests/test_clarification.py -k "detecter_theme or consigne_theme or besoin_localite" -v --no-cov`
Expected: FAIL (`detecter_theme`, `consigne_theme`, `besoin_localite` inexistants)

- [ ] **Step 3 : Implémenter**

Dans `api/app/services/clarification.py`, ajouter après `_fil_utilisateur` (et AVANT `analyser`) :

```python
_CONSIGNES: dict[str, str] = {
    "contact": (
        "Le producteur cherche un contact de l'ANADER mais n'a pas indiqué sa localité. "
        "Demande-lui simplement et chaleureusement dans quelle ville ou région il se "
        "trouve, sans rien affirmer d'autre et sans donner de numéro."
    ),
    "symptome": (
        "Il te manque, pour bien l'aider, la partie atteinte (feuilles, cabosses, "
        "tronc/rameaux, racines) et depuis combien de temps cela dure. Pose UNE question "
        "brève, naturelle et bienveillante pour l'obtenir, sans donner de conseil encore."
    ),
    "traitement": (
        "Avant d'orienter, il te faut savoir quel problème précis traiter (maladie, "
        "insecte, mauvaises herbes), sur quelle partie et quelle ampleur. Pose UNE "
        "question brève et naturelle pour le préciser, sans conseiller encore."
    ),
    "rendement": (
        "Pour comprendre la baisse de rendement, il te faut l'âge de la plantation, les "
        "entretiens récents (taille, désherbage, égourmandage) et la présence éventuelle "
        "de maladies. Pose UNE question brève et naturelle en ce sens, sans conclure encore."
    ),
    "fertilisation": (
        "Pour conseiller sur la fertilité, il te faut l'âge de la plantation, si elle a "
        "déjà été fertilisée et le type de sol. Pose UNE question brève et naturelle "
        "pour le savoir, sans donner de recommandation encore."
    ),
    "plantation": (
        "Pour bien accompagner une nouvelle plantation, il te faut la surface envisagée, "
        "le type de sol et si le producteur a déjà des plants sélectionnés. Pose UNE "
        "question brève et naturelle en ce sens, sans conseiller encore."
    ),
}


def detecter_theme(question: str, historique: list[dict[str, str]] | None) -> str | None:
    """Retourne le thème nécessitant une clarification, ou None (réponse directe).

    Même logique de déclenchement que :func:`analyser` (anti-boucle, contact sans
    ville, question informationnelle, détection de thème), mais renvoie le THÈME plutôt
    que le texte scripté — pour que l'appelant fasse formuler la question par le modèle.
    """
    historique = historique or []
    if _derniere_reponse_est_clarification(historique):
        return None
    fil = _fil_utilisateur(question, historique)
    if contacts.intention_contact(question) and contacts.chercher(fil) is None:
        return "contact"
    texte = _normaliser(question)
    if _repondre_directement(texte):
        return None
    return _detecter(texte)


def besoin_localite(question: str, historique: list[dict[str, str]] | None) -> bool:
    """Vrai si aucune ville connue n'apparaît dans le fil (localité à demander)."""
    fil = _fil_utilisateur(question, historique or [])
    return contacts.chercher(fil) is None


def consigne_theme(theme: str, besoin_localite: bool) -> str:
    """Consigne au modèle pour formuler la question de clarification du thème.

    Args:
        theme: Thème renvoyé par :func:`detecter_theme`.
        besoin_localite: Si vrai (et thème != contact), on demande aussi la ville.
    """
    consigne = _CONSIGNES[theme]
    if besoin_localite and theme != "contact":
        consigne += (
            " Demande aussi, dans la même phrase et naturellement, dans quelle localité "
            "il se trouve."
        )
    return consigne
```

- [ ] **Step 4 : Vérifier le succès (nouveaux + régression)**

Run: `python -m pytest api/tests/test_clarification.py -v --no-cov`
Expected: PASS (les 17 existants via `analyser` + les 6 nouveaux)

- [ ] **Step 5 : Commit**

```bash
git add api/app/services/clarification.py api/tests/test_clarification.py
git commit -m "feat(clarification): detecter_theme + consigne_theme + besoin_localite"
```

---

### Task 4 : Client d'inférence — prompt système injecté + consigne

**Files:**
- Modify: `api/app/services/inference.py`
- Test: `api/tests/test_inference.py`

**Interfaces:**
- Consumes : `build_messages(..., system_prompt=..., consigne=...)` (Task 2).
- Produces :
  - `InferenceClient.__init__(..., system_prompt: str = SYSTEM_PROMPT)` (nouveau kwarg, dernier).
  - `InferenceClient.from_settings` choisit `SYSTEM_PROMPT` si `settings.dialogue_naturel_enabled` sinon `SYSTEM_PROMPT_STRICT`.
  - `generer(..., consigne: str | None = None)` et `generer_stream(..., consigne: str | None = None)` passent `system_prompt`/`consigne` à `build_messages`.

- [ ] **Step 1 : Tests qui échouent**

Ajouter à `api/tests/test_inference.py` (adapter `_client(handler)` au style local existant du fichier ; le handler capte le payload) :

```python
async def test_generer_transmet_la_consigne_et_le_prompt_strict() -> None:
    from app.services.prompts import SYSTEM_PROMPT_STRICT

    captures: dict = {}

    def handler(request):
        import json as _json

        captures["payload"] = _json.loads(request.content)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "Dans quelle ville ?"}}]}
        )

    client = _client(handler)
    object.__setattr__(client, "_system_prompt", SYSTEM_PROMPT_STRICT)
    texte = await client.generer("Mes feuilles jaunissent", consigne="Pose une question.", max_tokens=80)

    assert texte == "Dans quelle ville ?"
    msgs = captures["payload"]["messages"]
    assert msgs[0]["content"] == SYSTEM_PROMPT_STRICT
    assert "Pose une question." in msgs[-1]["content"]
    assert captures["payload"]["max_tokens"] == 80
```

- [ ] **Step 2 : Vérifier l'échec**

Run: `python -m pytest api/tests/test_inference.py::test_generer_transmet_la_consigne_et_le_prompt_strict -v --no-cov`
Expected: FAIL (`consigne` inconnu / `_system_prompt` non utilisé)

- [ ] **Step 3 : Implémenter**

Dans `api/app/services/inference.py` :

1) Import du prompt en tête (à côté de `from app.services.prompts import build_messages`) :

```python
from app.services.prompts import SYSTEM_PROMPT, SYSTEM_PROMPT_STRICT, build_messages
```

2) Ajouter `system_prompt` au `__init__` (nouveau paramètre, en dernier) et le stocker :

```python
        system_prompt: str = SYSTEM_PROMPT,
```
```python
        self._system_prompt = system_prompt
```

3) Dans `from_settings`, choisir le prompt selon le drapeau (passer le kwarg au constructeur) :

```python
            system_prompt=SYSTEM_PROMPT if settings.dialogue_naturel_enabled else SYSTEM_PROMPT_STRICT,
```

4) Ajouter `consigne: str | None = None` (dernier paramètre) à `generer` ET `generer_stream`, et remplacer l'appel `build_messages(question, contexte, historique)` par :

```python
            "messages": build_messages(
                question, contexte, historique,
                system_prompt=self._system_prompt, consigne=consigne,
            ),
```

- [ ] **Step 4 : Vérifier le succès**

Run: `python -m pytest api/tests/test_inference.py -v --no-cov`
Expected: PASS

- [ ] **Step 5 : Commit**

```bash
git add api/app/services/inference.py api/tests/test_inference.py
git commit -m "feat(inference): prompt systeme injecte (drapeau) + parametre consigne"
```

---

### Task 5 : Helper partagé de clarification naturelle

**Files:**
- Modify: `api/app/application/conseil_commun.py`
- Test: `api/tests/test_conseil_service.py` (ou un nouveau `api/tests/test_conseil_commun.py`)

**Interfaces:**
- Consumes : `clarification.consigne_theme` / `besoin_localite` (Task 3), `InferencePort.generer` / `generer_stream` avec `consigne` (Task 4).
- Produces :
  - `CLARIF_MAX_TOKENS: int = 80`
  - `async question_clarification(inference, theme, question, historique) -> str`
  - `async question_clarification_stream(inference, theme, question, historique) -> AsyncIterator[str]`

- [ ] **Step 1 : Test qui échoue**

Ajouter à `api/tests/test_conseil_service.py` :

```python
async def test_question_clarification_utilise_la_consigne_et_borne_les_tokens() -> None:
    from app.application import conseil_commun

    captures: dict = {}

    class _FauxInference:
        async def generer(self, question, *, consigne=None, historique=None, max_tokens=None):
            captures.update(consigne=consigne, max_tokens=max_tokens)
            return "Sur quelle partie l'observez-vous, et dans quelle ville êtes-vous ?"

    texte = await conseil_commun.question_clarification(
        _FauxInference(), "symptome", "Mes feuilles jaunissent", None
    )
    assert "ville" in texte
    assert captures["max_tokens"] == conseil_commun.CLARIF_MAX_TOKENS
    assert "question" in captures["consigne"].lower()
```

- [ ] **Step 2 : Vérifier l'échec**

Run: `python -m pytest api/tests/test_conseil_service.py::test_question_clarification_utilise_la_consigne_et_borne_les_tokens -v --no-cov`
Expected: FAIL (`question_clarification` inexistant)

- [ ] **Step 3 : Implémenter**

Dans `api/app/application/conseil_commun.py`, ajouter en tête les imports et à la fin les fonctions :

```python
from collections.abc import AsyncIterator

from app.services import clarification

# Latence : une question de clarification est courte -> on borne fort la génération.
CLARIF_MAX_TOKENS = 80


async def question_clarification(
    inference: object, theme: str, question: str, historique: list[dict[str, str]] | None
) -> str:
    """Fait formuler par le modèle une question de clarification naturelle et brève."""
    consigne = clarification.consigne_theme(
        theme, clarification.besoin_localite(question, historique)
    )
    return await inference.generer(  # type: ignore[attr-defined]
        question, consigne=consigne, historique=historique, max_tokens=CLARIF_MAX_TOKENS
    )


async def question_clarification_stream(
    inference: object, theme: str, question: str, historique: list[dict[str, str]] | None
) -> AsyncIterator[str]:
    """Variante flux : diffuse la question de clarification au fil de l'eau."""
    consigne = clarification.consigne_theme(
        theme, clarification.besoin_localite(question, historique)
    )
    async for fragment in inference.generer_stream(  # type: ignore[attr-defined]
        question, consigne=consigne, historique=historique, max_tokens=CLARIF_MAX_TOKENS
    ):
        yield fragment
```

- [ ] **Step 4 : Vérifier le succès**

Run: `python -m pytest api/tests/test_conseil_service.py::test_question_clarification_utilise_la_consigne_et_borne_les_tokens -v --no-cov`
Expected: PASS

- [ ] **Step 5 : Commit**

```bash
git add api/app/application/conseil_commun.py api/tests/test_conseil_service.py
git commit -m "feat(conseil): helper de clarification naturelle (latence bornee)"
```

---

### Task 6 : Brancher V2 (`ConseilService`) derrière le drapeau

**Files:**
- Modify: `api/app/application/conseil_service.py` (constructeur ~ligne 37 ; sites clarification ~148 et ~302)
- Test: `api/tests/test_conseil_service.py`

**Interfaces:**
- Consumes : `conseil_commun.question_clarification` / `..._stream` (Task 5).
- Produces : `ConseilService.__init__(..., dialogue_naturel: bool = False)` (nouveau kwarg, dernier).

- [ ] **Step 1 : Tests qui échouent**

Ajouter à `api/tests/test_conseil_service.py` (réutiliser les fakes existants du fichier pour cache/journal/inférence ; l'inférence factice doit exposer `generer(..., consigne=...)` renvoyant une question) :

```python
async def test_dialogue_naturel_on_genere_la_question(service_factory) -> None:
    # service_factory : fabrique un ConseilService avec fakes. Voir helpers du fichier.
    service = service_factory(dialogue_naturel=True, reponse_inference="Sur quelle partie ?")
    conseil = await service.conseiller("Mes feuilles jaunissent", Langue.FR, "ip")
    assert "partie" in conseil.reponse  # question générée, pas les puces scriptées
    assert "•" not in conseil.reponse


async def test_dialogue_naturel_off_garde_le_scripte(service_factory) -> None:
    service = service_factory(dialogue_naturel=False)
    conseil = await service.conseiller("Mes feuilles jaunissent", Langue.FR, "ip")
    assert "•" in conseil.reponse  # puces scriptées inchangées
```

> Note d'implémentation du test : si `service_factory` n'existe pas dans le fichier, l'ajouter comme fixture locale construisant `ConseilService` avec les fakes déjà présents (cache no-op, journal no-op, inférence factice paramétrable, RAG None), en passant `dialogue_naturel`.

- [ ] **Step 2 : Vérifier l'échec**

Run: `python -m pytest api/tests/test_conseil_service.py -k "dialogue_naturel_on or dialogue_naturel_off" -v --no-cov`
Expected: FAIL (`dialogue_naturel` inconnu du constructeur)

- [ ] **Step 3 : Implémenter**

1) Constructeur : ajouter le paramètre (dernier) et le stocker :

```python
        dialogue_naturel: bool = False,
```
```python
        self._dialogue_naturel = dialogue_naturel
```

2) Ajouter l'import en tête : `from app.application import conseil_commun` (s'il n'y est pas déjà).

3) **Site sync** (`conseiller`, ~148) : remplacer le bloc scripté par la branche drapeau :

```python
        if self._dialogue_naturel:
            theme = clarification.detecter_theme(question, historique)
            if theme is not None:
                logger.info("clarification_demandee", mode="naturel", theme=theme)
                if await self._cache.hit_rate_limit(client_ip):
                    raise RateLimitDepasse
                texte = await conseil_commun.question_clarification(
                    self._inference, theme, question, historique
                )
                conseil = Conseil(texte, Confiance.MOYENNE, [], redirection_anader=False)
                return await self._journaliser(question, langue, conseil)
        else:
            clarif = clarification.analyser(question, historique)
            if clarif is not None:
                logger.info("clarification_demandee")
                conseil = Conseil(clarif, Confiance.MOYENNE, [], redirection_anader=False)
                return await self._journaliser(question, langue, conseil)
```

4) **Site flux** (`conseiller_stream`, ~302) : remplacer le bloc scripté par :

```python
        if self._dialogue_naturel:
            theme = clarification.detecter_theme(question, historique)
            if theme is not None:
                logger.info("clarification_demandee", mode="naturel", theme=theme)
                if await self._cache.hit_rate_limit(client_ip):
                    raise RateLimitDepasse
                texte = ""
                async for frag in conseil_commun.question_clarification_stream(
                    self._inference, theme, question, historique
                ):
                    texte += frag
                    yield {"type": "token", "text": frag}
                yield await self._evenement_final(
                    question, langue, texte, [], Confiance.MOYENNE, redirection=False
                )
                return
        else:
            clarif = clarification.analyser(question, historique)
            if clarif is not None:
                logger.info("clarification_demandee")
                yield {"type": "token", "text": clarif}
                yield await self._evenement_final(
                    question, langue, clarif, [], Confiance.MOYENNE, redirection=False
                )
                return
```

- [ ] **Step 4 : Vérifier le succès**

Run: `python -m pytest api/tests/test_conseil_service.py -v --no-cov`
Expected: PASS

- [ ] **Step 5 : Commit**

```bash
git add api/app/application/conseil_service.py api/tests/test_conseil_service.py
git commit -m "feat(conseil-v2): clarification naturelle derriere le drapeau"
```

---

### Task 7 : Brancher V3 (`Orchestrateur`) derrière le drapeau

**Files:**
- Modify: `api/app/application/orchestrateur.py` (constructeur ~61 ; sites clarification ~122 et ~262)
- Test: `api/tests/agents/test_orchestrateur.py`

**Interfaces:**
- Consumes : `conseil_commun.question_clarification` / `..._stream` (Task 5).
- Produces : `Orchestrateur.__init__(..., inference: InferencePort | None = None, dialogue_naturel: bool = False)` (nouveaux kwargs, en dernier). **L'orchestrateur ne détenait PAS de client d'inférence** (il passe par les agents) : on lui en injecte un, utilisé uniquement pour formuler la clarification quand le drapeau est actif.

- [ ] **Step 1 : Tests qui échouent**

Ajouter à `api/tests/agents/test_orchestrateur.py` (réutiliser les fakes du fichier ; inférence factice avec `generer(..., consigne=...)`), en miroir de Task 6 :

```python
async def test_orchestrateur_dialogue_naturel_on(orchestrateur_factory) -> None:
    orch = orchestrateur_factory(dialogue_naturel=True, reponse_inference="Sur quelle partie ?")
    conseil = await orch.traiter("Mes feuilles jaunissent", Langue.FR, "ip")
    assert "partie" in conseil.reponse
    assert "•" not in conseil.reponse


async def test_orchestrateur_dialogue_naturel_off(orchestrateur_factory) -> None:
    orch = orchestrateur_factory(dialogue_naturel=False)
    conseil = await orch.traiter("Mes feuilles jaunissent", Langue.FR, "ip")
    assert "•" in conseil.reponse
```

> Note : si `orchestrateur_factory` n'existe pas, l'ajouter comme fixture locale construisant l'`Orchestrateur` avec les fakes existants (routeur/registre, cache no-op, journal, sémantique neutre, inférence factice) et le kwarg `dialogue_naturel`.

- [ ] **Step 2 : Vérifier l'échec**

Run: `python -m pytest api/tests/agents/test_orchestrateur.py -k "dialogue_naturel" -v --no-cov`
Expected: FAIL (`dialogue_naturel` inconnu)

- [ ] **Step 3 : Implémenter**

1) Constructeur : ajouter les deux paramètres (en dernier) et les stocker :

```python
        inference: InferencePort | None = None,
        dialogue_naturel: bool = False,
```
```python
        self._inference = inference
        self._dialogue_naturel = dialogue_naturel
```

Importer le port en tête si besoin : `from app.domain.ports import InferencePort`. Le drapeau n'est effectif que si `self._inference is not None` (garde défensive : `dialogue_naturel and self._inference is not None`).

2) Import : `from app.application import conseil_commun` (si absent).

3) **Site sync** (`traiter`, ~122) : remplacer le bloc `clarif = clarification.analyser(...)` par la même structure `if self._dialogue_naturel: theme = detecter_theme(...) ... else: analyser(...)` que Task 6 — en construisant le `Conseil` et en passant par `self._journaliser`. Le rate-limit (`self._cache.hit_rate_limit(client_ip)`) précède l'appel `question_clarification` (il utilise l'inférence).

4) **Site flux** (`traiter_stream`, ~262) : idem en flux, en réutilisant `conseil_commun.question_clarification_stream` et `flux.evenement_final` (comme le fait déjà le bloc refus voisin).

- [ ] **Step 4 : Vérifier le succès**

Run: `python -m pytest api/tests/agents/test_orchestrateur.py -v --no-cov`
Expected: PASS

- [ ] **Step 5 : Commit**

```bash
git add api/app/application/orchestrateur.py api/tests/agents/test_orchestrateur.py
git commit -m "feat(orchestrateur-v3): clarification naturelle derriere le drapeau"
```

---

### Task 8 : Câblage (`api_deps` + `main`) et ConfigMap

**Files:**
- Modify: `api/app/api_deps.py` (`get_conseil_service` ~177-192 ; `get_orchestrateur` ~150-166)
- Modify: `deploy/k8s/api.yaml` (ConfigMap `api-config`)
- Test: `api/tests/test_api_deps.py`

**Interfaces:**
- Consumes : `Settings.dialogue_naturel_enabled` (Task 1), constructeurs `ConseilService`/`Orchestrateur` avec `dialogue_naturel` (Tasks 6-7).
- Produces : services construits avec le drapeau depuis les settings.

- [ ] **Step 1 : Test qui échoue**

Ajouter à `api/tests/test_api_deps.py` :

```python
def test_conseil_service_recoit_le_drapeau(monkeypatch) -> None:
    # Construire une Request factice + app.state comme les autres tests du fichier,
    # avec settings.dialogue_naturel_enabled = True, puis :
    from app.api_deps import get_conseil_service

    service = get_conseil_service(_request_factice(dialogue_naturel_enabled=True))
    assert service._dialogue_naturel is True
```

> Note : réutiliser le motif de `Request`/`app.state` déjà présent dans `test_api_deps.py`.

- [ ] **Step 2 : Vérifier l'échec**

Run: `python -m pytest api/tests/test_api_deps.py::test_conseil_service_recoit_le_drapeau -v --no-cov`
Expected: FAIL (`_dialogue_naturel` False car non câblé)

- [ ] **Step 3 : Implémenter**

1) Dans `api/app/api_deps.py`, `get_conseil_service` : passer le drapeau au constructeur :

```python
        dialogue_naturel=settings.dialogue_naturel_enabled,
```

2) Idem dans `get_orchestrateur` pour `Orchestrateur(...)` : passer AUSSI le client d'inférence (l'orchestrateur ne l'avait pas) :

```python
        inference=request.app.state.inference,
        dialogue_naturel=settings.dialogue_naturel_enabled,
```

(Récupérer `settings` via `get_app_settings()` / `request.app.state` selon le motif du fichier.)

3) Dans `deploy/k8s/api.yaml`, ajouter au ConfigMap `api-config`, section `data` :

```yaml
  DIALOGUE_NATUREL_ENABLED: "false"
```

- [ ] **Step 4 : Vérifier le succès**

Run: `python -m pytest api/tests/test_api_deps.py -v --no-cov`
Expected: PASS

- [ ] **Step 5 : Commit**

```bash
git add api/app/api_deps.py deploy/k8s/api.yaml api/tests/test_api_deps.py
git commit -m "feat(cablage): injecter dialogue_naturel_enabled (defaut false) + ConfigMap"
```

---

### Task 9 : Vérification complète + lint

**Files:** aucun nouveau (portes qualité).

- [ ] **Step 1 : Suite complète**

Run: `python -m pytest api/tests -q -p no:cacheprovider`
Expected: PASS, couverture ≥ 97 %.

- [ ] **Step 2 : Lint + format**

Run: `python -m ruff format api/ && python -m ruff check api/`
Expected: « All checks passed! »

- [ ] **Step 3 : Vérifier la parité off = comportement actuel**

Vérifier manuellement (lecture) que, drapeau `false`, `conseiller`/`traiter` empruntent exactement l'ancien chemin scripté (les tests `dialogue_naturel_off` le confirment).

- [ ] **Step 4 : Commit éventuel (corrections de lint)**

```bash
git add -A api/
git commit -m "chore: lint/format dialogue naturel"
```

---

## Déploiement (hors plan de code — pour mémoire)

1. Merger → 0.6.71 (drapeau `false` : aucun changement visible).
2. Valider le dialogue naturel (local, ou bascule temporaire `DIALOGUE_NATUREL_ENABLED=true` + redémarrage API sur le ConfigMap).
3. Activer : `DIALOGUE_NATUREL_ENABLED=true` en prod + redémarrage API.
4. Rollback doux : repasser à `false` + redémarrage (~15 s). Rollback dur : `roll-image.sh 0.6.70`.

## Auto-revue (à faire après rédaction)

- Couverture spec : config (T1), prompt réchauffé+strict (T2), détection/consigne (T3), inférence (T4), helper latence-bornée (T5), V2 (T6), V3 (T7), câblage+ConfigMap (T8), portes (T9). ✔
- Types cohérents : `detecter_theme`/`consigne_theme`/`besoin_localite`, `question_clarification[_stream]`, `dialogue_naturel`, `system_prompt`/`consigne`. ✔
- Pas de placeholder : code complet à chaque étape. ✔
