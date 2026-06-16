#!/usr/bin/env bash
# Sert le modèle OpenCacao fusionné via vLLM (texte seul) pour le tester.
#
# Usage (depuis la racine du dépôt, sur le pod) :
#   bash training/scripts/pod_serve.sh
#   # puis, dans un 2e terminal : bash training/scripts/pod_ask.sh
#
# Argument optionnel : chemin du modèle (défaut : models/opencacao-7b).

set -euo pipefail

MODELE="${1:-models/opencacao-7b}"
export HF_HOME="${HF_HOME:-/workspace/.hf}"

# Libère un éventuel vLLM résiduel (sinon VRAM occupée).
pkill -9 -f "vllm.entrypoints" 2>/dev/null || true
sleep 2

echo "==> Démarrage de vLLM sur le port 8000 (modèle : ${MODELE})"
echo "    Attends la ligne « Application startup complete » avant de tester."
python -m vllm.entrypoints.openai.api_server \
  --model "${MODELE}" \
  --served-model-name opencacao \
  --limit-mm-per-prompt image=0 \
  --port 8000 \
  --max-model-len 4096 \
  --gpu-memory-utilization 0.9
