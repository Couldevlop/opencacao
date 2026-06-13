# CLAUDE.md — OpenCacao-7B

> Spécification technique complète pour Claude Code en mode agentique.
> Ce document est la **source unique de vérité** du projet OpenCacao. Toute génération de code, de configuration ou de documentation par Claude Code doit s'y conformer.

---

## 1. Contexte et finalité

### 1.1 Présentation du projet

**OpenCacao-7B** est un assistant de conseil agronomique pour les producteurs de cacao de Côte d'Ivoire, fondé sur un modèle de langue open-source (Ministral 3 8B Instruct) affiné par fine-tuning LoRA sur un corpus de la filière cacao ivoirienne, et publié en accès libre sous licence permissive.

Le projet accompagne le livre blanc *« IA souveraine pour la Côte d'Ivoire »* (Waopron Coulibaly, OpenLab Consulting, 2026) en tant que **démonstration technique** du chantier A2 — *Le conseil agronomique pour tous les producteurs*. Il matérialise la thèse défendue dans l'ouvrage : une équipe ivoirienne peut produire, avec des moyens modestes, une intelligence artificielle souveraine et utile.

### 1.2 Statut du projet

- **Nature** : démonstration technique, non un produit commercial.
- **Public visé pour la démonstration** : Ministère de la Transition Numérique et de l'Innovation Technologique, Conseil du Café-Cacao, ANADER, CNRA, et tout acteur de la filière.
- **Licence** : code MIT, corpus et poids LoRA sous licence ouverte (CC BY-NC-SA 4.0 pour le corpus, Apache 2.0 pour les poids dérivés, en cohérence avec la licence de Ministral 3).
- **Disclaimer impératif** : OpenCacao est un outil d'aide à la décision. Il ne remplace ni l'agronome, ni l'encadrement de terrain de l'ANADER, ni les recommandations officielles du Conseil du Café-Cacao. Cette mention figure dans chaque réponse du modèle et dans toute interface utilisateur.

### 1.3 Principes directeurs (non négociables)

1. **Souveraineté** : tout le pipeline doit pouvoir tourner sur infrastructure contrôlée localement. Aucune dépendance à un service propriétaire externe en production.
2. **Vérité** : aucune affirmation chiffrée d'impact (« +X % de rendement ») ne sera produite sans validation terrain. Les réponses citent leurs sources lorsque possible et reconnaissent leurs limites.
3. **Reproductibilité** : tout est conteneurisé, toutes les versions sont épinglées, toutes les commandes sont documentées.
4. **Modestie** : la qualité d'un modèle 7B fine-tuné sur un petit corpus a des limites assumées. On ne sur-promet pas.
5. **Garde-fous métier** : refus systématique de fournir des recommandations sur dosages de produits phytosanitaires sans renvoyer vers l'agent ANADER local.

---

## 2. Stack technique

### 2.1 Versions épinglées

| Composant | Version | Rôle |
|---|---|---|
| Python | 3.11 | Runtime |
| PyTorch | ≥ 2.5 | Tenseurs et entraînement |
| Transformers (HF) | ≥ 4.50 | Chargement du modèle (support Ministral 3) |
| PEFT | ≥ 0.13 | LoRA |
| bitsandbytes | ≥ 0.44 | Quantification 4-bit |
| TRL | ≥ 0.12 | SFTTrainer |
| Datasets | ≥ 3.0 | Pipeline corpus |
| Accelerate | ≥ 1.0 | Optimisation entraînement |
| mistral-common | ≥ 1.8.6 | Tokenizer Ministral 3 |
| FastAPI | 0.115.x | API HTTP |
| Uvicorn | 0.32.x | Serveur ASGI |
| Pydantic | 2.x | Validation |
| vLLM | ≥ 0.12 | Service d'inférence (GPU, support Ministral 3) |
| llama-cpp-python | ≥ 0.3 | Service d'inférence (CPU, fallback GGUF) |
| Docker | 24+ | Conteneurisation |
| Docker Compose | v2 | Orchestration |

### 2.2 Modèle de base

- **Ministral 3 8B Instruct** (`mistralai/Ministral-3-8B-Instruct-2512`, déc. 2025)
- Licence : Apache 2.0
- Taille : 8 milliards de paramètres
- Contexte : jusqu'à 256 k tokens
- Téléchargement : Hugging Face (token requis pour le pull initial)
- Note : migration depuis Mistral 7B Instruct v0.3 (validée) pour disposer du
  modèle ouvert souverain le plus récent. Exige un stack récent (transformers
  ≥ 4.50, vLLM ≥ 0.12, `mistral-common` ≥ 1.8.6).

