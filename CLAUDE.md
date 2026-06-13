# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Source unique de vérité

**`CLAUDE_OpenCacao.md`** est la spécification technique complète et fait autorité sur tout ce qui n'est pas couvert ici (architecture détaillée, conventions, garde-fous métier, pipeline d'entraînement, schémas API, gouvernance). La lire avant toute génération de code. Ne pas la modifier sans validation explicite de Waopron Coulibaly.

## État actuel

Le dépôt ne contient pour l'instant **que la spec** — aucun code, `docker-compose.yml`, `Makefile` ou `pyproject.toml` n'existe encore. L'arborescence décrite dans la spec (§9.1) est la cible à créer, pas l'état présent. Construire les fichiers conformément à la spec plutôt que de présumer leur existence.

## Projet

OpenCacao-7B : assistant de conseil agronomique pour les producteurs de cacao ivoiriens, basé sur **Mistral 7B Instruct v0.3** affiné par **LoRA 4-bit**, servi via une API FastAPI. Démonstration technique du livre blanc *« IA souveraine pour la Côte d'Ivoire »* (OpenLab Consulting). Souveraineté, reproductibilité, modestie — pas de produit commercial.

## Architecture (résumé)

Trois conteneurs principaux + Redis, en couches strictes :

- **`api/`** — FastAPI public. Endpoints versionnés `/v1/`. **Aucune logique métier dans les routers** : tout passe par `app/services/` (`inference.py` client vers l'inférence, `guardrails.py` garde-fous, `prompts.py` templates système). Validation Pydantic v2 sur entrées ET sorties.
- **`inference/`** — vLLM (GPU, préféré) ou llama-cpp (CPU, fallback GGUF). **Jamais exposé publiquement** : l'API le consomme en interne pour pouvoir appliquer garde-fous + journalisation.
- **`training/`** — pipeline ponctuel sur GPU loué (24 Go). `corpus/*.jsonl` → `train_lora.py` → adaptateur LoRA → `merge_and_export.py` → `models/opencacao-7b/` → export GGUF.
- **`redis`** — cache de réponses + rate-limit (20 req/min/IP par défaut).

Flux requête : client → api (garde-fous, cache) → inference interne → réponse avec sources + disclaimer.

## Garde-fous métier — non négociables

`api/app/services/guardrails.py` doit **refuser systématiquement** et rediriger vers ANADER : dosages phytosanitaires précis, demandes médicales/vétérinaires, identification de maladie sur image sans agent, hors-filière cacao (anacarde/vivrier tolérés). Un test par règle de refus est obligatoire. **Ne jamais générer de dosages phytosanitaires, même en exemple de test.** Chaque réponse modèle inclut le disclaimer ANADER.

## Commandes (cibles Makefile à créer — voir spec §9.5)

```
make corpus-check   # valide le corpus (scripts/enrich_corpus.py)
make train          # entraînement LoRA (GPU)
make merge          # fusion LoRA + modèle de base
make build          # construit les images Docker
make up / make down # démarre / arrête le service
make test           # pytest
make lint           # ruff check
make format         # ruff format
```

Tests : `pytest` + `pytest-asyncio`, couverture min. 80 % sur `api/app/`, inférence mockée (pas d'appel réseau réel en CI). Lancer un seul test : `pytest api/tests/test_guardrails.py::nom_du_test`.

## Conventions

- Python 3.11+, typage systématique (`from __future__ import annotations`), pas de variables globales mutables.
- `ruff format` + `ruff check` (config dans `pyproject.toml`). Imports triés par ruff.
- Logging structuré JSON via `structlog` — **jamais `print()`**.
- Dépendances uniquement depuis les versions épinglées de la spec §2.1. Aucun framework hors liste.
- **Aucun service externe** (OpenAI, Anthropic, Cohere) dans le pipeline de production — toléré uniquement pour l'enrichissement initial du corpus, et clairement signalé.
- Code complet et exécutable, pas de stubs ni `TODO`. Docstrings format Google.
