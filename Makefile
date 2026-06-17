.PHONY: help corpus-check corpus-rag corpus-rag-collect corpus-cure corpus-assemble \
	train merge redeploy-model build up down test lint format

help:
	@echo "Cibles disponibles :"
	@echo "  corpus-check    Valide le corpus (format, garde-fous)"
	@echo "  corpus-rag      Construit le corpus Q/R via RAG (LLM local requis)"
	@echo "  corpus-rag-collect Télécharge + découpe les sources (sans LLM)"
	@echo "  corpus-cure     Récupère le corpus curé depuis le cluster"
	@echo "  corpus-assemble Assemble + valide + déduplique le corpus d'entraînement"
	@echo "  train           Lance l'entraînement LoRA (GPU)"
	@echo "  merge           Fusionne l'adaptateur LoRA + modèle de base"
	@echo "  redeploy-model  Redéploie un GGUF (GGUF=... [VERSION=...])"
	@echo "  build         Construit les images Docker du service"
	@echo "  demo-base     Démarre la démo flux complet (Mistral-7B de base, GPU)"
	@echo "  demo-base-cpu Démarre la démo flux complet (Mistral-7B de base, CPU/GGUF)"
	@echo "  up            Démarre le service (inference + api + redis)"
	@echo "  down          Arrête le service"
	@echo "  test          Lance les tests Python (pytest)"
	@echo "  lint          Vérifie le style (ruff check)"
	@echo "  format        Formate le code (ruff format)"

corpus-check:
	python training/scripts/enrich_corpus.py --check corpus/corpus_cacao_demarrage.jsonl

# Récupère le corpus curé (réponses validées via la console) depuis le cluster.
corpus-cure:
	bash training/scripts/fetch_curation.sh

# Combine sources + corpus curé en un corpus d'entraînement validé/dédupliqué.
corpus-assemble:
	python training/scripts/assemble_corpus.py \
		--sources corpus/corpus_cacao_rag.jsonl corpus/corpus_cacao_demarrage.jsonl \
			corpus/corpus_refus.jsonl corpus/corpus_cure.jsonl \
		--out corpus/corpus_entrainement.jsonl

# Construit le corpus à partir des documents officiels (cf. docs/corpus_rag_guide.md).
# Nécessite un LLM local OpenAI-compatible : CORPUS_LLM_BASE_URL, CORPUS_LLM_MODEL.
corpus-rag:
	python training/scripts/build_corpus_rag.py --target $(or $(TARGET),5000) \
		--out corpus/corpus_cacao_rag.jsonl

corpus-rag-collect:
	python training/scripts/build_corpus_rag.py --collect-only

train:
	docker compose -f docker-compose.training.yml up --build

merge:
	python training/scripts/merge_and_export.py \
		--base mistralai/Ministral-3-8B-Instruct-2512-BF16 \
		--adapter models/lora-adapter \
		--output models/opencacao-8b

# Redéploie un nouveau GGUF sur le cluster (purge le cache, recharge l'inférence).
#   make redeploy-model GGUF=models/opencacao-8b-Q4_K_M.gguf VERSION=1.1.0
redeploy-model:
	bash deploy/redeploy_model.sh $(GGUF) $(VERSION)

build:
	docker compose build

up:
	docker compose up -d

demo-base:
	docker compose -f docker-compose.base.yml up --build

demo-base-cpu:
	docker compose -f docker-compose.base-cpu.yml up --build

down:
	docker compose down
	docker compose -f docker-compose.base.yml down
	docker compose -f docker-compose.base-cpu.yml down

test:
	cd api && pytest
	pytest training/tests -o addopts=""

lint:
	cd api && ruff check .
	ruff check training

format:
	cd api && ruff format .
