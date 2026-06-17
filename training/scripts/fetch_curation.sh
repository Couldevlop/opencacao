#!/usr/bin/env bash
# Récupère le corpus CURÉ (corpus_cure.jsonl) depuis le cluster vers le dépôt local,
# pour le réinjecter dans le prochain entraînement (boucle d'amélioration).
#
# Pré-requis : kubectl configuré (KUBECONFIG) sur le cluster OpenCacao.
# Usage : bash training/scripts/fetch_curation.sh
#
# L'image de la console étant minimale (pas de tar -> kubectl cp impossible),
# on lit le fichier via `kubectl exec cat`.

set -euo pipefail

NS="${NS:-opencacao}"
OUT="corpus/corpus_cure.jsonl"

mkdir -p corpus
echo "==> Récupération de /data/corpus_cure.jsonl depuis la console de curation ($NS)"
kubectl -n "${NS}" exec deploy/curation -- sh -c 'cat /data/corpus_cure.jsonl 2>/dev/null || true' > "${OUT}"

n=$(grep -c '[^[:space:]]' "${OUT}" 2>/dev/null || echo 0)
echo "==> ${n} paire(s) curée(s) écrite(s) dans ${OUT}"
if [[ "${n}" -eq 0 ]]; then
  echo "    (rien à curer pour l'instant — valide des réponses via la console de curation)"
fi