### 2.3 Choix techniques justifiés

- **LoRA 4-bit** plutôt que fine-tuning complet : permet l'entraînement sur un GPU 24 Go (RTX 4090, A10G) au lieu d'un cluster multi-A100.
- **Ministral 3 8B** (modèle ouvert Mistral le plus récent, déc. 2025) plutôt qu'un modèle plus gros : tient en VRAM modeste (≈20 Go pour l'affinage 4-bit, moins à l'inférence quantifiée), et reste sous Apache 2.0 (souveraineté commerciale possible).
- **vLLM** pour servir : performance d'inférence très supérieure aux serveurs naïfs, supporte les batchs continus.
- **llama-cpp** en fallback CPU : permet de servir le modèle quantifié (GGUF) sur du matériel sans GPU pour la démo locale.

---

## 3. Architecture

### 3.1 Vue d'ensemble

```
┌─────────────────────────────────────────────────────────────┐
│                  CLIENT (SMS, WhatsApp, web)                │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTP
                         ▼
┌─────────────────────────────────────────────────────────────┐
│         api/  —  FastAPI  (conteneur Python)                │
│  • /v1/chat          : conseil agronomique                  │
│  • /v1/health        : santé du service                     │
│  • garde-fous métier, journalisation, citations sources     │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTP interne
                         ▼
┌─────────────────────────────────────────────────────────────┐
│   inference/  —  vLLM ou llama-cpp  (conteneur séparé)      │
│   Sert opencacao-7b (modèle fine-tuné fusionné)             │
└─────────────────────────────────────────────────────────────┘

PIPELINE D'ENTRAÎNEMENT (exécuté ponctuellement, sur GPU loué) :
─────────────────────────────────────────────────────────────
training/                    (conteneur GPU séparé)
  corpus/*.jsonl  →  train_lora.py  →  adaptateur LoRA
  adaptateur LoRA + Ministral 3 8B  →  merge_and_export.py  →  opencacao-7b/
  opencacao-7b/  →  export GGUF  →  service llama-cpp
```

### 3.2 Découpage en conteneurs

| Conteneur | Image | Cible | Quand il tourne |
|---|---|---|---|
| `opencacao-training` | `pytorch/pytorch:2.3.1-cuda12.1-cudnn8-runtime` + scripts | GPU (loué) | Ponctuel — phase de fine-tuning |
| `opencacao-inference` | `vllm/vllm-openai:0.6.x` (GPU) **ou** `ghcr.io/abetlen/llama-cpp-python:0.3.x` (CPU) | Démo / production | En continu |
| `opencacao-api` | image construite depuis `api/Dockerfile` (FastAPI) | Démo / production | En continu |
| `opencacao-redis` | `redis:7-alpine` | Cache de réponses + rate-limit | En continu |

### 3.3 Architecture en couches (api/)

```
api/
├── app/
│   ├── main.py              FastAPI entry point
│   ├── routers/
│   │   ├── chat.py          POST /v1/chat
│   │   └── health.py        GET  /v1/health
│   ├── services/
│   │   ├── inference.py     Client vers le service inference
│   │   ├── guardrails.py    Garde-fous métier (refus phytosanitaires, etc.)
│   │   └── prompts.py       Templates prompts système
│   ├── models/
│   │   ├── chat.py          Pydantic — requête et réponse
│   │   └── domain.py        Types métier (Question, Reponse, NiveauUrgence)
│   ├── core/
│   │   ├── config.py        Settings (env vars)
│   │   ├── logging.py       Logging structuré JSON
│   │   └── cache.py         Client Redis
│   └── data/
│       └── sources_agro.yaml   Référentiel des sources citées
└── tests/
    ├── test_chat.py
    ├── test_guardrails.py
    └── conftest.py
```

---

## 4. Conventions de code

### 4.1 Python général

- Python 3.11+, typage statique systématique (`from __future__ import annotations`).
- Formatage : `ruff format` (équivalent black). Linting : `ruff check`. Configuration dans `pyproject.toml`.
- Imports triés : `ruff` (isort).
- Aucune dépendance non listée dans `requirements.txt` ou `pyproject.toml`.
- Pas de variables globales mutables.
- Logging structuré JSON via `structlog` — jamais `print()`.

