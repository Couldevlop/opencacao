#!/usr/bin/env bash
# Génère le corpus EN LOCAL contre le modèle-maître servi sur le pod (option B).
#
# Le RAG (téléchargement des sources officielles, découpage, embeddings, dédup)
# tourne ICI, sur ta machine ; SEULES les requêtes de rédaction partent vers le pod
# GPU (cf. pod_corpus_souverain.sh). Aucune API tierce : souverain de bout en bout.
#
# Pré-requis : pod déjà démarré (pod_corpus_souverain.sh) et son endpoint joignable.
#
# Usage (depuis la racine du dépôt, en local) :
#   export CORPUS_LLM_BASE_URL=https://<POD_ID>-8000.proxy.runpod.net
#   export CORPUS_LLM_API_KEY=...           # la même clé que le pod
#   bash training/scripts/generate_souverain_local.sh 2000
#
# Argument optionnel : cible de paires (défaut 2000, le lot de mesure).

set -euo pipefail

: "${CORPUS_LLM_BASE_URL:?Exporte CORPUS_LLM_BASE_URL (endpoint du pod, ex. https://<POD_ID>-8000.proxy.runpod.net)}"
: "${CORPUS_LLM_API_KEY:?Exporte CORPUS_LLM_API_KEY (la même clé que celle affichée par le pod)}"
export CORPUS_LLM_MODEL="${CORPUS_LLM_MODEL:-teacher}"

CIBLE="${1:-2000}"
CONCURRENCE="${CONCURRENCE:-8}"
SORTIE="${CORPUS_OUT:-corpus/corpus_cacao_teacher.jsonl}"

echo "==> 1/3  Dépendances locales du corpus (embeddings + PDF)"
python -m pip install --no-cache-dir -r training/requirements-corpus.txt

echo "==> 2/3  Génération (cible ${CIBLE}) contre ${CORPUS_LLM_BASE_URL}"
echo "    RAG local, requêtes vers le pod (modèle « ${CORPUS_LLM_MODEL} »)."
python training/scripts/build_corpus_rag.py \
  --target "${CIBLE}" \
  --concurrence "${CONCURRENCE}" \
  --out "${SORTIE}"

echo "==> 3/3  Validation du corpus produit"
python training/scripts/enrich_corpus.py --check "${SORTIE}" || true

echo
echo "Terminé : ${SORTIE}."
echo "Étape suivante : 'make corpus-assemble' puis 'make train' (augmente l'existant)."
