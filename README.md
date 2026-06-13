# OpenCacao-7B

Assistant de conseil agronomique pour les producteurs de cacao de Côte d'Ivoire,
fondé sur **Mistral 7B Instruct v0.3** affiné par fine-tuning **LoRA 4-bit** sur un
corpus de la filière cacao ivoirienne.

> Démonstration technique du livre blanc *« IA souveraine pour la Côte d'Ivoire »*
> (Waopron Coulibaly, OpenLab Consulting, 2026). Ce n'est **pas** un produit commercial.

**Disclaimer** — OpenCacao est un outil d'aide à la décision. Il ne remplace ni
l'agronome, ni l'encadrement de terrain de l'ANADER, ni les recommandations
officielles du Conseil du Café-Cacao.

La spécification technique complète fait autorité : voir [`CLAUDE_OpenCacao.md`](CLAUDE_OpenCacao.md).

## Architecture

```
client → api/ (FastAPI, garde-fous, cache) → inference/ (vLLM ou llama-cpp) → réponse
                                  └→ redis (cache + rate-limit)
```

L'inférence n'est jamais exposée publiquement : seule l'API HTTP l'est, pour
appliquer les garde-fous métier et la journalisation.

## Démarrage rapide (service)

Prérequis : Docker 24+ et Docker Compose v2. Un modèle fusionné doit exister dans
`models/opencacao-7b/` (voir entraînement ci-dessous).

```bash
cp .env.example .env          # ajuster INFERENCE_BACKEND, etc.
make build                    # construit les images
make up                       # démarre inference + api + redis
curl http://localhost:8080/v1/health
```

Variante CPU (sans GPU, modèle GGUF quantifié) :

```bash
docker compose -f docker-compose.cpu.yml up -d
```

### Valider le flux avant entraînement (modèle de base)

Pour tester l'API de bout en bout **sans modèle affiné**, on sert le Mistral 7B
de base depuis Hugging Face sous le même nom logique (`opencacao-7b`). Prérequis :
GPU NVIDIA ~16 Go et un token Hugging Face (`HF_TOKEN` dans `.env`, modèle gated).

```bash
make demo-base                # docker-compose.base.yml (GPU, télécharge ~15 Go au 1er run)
```

Le premier démarrage télécharge les poids (plusieurs minutes) : `/v1/chat` renvoie
503 tant que vLLM n'a pas fini de charger — `GET /v1/ready` reflète l'état.

> Bascule vers le modèle affiné : comme le `served-model-name` est identique
> (`opencacao-7b`), passer de cette démo à `make up` (LoRA fusionné) ne change que
> les poids servis — aucune modification de l'API, des garde-fous ou des tests.

### Valider le flux en CPU (sans GPU)

Pour une machine sans GPU NVIDIA, on sert le Mistral 7B de base quantifié (GGUF)
via llama-cpp. Aucun token HF requis (quantification communautaire). Lent
(3-8 tokens/s) mais suffisant pour valider le flux.

```bash
# 1. télécharger le GGUF (~4,4 Go) dans models/
pip install -U "huggingface_hub[cli]"
huggingface-cli download bartowski/Mistral-7B-Instruct-v0.3-GGUF \
  Mistral-7B-Instruct-v0.3-Q4_K_M.gguf --local-dir models

# 2. démarrer
make demo-base-cpu            # docker-compose.base-cpu.yml
```

### Exemple d'appel

```bash
curl -X POST http://localhost:8080/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "Mes feuilles de cacaoyer jaunissent, que faire ?", "langue": "fr", "canal": "web"}'
```

## Développement de l'API

```bash
cd api
pip install -e ".[dev]"       # ou: uv pip install -e ".[dev]"
ruff check . && ruff format --check .
pytest                        # couverture min. 80 % sur app/
```

L'inférence est mockée en test (pas d'appel réseau réel).

## Entraînement (ponctuel, GPU 24 Go loué)

```bash
make corpus-check             # valide le corpus
make train                    # docker-compose.training.yml (GPU)
make merge                    # fusionne LoRA + base → models/opencacao-7b/
```

Détails : [`docs/training_guide.md`](docs/training_guide.md).

## Licences

- Code : MIT
- Corpus : CC BY-NC-SA 4.0
- Poids LoRA dérivés : Apache 2.0 (cohérent avec Mistral 7B)
