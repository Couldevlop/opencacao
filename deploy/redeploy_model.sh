#!/usr/bin/env bash
# Redéploie un nouveau modèle GGUF (issu d'un réentraînement) sur le cluster K3s.
#
# Étapes : envoi du GGUF sur le nœud -> redémarrage de l'inférence (recharge le
# modèle) -> purge du cache de réponses (les réponses changent avec le modèle)
# -> rappel pour la pré-chauffe.
#
# Pré-requis : kubectl + ssh configurés (KUBECONFIG, clé SSH du nœud).
# Usage :
#   bash deploy/redeploy_model.sh <chemin/opencacao-8b-Q4_K_M.gguf> [version]
# Variables : NODE (root@IP), NS (namespace).

set -euo pipefail

GGUF="${1:?usage: redeploy_model.sh <chemin.gguf> [version]}"
VERSION="${2:-}"
NODE="${NODE:-root@62.238.11.20}"
NS="${NS:-opencacao}"
DEST="/opt/opencacao/models/opencacao-8b-Q4_K_M.gguf"

[[ -f "${GGUF}" ]] || { echo "GGUF introuvable : ${GGUF}" >&2; exit 1; }

echo "==> 1/4  Envoi du GGUF vers le nœud (${NODE})"
scp "${GGUF}" "${NODE}:${DEST}.new"
ssh "${NODE}" "mv -f '${DEST}.new' '${DEST}' && ls -lh '${DEST}'"

echo "==> 2/4  Redémarrage de l'inférence (recharge le nouveau modèle)"
kubectl -n "${NS}" rollout restart deploy/inference
kubectl -n "${NS}" rollout status deploy/inference --timeout=300s

echo "==> 3/4  Purge du cache de réponses (évite de servir d'anciennes réponses)"
kubectl -n "${NS}" exec deploy/redis -- redis-cli FLUSHDB

if [[ -n "${VERSION}" ]]; then
  echo "==> 4/4  Mise à jour de MODEL_VERSION=${VERSION}"
  kubectl -n "${NS}" patch configmap api-config --type merge \
    -p "{\"data\":{\"MODEL_VERSION\":\"${VERSION}\"}}"
  kubectl -n "${NS}" rollout restart deploy/api
  echo "    (pense à reporter MODEL_VERSION=${VERSION} dans deploy/k8s/api.yaml puis à committer)"
else
  echo "==> 4/4  (version inchangée — passe un 2e argument pour bumper MODEL_VERSION)"
fi

echo
echo "Modèle redéployé. Le cache est vide : relance la pré-chauffe pour les FAQ :"
echo "  python scripts/prewarm_cache.py https://opencacao.openlabconsulting.com"