### 4.2 FastAPI

- Validation par Pydantic v2 systématique sur entrées **et** sorties.
- Endpoints versionnés sous `/v1/`.
- Codes HTTP corrects : 200, 400 (entrée invalide), 422 (validation Pydantic), 429 (rate-limit), 503 (inference indisponible).
- Documentation OpenAPI activée par défaut.
- Pas de logique métier dans les routers — tout passe par `services/`.

### 4.3 Garde-fous métier (à respecter dans le code)

Dans `services/guardrails.py`, refus systématique avec orientation vers ANADER pour :
- Demandes de dosages précis de produits phytosanitaires.
- Demandes médicales ou vétérinaires.
- Demandes d'identification de maladie sur image sans confirmation visuelle d'un agent.
- Toute demande hors filière cacao (sauf cultures connexes : anacarde, vivrier).

Format de refus standard, inscrit comme constante :
```python
REFUS_PHYTO = (
    "Pour des dosages précis de produits phytosanitaires, je vous oriente "
    "vers votre agent ANADER local ou la délégation du Conseil du Café-Cacao "
    "de votre zone. Je peux en revanche vous renseigner sur les bonnes "
    "pratiques générales et la reconnaissance des symptômes."
)
```

### 4.4 Tests

- `pytest` + `pytest-asyncio`.
- Couverture minimale 80 % sur `api/app/`.
- Tests d'intégration avec un mock du service d'inférence — pas d'appel réseau réel en CI.
- Tests de garde-fous obligatoires : un test par règle de refus.

---

## 5. Pipeline de données

### 5.1 Format du corpus

JSONL, une ligne par exemple, format **instruction-tuning** :

```jsonl
{"instruction": "Mes feuilles de cacaoyer jaunissent et les rameaux gonflent. Que faire ?", "input": "", "output": "Ces symptômes — jaunissement des feuilles et gonflement des rameaux — sont caractéristiques de la maladie virale du swollen shoot (CSSV). [...] Je vous recommande de contacter immédiatement votre agent ANADER pour confirmation visuelle et orientation. Sources : Conseil du Café-Cacao, CNRA."}
```

### 5.2 État du corpus

- **Corpus de démarrage** : 20 paires Q/R (`corpus/corpus_cacao_demarrage.jsonl`)
- **Objectif** : 500+ paires Q/R validées avant tout entraînement sérieux
- **Sources de la collecte (priorité par ordre)** :
  1. Données publiques CNRA, ANADER, Conseil Café-Cacao, FAO Côte d'Ivoire
  2. Partenariats institutionnels (à négocier)
  3. Corpus participatif (questions réelles de producteurs, réponses validées)
  4. Enrichissement terrain par agronomes

### 5.3 Validation du corpus

Chaque paire ajoutée passe par `scripts/enrich_corpus.py` qui vérifie :
- Format JSON valide.
- Champs obligatoires présents.
- Longueur d'instruction entre 10 et 500 caractères.
- Longueur de réponse entre 50 et 2000 caractères.
- Absence de dosages chiffrés de produits phytosanitaires (filtre par regex).
- Présence d'au moins une source citée pour les réponses techniques.

### 5.4 Découpage train / validation

- 90 % entraînement, 10 % validation.
- Seed fixé à 42 pour la reproductibilité.

---

## 6. Pipeline d'entraînement

### 6.1 Configuration LoRA (épinglée)

Dans `training/train_lora.py` :

```python
LORA_CONFIG = {
    "r": 16,
    "lora_alpha": 32,
    "lora_dropout": 0.05,
    "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj",
                       "gate_proj", "up_proj", "down_proj"],
    "bias": "none",
    "task_type": "CAUSAL_LM",
}

QUANTIZATION_CONFIG = {
    "load_in_4bit": True,
    "bnb_4bit_compute_dtype": "bfloat16",
    "bnb_4bit_quant_type": "nf4",
    "bnb_4bit_use_double_quant": True,
}

TRAINING_ARGS = {
    "num_train_epochs": 3,
    "per_device_train_batch_size": 4,
    "gradient_accumulation_steps": 4,
    "learning_rate": 2e-4,
    "warmup_ratio": 0.03,
    "lr_scheduler_type": "cosine",
    "logging_steps": 10,
    "save_strategy": "epoch",
    "evaluation_strategy": "epoch",
    "bf16": True,
    "optim": "paged_adamw_8bit",
    "report_to": "none",
}
```

