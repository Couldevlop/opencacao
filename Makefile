.PHONY: help corpus-check corpus-rag corpus-rag-collect corpus-cure corpus-assemble \
	corpus-souverain rag-index train merge eval eval-juge redeploy-model build up down \
	test lint format

help:
	@echo "Cibles disponibles :"
	@echo "  corpus-check    Valide le corpus (format, garde-fous)"
	@echo "  corpus-rag      Construit le corpus Q/R via RAG (LLM local requis)"
	@echo "  corpus-rag-collect Télécharge + découpe les sources (sans LLM)"
	@echo "  corpus-souverain Génère le corpus via un modèle-maître ouvert auto-hébergé (GPU, souverain)"
	@echo "  corpus-cure     Récupère le corpus curé depuis le cluster"
	@echo "  corpus-assemble Assemble + valide + déduplique le corpus d'entraînement"
	@echo "  rag-index       Construit l'index RAG (embeddings) depuis le corpus"
	@echo "  train           Lance l'entraînement LoRA (GPU)"
	@echo "  merge           Fusionne l'adaptateur LoRA + modèle de base"
	@echo "  eval            Évalue le modèle servi (garde-fous + qualité) — ENDPOINT=... MODEL=..."
	@echo "  eval-juge       eval + juge LLM externe GLM-5.2/Z.ai (hors prod, ZAI_API_KEY requise)"
	@echo "  redeploy-model  Redéploie un GGUF (GGUF=... [VERSION=...])"
	@echo "  build         Construit les images Docker du service"
	@echo "  demo-base     Démarre la démo flux complet (Ministral 3 8B de base, GPU)"
	@echo "  demo-base-cpu Démarre la démo flux complet (Ministral 3 8B de base, CPU/GGUF)"
	@echo "  up            Démarre le service (inference + api + redis)"
	@echo "  down          Arrête le service"
	@echo "  test          Lance tous les tests (API + front)"
	@echo "  test-api      Tests Python (pytest)"
	@echo "  test-web      Tests du front (node --test, sans dépendance)"
	@echo "  lint          Vérifie le style (ruff check)"
	@echo "  format        Formate le code (ruff format)"

corpus-check:
	python training/scripts/enrich_corpus.py --check corpus/corpus_cacao_demarrage.jsonl

# Récupère le corpus curé (réponses validées via la console) depuis le cluster.
corpus-cure:
	bash training/scripts/fetch_curation.sh

# Combine sources + corpus curé en un corpus d'entraînement validé/dédupliqué.
# Assemble le corpus d'entraînement : le corpus souverain (teacher) est placé EN
# PREMIER pour gagner la priorité sur doublon exact ; le corpus rag historique
# l'augmente, plus l'amorce, les refus (garde-fous) et la curation. Dédupliqué + validé.
corpus-assemble:
	python training/scripts/assemble_corpus.py \
		--sources corpus/corpus_cacao_teacher.jsonl corpus/corpus_cacao_rag.jsonl \
			corpus/corpus_cacao_demarrage.jsonl corpus/corpus_refus.jsonl \
			corpus/corpus_cure.jsonl \
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

# Réassemble d'abord le corpus (teacher + rag + amorce + refus + cure), puis entraîne
# la LoRA sur ce corpus unique, validé et dédupliqué (garde-fous inclus).
train: corpus-assemble
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

# Génération SOUVERAINE du corpus (option B) : modèle-maître ouvert auto-hébergé
# (vLLM) + générateur, sur un hôte GPU. Aucun appel externe. Voir docs/corpus_souverain.md.
#   make corpus-souverain HF_TOKEN=hf_xxx TARGET=2000
corpus-souverain:
	HF_TOKEN=$(HF_TOKEN) TARGET=$(or $(TARGET),10000) \
		docker compose -f docker-compose.corpus.yml up --build

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

test: test-api test-web

test-api:
	cd api && pytest
	pytest training/tests -o addopts=""

# Front : lanceur de tests de Node, aucune dépendance (le web est « zéro dépendance »).
# Ne couvre que les couches PURES — domaine et application — là où est la logique ;
# le rendu DOM se vérifie à l'écran, pas dans un simulacre de navigateur.
test-web:
# Les tests ne chargent que les couches domaine et application. Un module non testé
# — client d'API, vue, composition — peut donc être syntaxiquement cassé sans qu'un
# test échoue : la page blanche se découvrirait alors dans le navigateur. On vérifie
# tous les modules avant de lancer les tests.
	node -e "const fs=require('fs'),p=require('path'),{execFileSync}=require('child_process');(function w(d){for(const e of fs.readdirSync(d,{withFileTypes:true})){const f=p.join(d,e.name);if(e.isDirectory())w(f);else if(f.endsWith('.js'))execFileSync(process.execPath,['--check',f],{stdio:'inherit'});}})('web/src');console.log('syntaxe des modules : ok')"
	node --test web/tests/*.test.js

lint:
	cd api && ruff check .
	ruff check training

format:
	cd api && ruff format .
