#!/usr/bin/env bash
# Génération du corpus OpenCacao sur un pod GPU loué (RunPod, etc.).
#
# Tourne ENTIÈREMENT sur le pod : installe vLLM + dépendances, sert Mistral 7B
# en local, lance la génération RAG (qui télécharge elle-même les documents
# officiels), puis valide le corpus. Aucun transfert de PDF nécessaire.
#
# Pré-requis sur le pod :
#   - Template avec Python + PyTorch + CUDA (ex. « Runpod Pytorch 2.8.0 »)
#   - Le code du dépôt présent (git clone ou `runpodctl receive`)
#   - Variable HF_TOKEN exportée (token Hugging Face, modèle Mistral gated)
#
# Usage (depuis la racine du dépôt, sur le pod) :
#   export HF_TOKEN=hf_xxx
#   bash training/scripts/pod_generate.sh 10000
#
# Argument optionnel : cible de paires (défaut 10000).

set -euo pipefail

CIBLE="${1:-10000}"
MODELE_BASE="mistralai/Mistral-7B-Instruct-v0.3"
NOM_SERVI="mistral"
PORT=8000

if [[ -z "${HF_TOKEN:-}" ]]; then
  echo "ERREUR : exporte HF_TOKEN (token Hugging Face) avant de lancer." >&2
  echo "  export HF_TOKEN=hf_xxx" >&2
  exit 1
fi

echo "==> 1/5  Installation de vLLM et des dépendances du corpus"
pip install --quiet --upgrade pip
pip install --quiet vllm
pip install --quiet -r training/requirements-corpus.txt

echo "==> 2/5  Démarrage du serveur Mistral (vLLM) sur le port ${PORT}"
export HUGGING_FACE_HUB_TOKEN="${HF_TOKEN}"
python -m vllm.entrypoints.openai.api_server \
  --model "${MODELE_BASE}" \
  --served-model-name "${NOM_SERVI}" \
  --port "${PORT}" \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.92 \
  > /tmp/vllm.log 2>&1 &
VLLM_PID=$!
trap 'kill "${VLLM_PID}" 2>/dev/null || true' EXIT

echo "==> 3/5  Attente du chargement du modèle (peut prendre 1-3 min)"
for _ in $(seq 1 90); do
  if curl -sf "http://localhost:${PORT}/v1/models" >/dev/null 2>&1; then
    echo "    Mistral prêt."
    break
  fi
  if ! kill -0 "${VLLM_PID}" 2>/dev/null; then
    echo "ERREUR : vLLM s'est arrêté. Voir /tmp/vllm.log :" >&2
    tail -n 30 /tmp/vllm.log >&2
    exit 1
  fi
  sleep 5
done

echo "==> 4/5  Génération du corpus (cible ${CIBLE})"
export CORPUS_LLM_BASE_URL="http://localhost:${PORT}"
export CORPUS_LLM_MODEL="${NOM_SERVI}"
python training/scripts/build_corpus_rag.py \
  --target "${CIBLE}" \
  --out corpus/corpus_cacao_rag.jsonl

echo "==> 5/5  Validation du corpus produit"
python training/scripts/enrich_corpus.py --check corpus/corpus_cacao_rag.jsonl || true

echo
echo "Terminé. Récupère le corpus sur ton PC, par exemple :"
echo "  runpodctl send corpus/corpus_cacao_rag.jsonl"
echo "puis sur ton PC :  runpodctl receive <code-affiché>"
