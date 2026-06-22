.PHONY: help corpus-check corpus-rag corpus-rag-collect corpus-cure corpus-assemble \
	rag-index train merge eval eval-juge redeploy-model build up down test lint format

help:
	@echo "Cibles disponibles :"
	@echo "  corpus-check    Valide le corpus (format, garde-fous)"
	@echo "  corpus-rag      Construit le corpus Q/R via RAG (LLM local requis)"
	@echo "  corpus-rag-collect Télécharge + découpe les sources (sans LLM)"
	@echo "  corpus-cure     Récupère le corpus curé depuis le cluster"
	@echo "  corpus-assemble Assemble + valide + déduplique le corpus d'entraînement"
	@echo "  rag-index       Construit l'index RAG (embeddings) depuis le corpus"
	@echo "  train           Lance l'entraînement LoRA (GPU)"
	@echo "  merge           Fusionne l'adaptateur LoRA + modèle de base"
	@echo "  eval            Évalue le modèle servi (garde-fous + qualité) — ENDPOINT=... MODEL=..."
	@echo "  eval-juge       eval + juge LLM externe GLM-5.2/Z.ai (hors prod, ZAI_API_KEY requise)"
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

# Construit l'index RAG (embeddings). Service d'embeddings requis (EMBEDDINGS_URL).
rag-index:
	python training/scripts/build_rag_index.py \
		--sources corpus/corpus_cacao_rag.jsonl corpus/corpus_cacao_demarrage.jsonl \
			corpus/corpus_cure.jsonl \
		--embeddings-url $(or $(EMBEDDINGS_URL),http://localhost:8001) \
		--out rag_index.jsonl

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

# Évalue le modèle servi sur le jeu de tests figé (garde-fous + qualité).
#   make eval ENDPOINT=http://localhost:8000 MODEL=opencacao-8b
eval:
	python training/scripts/evaluate.py \
		--endpoint $(or $(ENDPOINT),http://localhost:8000) \
		--model $(or $(MODEL),opencacao-8b)

# Idem + juge LLM externe (GLM-5.2 via Z.ai) sur les cas de qualité — HORS PROD.
# Nécessite ZAI_API_KEY ; mesure la pertinence/fidélité au-delà des heuristiques.
#   make eval-juge ENDPOINT=http://localhost:8000 MODEL=opencacao-8b
eval-juge:
	python training/scripts/evaluate.py \
		--endpoint $(or $(ENDPOINT),http://localhost:8000) \
		--model $(or $(MODEL),opencacao-8b) \
		--juge

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
