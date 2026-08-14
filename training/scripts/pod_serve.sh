#!/usr/bin/env bash
# Sert le modèle OpenCacao fusionné via vLLM (texte seul).
#
# Usage (depuis la racine du dépôt, sur le pod) :
#   bash training/scripts/pod_serve.sh
#   # puis, dans un 2e terminal : bash training/scripts/pod_ask.sh
#
# Pour SERVIR LA PRODUCTION depuis un GPU loué (spec V3 §4.5), poser le jeton :
#   INFERENCE_API_KEY=<jeton> bash training/scripts/pod_serve.sh
# Le même jeton va dans le Secret `opencacao-inference` côté cluster, et la bascule
# se fait par `deploy/scripts/profil.sh runpod <URL du tunnel>`.
#
# Argument optionnel : chemin du modèle (défaut : models/opencacao-8b).

set -euo pipefail

MODELE="${1:-models/opencacao-8b}"
export HF_HOME="${HF_HOME:-/workspace/.hf}"
# Le nom SERVI doit égaler le MODEL_NAME que l'API envoie dans chaque requête, sinon
# vLLM répond 404 sur toutes. Il valait « opencacao » ici et « opencacao-8b » partout
# ailleurs (ConfigMap, pod_demo.sh, manifeste GPU) : corrigé le 14/08/2026.
NOM_SERVI="${NOM_SERVI:-opencacao-8b}"

# Libère un éventuel vLLM résiduel (sinon VRAM occupée).
pkill -9 -f "vllm.entrypoints" 2>/dev/null || true
sleep 2

if [ -n "${INFERENCE_API_KEY:-}" ]; then
  echo "==> Jeton exigé des clients (--api-key) : le point de terminaison est protégé."
else
  echo "⚠ AUCUN jeton : n'expose ce port QUE sur un tunnel privé (D1)."
fi

echo "==> Démarrage de vLLM sur le port 8000 (modèle : ${MODELE}, servi : ${NOM_SERVI})"
echo "    Attends la ligne « Application startup complete » avant de tester."
python -m vllm.entrypoints.openai.api_server \
  --model "${MODELE}" \
  --served-model-name "${NOM_SERVI}" \
  --limit-mm-per-prompt '{"image": 0}' \
  --port 8000 \
  --max-model-len 4096 \
  --gpu-memory-utilization 0.9 \
  ${INFERENCE_API_KEY:+--api-key "${INFERENCE_API_KEY}"}
