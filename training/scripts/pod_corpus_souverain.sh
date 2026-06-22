#!/usr/bin/env bash
# Sert le MODÈLE-MAÎTRE ouvert (option B) via vLLM sur un pod GPU loué.
#
# Ce script ne fait QUE servir le modèle. La génération RAG (téléchargement des
# sources, découpage, embeddings, déduplication, requêtes) tourne EN LOCAL et
# interroge ce pod à distance — le pod n'exécute donc aucun RAG. Séparation voulue :
# le GPU est dédié au modèle-maître, le reste (CPU) reste sur ta machine.
#
# Souveraineté (CLAUDE §1.3, §13) : modèle ouvert auto-hébergé, AUCUNE API tierce.
# L'endpoint est protégé par une clé (il est exposé publiquement via le proxy RunPod).
#
# Pré-requis sur le pod :
#   - Template Python + PyTorch + CUDA >= 12.1, 1 GPU >= 40 Go
#   - Le code du dépôt présent (git clone ou runpodctl receive)
#   - HF_TOKEN exporté UNIQUEMENT si le modèle est gated (ex. Llama). Le défaut
#     Qwen2.5-72B-AWQ n'est PAS gated.
#
# Usage (sur le pod) :
#   export CORPUS_LLM_API_KEY=...        # clé partagée pod <-> local (sinon générée)
#   bash training/scripts/pod_corpus_souverain.sh
#
# Puis EN LOCAL (sur ta machine), génère le corpus contre ce pod :
#   export CORPUS_LLM_BASE_URL=https://<POD_ID>-8000.proxy.runpod.net
#   export CORPUS_LLM_MODEL=teacher
#   export CORPUS_LLM_API_KEY=<la même clé>
#   python training/scripts/build_corpus_rag.py \
#     --target 2000 --concurrence 8 --out corpus/corpus_cacao_teacher.jsonl

set -euo pipefail

TEACHER_MODEL="${TEACHER_MODEL:-Qwen/Qwen2.5-72B-Instruct-AWQ}"
TEACHER_QUANT="${TEACHER_QUANT:-awq}"
PORT="${PORT:-8000}"

# Clé d'API de l'endpoint (protège le pod exposé). Générée si absente, puis affichée.
if [[ -z "${CORPUS_LLM_API_KEY:-}" ]]; then
  CORPUS_LLM_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(24))')"
  echo "==> Aucune CORPUS_LLM_API_KEY fournie : une clé a été générée."
fi

# Caches sur le gros volume monté (/workspace sur RunPod) : l'overlay du conteneur
# est trop petit pour les poids du modèle-maître (40-70 Go).
CACHE_ROOT="${CACHE_ROOT:-/workspace}"; [ -d "$CACHE_ROOT" ] || CACHE_ROOT="$HOME"
export HF_HOME="$CACHE_ROOT/.hf"
export TMPDIR="$CACHE_ROOT/tmp"
mkdir -p "$HF_HOME" "$TMPDIR"
[[ -n "${HF_TOKEN:-}" ]] && export HUGGING_FACE_HUB_TOKEN="${HF_TOKEN}"

echo "==> 1/2  Installation de vLLM (premier téléchargement : 5-15 min)"
pip install --no-cache-dir --upgrade pip
pip install --no-cache-dir -U vllm

# Libère un éventuel vLLM résiduel (sinon VRAM occupée).
pkill -9 -f "vllm.entrypoints" 2>/dev/null || true
sleep 3

echo "==> 2/2  Démarrage du modèle-maître : ${TEACHER_MODEL} (quant ${TEACHER_QUANT})"
echo "    Endpoint protégé par clé. Le chargement d'un 70B peut prendre ~15-25 min."
echo
echo "  ┌─────────────────────────────────────────────────────────────────────┐"
echo "  │  À UTILISER EN LOCAL (build_corpus_rag.py) :                         │"
echo "  │    CORPUS_LLM_BASE_URL = https://<POD_ID>-${PORT}.proxy.runpod.net   │"
echo "  │    CORPUS_LLM_MODEL    = teacher                                     │"
echo "  │    CORPUS_LLM_API_KEY  = ${CORPUS_LLM_API_KEY}"
echo "  └─────────────────────────────────────────────────────────────────────┘"
echo "    (Remplace <POD_ID> par l'identifiant de ton pod ; attends"
echo "     « Application startup complete » avant de lancer la génération locale.)"
echo

python -m vllm.entrypoints.openai.api_server \
  --model "${TEACHER_MODEL}" \
  --served-model-name teacher \
  --quantization "${TEACHER_QUANT}" \
  --api-key "${CORPUS_LLM_API_KEY}" \
  --port "${PORT}" \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.92
