#!/usr/bin/env bash
# Déploiement de secours (fallback) — bascule les déploiements OpenCacao sur un tag
# d'image GHCR donné, sans passer par ArgoCD.
#
# Pourquoi : ArgoCD v2.13.x plante au diff client sur K8s >= 1.33/1.35 (notre cluster
# est en v1.35.x) → les syncs échouent et l'Image Updater n'a aucun effet. En attendant
# l'upgrade d'ArgoCD, ce script applique le nouveau tag directement (kubectl set image).
#
# Usage :
#   KUBECONFIG=kubeconfig-hetzner.yaml deploy/scripts/roll-image.sh 0.6.3
set -euo pipefail

TAG="${1:?Usage: roll-image.sh <X.Y.Z>}"
NS="${NS:-opencacao}"
API="ghcr.io/couldevlop/opencacao-api:${TAG}"
WEB="ghcr.io/couldevlop/opencacao-web:${TAG}"

echo "→ Bascule du namespace ${NS} sur le tag ${TAG}"
kubectl -n "${NS}" set image deployment/api api="${API}"
kubectl -n "${NS}" set image deployment/curation curation="${API}"
kubectl -n "${NS}" set image deployment/web web="${WEB}"

kubectl -n "${NS}" rollout status deployment/api --timeout=300s
kubectl -n "${NS}" rollout status deployment/web --timeout=300s
kubectl -n "${NS}" rollout status deployment/curation --timeout=300s

# Purge du cache Cloudflare (optionnelle) : l'app statique n'a pas de versionnage de
# fichiers, donc le CDN peut servir un CSS/JS périmé après déploiement. Si CF_API_TOKEN
# et CF_ZONE_ID sont fournis, on purge tout. Sinon, purger à la main dans le dashboard.
if [ -n "${CF_API_TOKEN:-}" ] && [ -n "${CF_ZONE_ID:-}" ]; then
    echo "→ Purge du cache Cloudflare"
    curl -fsS -X POST "https://api.cloudflare.com/client/v4/zones/${CF_ZONE_ID}/purge_cache" \
        -H "Authorization: Bearer ${CF_API_TOKEN}" \
        -H "Content-Type: application/json" \
        --data '{"purge_everything":true}' >/dev/null && echo "  cache purgé"
else
    echo "ℹ Cache Cloudflare NON purgé (CF_API_TOKEN/CF_ZONE_ID absents) — purger à la main."
fi

echo "OK → api/curation=${API}, web=${WEB}"