### 6.2 Exécution sur GPU loué

L'entraînement n'est **pas** un service en continu — il s'exécute ponctuellement quand le corpus a été enrichi.

Procédure :
1. Louer un GPU 24 Go (RunPod, Vast.ai, Lambda Labs) — coût indicatif 1-2 USD/h.
2. `git clone` du dépôt sur l'instance.
3. `docker compose -f docker-compose.training.yml up` (image avec CUDA).
4. Récupération de l'adaptateur LoRA produit (~150 Mo) en local.
5. Fusion locale ou sur la même instance avec `scripts/merge_and_export.py` → modèle `opencacao-7b/`.
6. Export GGUF quantifié pour service CPU si nécessaire.
7. Arrêt et libération de l'instance GPU.

Durée typique pour 500 paires sur 3 époques : 30 à 90 minutes selon le GPU.

---

## 7. Service d'inférence

### 7.1 Mode GPU (préféré)

Image `vllm/vllm-openai:0.6.x`, expose l'API OpenAI-compatible sur `:8000`.

Le service `opencacao-api` consomme cette API en interne — il n'expose **pas** vLLM directement au public, pour pouvoir appliquer les garde-fous et la journalisation.

### 7.2 Mode CPU (fallback)

Si pas de GPU disponible pour la démo, on sert le modèle quantifié GGUF (Q4_K_M) via `llama-cpp-python`. Performance dégradée (5-15 tokens/s sur CPU x86 récent) mais utilisable pour une démo qualitative.

### 7.3 Templates de prompt

`api/app/services/prompts.py` contient les templates système, en français, qui forcent :
- Le rôle (« assistant agronomique pour les producteurs de cacao ivoiriens »).
- L'usage d'un français accessible et bienveillant.
- L'orientation vers ANADER pour les cas hors champ.
- La citation des sources lorsque possible.

---

## 8. API publique

### 8.1 Endpoints

| Méthode | Chemin | Description |
|---|---|---|
| POST | `/v1/chat` | Pose une question agronomique, reçoit une réponse |
| GET | `/v1/health` | Liveness probe (200 si OK) |
| GET | `/v1/ready` | Readiness probe (200 si inference dispo) |
| GET | `/v1/version` | Version du modèle et de l'API |

### 8.2 Schéma de requête `/v1/chat`

```json
{
  "question": "Mes feuilles jaunissent, que faire ?",
  "langue": "fr",
  "canal": "sms"
}
```

### 8.3 Schéma de réponse

```json
{
  "reponse": "Ces symptômes...",
  "sources": ["Conseil du Café-Cacao", "CNRA"],
  "confiance": "moyenne",
  "redirection_anader": false,
  "disclaimer": "OpenCacao est un outil d'aide. Pour confirmation, contactez votre agent ANADER."
}
```

### 8.4 Rate-limit

Via Redis : 20 requêtes/minute/IP par défaut, configurable par variable d'environnement.

---

## 9. Conteneurisation

### 9.1 Arborescence du dépôt

```
opencacao/
├── CLAUDE.md                     ← ce document
├── README.md
├── LICENSE
├── docker-compose.yml            ← service (inference + api + redis)
├── docker-compose.training.yml   ← entraînement (GPU)
├── docker-compose.cpu.yml        ← variante CPU (llama-cpp)
├── .env.example
├── api/
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── app/                      (voir 3.3)
│   └── tests/
├── training/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── scripts/
│       ├── train_lora.py
│       ├── merge_and_export.py
│       └── enrich_corpus.py
├── corpus/
│   └── corpus_cacao_demarrage.jsonl
├── models/                       (gitignored — généré)
│   └── opencacao-7b/
├── docs/
│   ├── Modelfile.opencacao       (pour Ollama)
│   ├── architecture.md
│   └── training_guide.md
└── Makefile
```

### 9.2 `docker-compose.yml` (service principal)

Services :
- `inference` — vLLM ou llama-cpp selon la cible, lit `models/opencacao-7b/`
- `api` — FastAPI, dépend de inference et redis
- `redis` — cache et rate-limit

Réseau interne `opencacao-net`. Aucun port exposé publiquement à part `api:8080`.

