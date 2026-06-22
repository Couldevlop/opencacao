#!/usr/bin/env bash
# Génération du corpus OpenCacao avec GLM-5.2 comme MODÈLE-MAÎTRE (Z.ai), SANS GPU.
#
# Contrairement à pod_generate.sh (qui sert Ministral en local via vLLM sur un pod
# GPU loué et lui fait générer ses propres données), ce script délègue la rédaction
# des paires Q/R à GLM-5.2 via l'API OpenAI-compatible de Z.ai. Un modèle-maître plus
# fort produit des données de meilleure qualité : c'est le levier le plus rentable
# pour améliorer le fine-tuning, sans télécharger 1,5 To de poids ni louer de GPU.
#
# SOUVERAINETÉ (CLAUDE §1.3, §13) : appel externe TOLÉRÉ et CLAIREMENT SIGNALÉ, car
# limité à l'ENRICHISSEMENT du corpus (hors production). La génération reste ancrée
# dans les extraits officiels (ANADER/CNRA/Conseil du Café-Cacao/FAO/FIRCA) et passe
# les mêmes garde-fous (source citée obligatoire, refus de tout dosage chiffré). Les
# embeddings et la déduplication restent 100 % locaux.
#
# Pré-requis :
#   - Python 3.11+ (CPU suffit : pas de GPU, pas de vLLM)
#   - Une clé API Z.ai exportée dans ZAI_API_KEY
#
# Usage (depuis la racine du dépôt) :
#   export ZAI_API_KEY=...            # clé Z.ai
#   bash training/scripts/generate_glm.sh 10000
#
# Argument optionnel : cible de paires (défaut 10000).

set -euo pipefail

CIBLE="${1:-10000}"
# Endpoint OpenAI-compatible de Z.ai (déjà versionné en /v4 : le client n'ajoute
# donc PAS de /v1). Surchargeable si Z.ai change de domaine.
BASE_URL="${CORPUS_LLM_BASE_URL:-https://api.z.ai/api/coding/paas/v4}"
MODELE="${CORPUS_LLM_MODEL:-glm-5.2}"
SORTIE="${CORPUS_OUT:-corpus/corpus_cacao_glm.jsonl}"
# Une API distante facture et limite le débit : concurrence plus basse qu'en local
# (vLLM agrège, pas une API publique). Surchargeable via CONCURRENCE.
CONCURRENCE="${CONCURRENCE:-4}"

if [[ -z "${ZAI_API_KEY:-}" && -z "${CORPUS_LLM_API_KEY:-}" ]]; then
  echo "ERREUR : exporte ZAI_API_KEY (clé API Z.ai) avant de lancer." >&2
  echo "  export ZAI_API_KEY=..." >&2
  exit 1
fi

echo "==> 1/3  Installation des dépendances du corpus (embeddings locaux + PDF)"
pip install --no-cache-dir -r training/requirements-corpus.txt

echo "==> 2/3  Génération du corpus avec GLM-5.2 (cible ${CIBLE}, sortie ${SORTIE})"
echo "    Modèle-maître EXTERNE : ${MODELE} via ${BASE_URL} (enrichissement signalé)"
export CORPUS_LLM_BASE_URL="${BASE_URL}"
export CORPUS_LLM_MODEL="${MODELE}"
export CORPUS_LLM_API_KEY="${ZAI_API_KEY:-${CORPUS_LLM_API_KEY}}"
python training/scripts/build_corpus_rag.py \
  --target "${CIBLE}" \
  --concurrence "${CONCURRENCE}" \
  --out "${SORTIE}"

echo "==> 3/3  Validation du corpus produit"
python training/scripts/enrich_corpus.py --check "${SORTIE}" || true

echo
echo "Terminé. Corpus écrit dans ${SORTIE}."
echo "Étape suivante : fusion/entraînement LoRA (make train) avec ce corpus."
