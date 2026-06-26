# Architecture — OpenCacao-7B

Ce document complète la section 3 de [`CLAUDE_OpenCacao.md`](../CLAUDE_OpenCacao.md).

## Vue d'ensemble

```
┌─────────────────────────────────────────────┐
│        CLIENT (SMS, WhatsApp, web)          │
└───────────────────────┬─────────────────────┘
                        │ HTTP
                        ▼
┌─────────────────────────────────────────────┐
│  api/  — FastAPI (port 8080)                │
│  • garde-fous métier (guardrails)           │
│  • cache + rate-limit (Redis)               │
│  • citations sources, disclaimer            │
└───────────────────────┬─────────────────────┘
                        │ HTTP interne (réseau opencacao-net)
                        ▼
┌─────────────────────────────────────────────┐
│  inference/ — vLLM (GPU) ou llama-cpp (CPU) │
│  API OpenAI-compatible, non exposée publiquement
└─────────────────────────────────────────────┘
```

## Principe de séparation

L'API publique ne fait **aucune** inférence elle-même. Elle orchestre :

1. **Garde-fous d'entrée** (`services/guardrails.py`) : si la question tombe sous
   une règle de refus (dosages phytosanitaires, médical/vétérinaire, identification
   de maladie sur image, **toute culture autre que le cacao**), elle renvoie une
   réponse de redirection ANADER **sans** appeler le modèle. Périmètre **cacao
   uniquement** : le vivrier (maïs, manioc…) et l'anacarde sont redirigés (juin 2026) ;
   seul l'ombrage/les cultures associées mentionnant le cacao restent traités.
2. **Cache** (`core/cache.py`) : réponses mises en cache dans Redis. La clé inclut
   `MODEL_VERSION` **et `APP_VERSION`** (tag de l'image) : un déploiement qui change le
   post-traitement n'invalide donc plus à tort le cache (et `roll-image.sh` purge
   `cache:chat:*` au rollout).
3. **Rate-limit** (`core/cache.py`) : 20 req/min/IP par défaut.
4. **RAG** (`services/rag.py`, optionnel) : récupération d'un vivier dense large →
   **reranking** par fusion similarité dense + recouvrement lexical (texte **et** nom de
   source du passage) → `top_k` passages injectés au prompt. Index NumPy en mémoire,
   embeddings via service interne — aucune base vectorielle externe.
5. **Inférence** (`services/inference.py`) : appel HTTP au service d'inférence via l'API
   OpenAI-compatible, avec le prompt système de `services/prompts.py`. `build_messages`
   garantit une **stricte alternance des rôles** (le template Ministral 3 l'exige, sinon
   500 → 503).
6. **Post-traitement** (`services/postprocess.py`) : disclaimer systématique + extraction
   des **sources depuis le texte** (référentiel `data/sources_agro.yaml`, incluant FIRCA,
   ICCO, FAO) et estimation de la confiance.

L'inférence n'est jamais joignable depuis l'extérieur : c'est ce qui garantit que
tous les garde-fous et la journalisation s'appliquent.

## Couches (api/app/)

- `routers/` — déclaration des endpoints, **aucune logique métier**.
- `services/` — logique métier (inférence, garde-fous, prompts).
- `models/` — schémas Pydantic (I/O) et types domaine.
- `core/` — configuration, logging structuré, client Redis.
- `data/` — référentiel statique des sources citées.

## Pipeline d'entraînement (hors ligne)

```
corpus/*.jsonl
   └→ train_lora.py (GPU 24 Go) → models/lora-adapter/
        └→ merge_and_export.py → models/opencacao-7b/
             └→ export GGUF → service llama-cpp (CPU)
```

L'entraînement n'est pas un service continu : il tourne ponctuellement sur GPU loué.