Volumes :
- `./models:/models:ro` — modèle en lecture seule
- `./corpus:/corpus:ro` — pour debug uniquement

Variables d'environnement (via `.env`) :
- `INFERENCE_BACKEND` : `vllm` ou `llama-cpp`
- `MODEL_PATH` : `/models/opencacao-7b`
- `REDIS_URL` : `redis://redis:6379`
- `RATE_LIMIT_PER_MIN` : `20`
- `LOG_LEVEL` : `INFO`

### 9.3 `docker-compose.training.yml` (entraînement)

Service unique `training` avec `runtime: nvidia` et `deploy.resources.reservations.devices` pour GPU. Lit `corpus/` et écrit dans `models/lora-adapter/`.

### 9.4 Dockerfile API (api/Dockerfile)

Multi-stage : builder avec `uv pip install`, runner sur `python:3.11-slim`, `USER` non-root, healthcheck intégré.

### 9.5 Makefile

Cibles minimales :
- `make corpus-check` — valide le corpus
- `make train` — lance l'entraînement (GPU)
- `make merge` — fusionne LoRA + base
- `make build` — construit les images
- `make up` / `make down` — démarre/arrête le service
- `make test` — tests Python
- `make lint` — ruff
- `make format` — ruff format

---

## 10. Canaux de diffusion (post-MVP)

Hors du périmètre du Compose initial, mais préparé par l'architecture :

- **SMS** via Africa's Talking ou Orange API CI — adaptateur dédié dans `api/app/services/`
- **WhatsApp Business API** — webhook FastAPI
- **Application web minimale** — page statique servie séparément

Ces canaux ne sont **pas** inclus dans la première livraison Compose. La livraison initiale expose uniquement l'API HTTP — les intégrations canaux sont des chantiers ultérieurs.

---

## 11. Sécurité et conformité

### 11.1 Données

- Aucune collecte de données nominatives par défaut.
- Les questions soumises peuvent être journalisées **anonymisées** pour amélioration du corpus, après accord explicite (configurable).
- Les journaux sont rotatifs (logrotate) et purgés après 90 jours.

### 11.2 Modèle

- Le modèle fusionné est versionné par hash SHA-256 et signé.
- Les poids LoRA ne sont publiés qu'après validation par un relecteur agronome de référence.

### 11.3 API

- Pas d'authentification sur la démo publique, mais rate-limit par IP.
- Headers de sécurité : `X-Content-Type-Options`, `X-Frame-Options`.
- CORS restreint aux origines connues en production.

---

## 12. Cycle de vie et versionnement

- Versionnement sémantique : `MAJOR.MINOR.PATCH`.
- Première version publique cible : `0.1.0` (démonstration de faisabilité).
- Tag Git par version, image Docker taguée en miroir.
- Le numéro de version est exposé par `/v1/version` et inclus dans chaque réponse.

---

## 13. Ce que Claude Code doit faire (et ne pas faire)

### À faire

- Générer du code **complet et exécutable**, pas de stubs ni de `TODO`.
- Respecter intégralement les conventions de code de la section 4.
- Implémenter tous les garde-fous métier de la section 4.3 avec leurs tests.
- Documenter chaque fonction publique (docstrings format Google).
- Produire un `README.md` clair avec les commandes de démarrage.
- Tester localement le pipeline avec un corpus minimal avant de prétendre que ça marche.

### À ne pas faire

- Pas de framework non listé dans la section 2.1.
- Pas de service externe (OpenAI, Anthropic, Cohere) dans le pipeline de production — uniquement pour l'enrichissement initial du corpus, et clairement signalé.
- Pas de promesse d'impact chiffrée non sourcée dans la documentation.
- Pas de génération de dosages phytosanitaires, même à titre d'exemple dans les tests.
- Pas de modification de ce `CLAUDE.md` sans validation explicite de Waopron.

---

## 14. Contacts et gouvernance

- **Mainteneur principal** : Waopron Coulibaly — waopron@openlabconsulting.com
- **Organisation** : OpenLab Consulting, Abidjan
- **Référents agronomiques** (à confirmer) : CNRA, ANADER, Conseil du Café-Cacao
- **Cadre éditorial** : le projet s'inscrit dans le livre blanc *« IA souveraine pour la Côte d'Ivoire »*, dont il est la démonstration technique.

---

*Document de spécification — Version 1.0 — Mai 2026 — OpenLab Consulting*
