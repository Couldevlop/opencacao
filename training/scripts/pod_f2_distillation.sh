#!/usr/bin/env bash
# F2 — Distillation SOUVERAINE depuis un maître ouvert auto-hébergé, sur un seul pod.
#
# Pourquoi : le corpus actuel (10k) a été AUTO-généré par Ministral-8B lui-même → le
# fine-tune ne peut pas dépasser le modèle de base. On régénère un corpus de meilleure
# qualité avec un maître PLUS FORT et OUVERT (Qwen2.5-72B-AWQ par défaut), hébergé sur
# TON pod via vLLM — AUCUNE API tierce (souveraineté, CLAUDE §1.3/§13). Puis on
# ré-entraîne la LoRA dessus.
#
# Chaîne (tout sur le pod, séquentiel pour tenir sur 1 GPU) :
#   1) sert le maître (vLLM, localhost)  2) génère le corpus (RAG sur docs officiels)
#   3) ARRÊTE le maître (libère le GPU)  4) LoRA 4-bit + fusion  5) export GGUF Q4_K_M.
#
# GPU :
#   - Qwen2.5-72B-AWQ : GPU >= 48 Go (A6000 48G / A100). Meilleure qualité.
#   - Plus léger (24 Go) : TEACHER_MODEL=Qwen/Qwen2.5-32B-Instruct-AWQ (toujours bien
#     plus fort que Ministral-8B). Régler aussi GPU_UTIL si besoin.
#
# Pré-requis pod : template Python+PyTorch+CUDA>=12.1, volume >= 80 Go, dépôt cloné.
#   - HF_TOKEN : requis seulement pour le modèle de BASE Ministral (gated). Qwen-AWQ
#     n'est PAS gated.
#
# Usage :
#   export HF_TOKEN=hf_xxx
#   bash training/scripts/pod_f2_distillation.sh 5000      # 5000 = cible de paires
set -euo pipefail

CIBLE="${1:-5000}"
TEACHER_MODEL="${TEACHER_MODEL:-Qwen/Qwen2.5-72B-Instruct-AWQ}"
TEACHER_QUANT="${TEACHER_QUANT:-awq}"
GPU_UTIL="${GPU_UTIL:-0.92}"
CONCURRENCE="${CONCURRENCE:-8}"
PORT="${PORT:-8000}"
TEACHER_OUT="corpus/corpus_cacao_teacher.jsonl"
MERGED_DIR="models/opencacao-8b"

: "${HF_TOKEN:?Exporte HF_TOKEN (modèle de base Ministral, gated)}"
export HF_TOKEN HUGGING_FACE_HUB_TOKEN="${HF_TOKEN}"
# Caches sur le gros volume (l'overlay du conteneur est trop petit pour un 70B).
CACHE_ROOT="${CACHE_ROOT:-/workspace}"; [ -d "$CACHE_ROOT" ] || CACHE_ROOT="$HOME"
export HF_HOME="$CACHE_ROOT/.hf" TMPDIR="$CACHE_ROOT/tmp"
mkdir -p "$HF_HOME" "$TMPDIR"
CLE="$(python -c 'import secrets; print(secrets.token_urlsafe(24))')"

echo "==================================================================="
echo " F2 souverain — maître ${TEACHER_MODEL} (cible ${CIBLE} paires)"
echo "==================================================================="

echo "==> 0/5  Installation de vLLM (1er téléchargement : 5-15 min)"
pip install --no-cache-dir --upgrade pip >/dev/null
pip install --no-cache-dir vllm >/dev/null

echo "==> 1/5  Démarrage du maître (chargement d'un 70B : ~15-25 min)"
python -m vllm.entrypoints.openai.api_server \
  --model "${TEACHER_MODEL}" --served-model-name teacher \
  --quantization "${TEACHER_QUANT}" --api-key "${CLE}" \
  --port "${PORT}" --max-model-len 8192 --gpu-memory-utilization "${GPU_UTIL}" \
  > "${TMPDIR}/vllm.log" 2>&1 &
VLLM_PID=$!
trap 'kill "${VLLM_PID}" 2>/dev/null || true' EXIT

# Attente du « ready » (poll de /v1/models avec la clé).
for i in $(seq 1 360); do  # jusqu'à ~30 min
  if curl -fsS -H "Authorization: Bearer ${CLE}" "http://localhost:${PORT}/v1/models" >/dev/null 2>&1; then
    echo "    maître prêt après ~$((i * 5))s"; break
  fi
  if ! kill -0 "${VLLM_PID}" 2>/dev/null; then
    echo "ERREUR : vLLM s'est arrêté. Voir ${TMPDIR}/vllm.log :"; tail -30 "${TMPDIR}/vllm.log"; exit 1
  fi
  sleep 5
done

echo "==> 2/5  Génération du corpus distillé (RAG sur les documents officiels)"
CORPUS_LLM_BASE_URL="http://localhost:${PORT}" \
CORPUS_LLM_MODEL="teacher" \
CORPUS_LLM_API_KEY="${CLE}" \
python training/scripts/build_corpus_rag.py \
  --target "${CIBLE}" --concurrence "${CONCURRENCE}" --out "${TEACHER_OUT}"

echo "==> 3/5  Arrêt du maître (libération du GPU pour l'entraînement)"
kill "${VLLM_PID}" 2>/dev/null || true
trap - EXIT
sleep 10

echo "==> 4/5  Assemblage + LoRA 4-bit + fusion"
python training/scripts/assemble_corpus.py \
  --sources "${TEACHER_OUT}" corpus/corpus_cacao_rag.jsonl \
            corpus/corpus_cacao_demarrage.jsonl corpus/corpus_refus.jsonl \
            corpus/corpus_cure.jsonl \
  --out corpus/corpus_entrainement.jsonl
bash training/scripts/pod_train.sh

echo "==> 5/5  Export GGUF Q4_K_M"
bash training/scripts/pod_gguf.sh "${MERGED_DIR}"

echo ""
echo "================================ TERMINÉ ============================"
echo " GGUF prêt : models/opencacao-7b-Q4_K_M.gguf (~5 Go)"
echo " Suite (cf. docs/F2_distillation_runpod.md) : éval F1 avant/après, puis"
echo " rapatrier + déployer sur le CX53 si garde-fous=100% ET qualité>=baseline."
echo "===================================================================="
