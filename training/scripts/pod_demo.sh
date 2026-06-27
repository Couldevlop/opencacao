#!/usr/bin/env bash
# Démo complète SUR LE POD : sert le modèle (vLLM) + l'API FastAPI qui sert aussi
# l'interface web. Tout sur le port 8080, même origine -> aucun CORS.
#
# Pré-requis : dépôt cloné, modèle fusionné présent (models/opencacao-8b),
#              GPU disponible.
# Usage (racine du dépôt) :
#   bash training/scripts/pod_demo.sh
# Puis : exposer le port 8080 sur RunPod et ouvrir son URL proxy dans le navigateur.

set -euo pipefail

MODELE="${1:-models/opencacao-8b}"
export HF_HOME="${HF_HOME:-/workspace/.hf}"

echo "==> 0/3  Vérification de vLLM (requiert un driver CUDA >= 12.8)"
python -c "import vllm" 2>/dev/null \
  || pip install --no-cache-dir -U "vllm>=0.12.0" "mistral-common>=1.8.6"

echo "==> 1/3  Démarrage du modèle (vLLM) sur :8000"
pkill -9 -f "vllm.entrypoints" 2>/dev/null || true
sleep 2
nohup python -m vllm.entrypoints.openai.api_server \
  --model "${MODELE}" \
  --served-model-name opencacao-8b \
  --limit-mm-per-prompt '{"image": 0}' \
  --port 8000 \
  --max-model-len 4096 \
  --gpu-memory-utilization 0.9 \
  > /workspace/vllm.log 2>&1 &

echo "    Attente du chargement (jusqu'à ~10 min la 1re fois)…"
for _ in $(seq 1 150); do
  if python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/v1/models', timeout=3).read()" >/dev/null 2>&1; then
    echo "    Modèle prêt."
    break
  fi
  sleep 5
done

echo "==> 2/3  Installation des dépendances de l'API"
pip install --no-cache-dir -e ./api >/dev/null 2>&1 \
  || pip install --no-cache-dir fastapi "uvicorn[standard]" httpx redis structlog pyyaml pydantic pydantic-settings

echo "==> 3/3  Lancement de l'API + interface sur :8080"
# Même origine (l'API sert web/) -> pas de CORS. Redis absent : cache/rate-limit
# se dégradent proprement. ALLOWED_HOSTS=* (CSV accepté depuis le correctif).
export INFERENCE_BACKEND=vllm
export INFERENCE_URL=http://localhost:8000
export MODEL_NAME=opencacao-8b
export ALLOWED_HOSTS='*'
export ENABLE_DOCS=false
echo
echo ">>> Expose le port 8080 sur RunPod, puis ouvre l'URL proxy (…-8080.proxy.runpod.net)."
echo ">>> L'interface s'affiche et répond AVEC le modèle. Ctrl+C pour arrêter."
echo
uvicorn app.main:app --host 0.0.0.0 --port 8080
