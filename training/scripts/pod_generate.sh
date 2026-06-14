#!/usr/bin/env bash
# Génération du corpus OpenCacao sur un pod GPU loué (RunPod, etc.).
#
# Tourne ENTIÈREMENT sur le pod : installe vLLM + dépendances, sert Ministral 3
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
MODELE_BASE="mistralai/Ministral-3-8B-Instruct-2512"
NOM_SERVI="ministral"
PORT=8000

if [[ -z "${HF_TOKEN:-}" ]]; then
  echo "ERREUR : exporte HF_TOKEN (token Hugging Face) avant de lancer." >&2
  echo "  export HF_TOKEN=hf_xxx" >&2
  exit 1
fi

# Caches sur le gros volume monté (/workspace sur RunPod) : l'overlay du conteneur
# (souvent ~5 Go) ne peut contenir ni le modèle (~8-16 Go) ni les caches pip.
CACHE_ROOT="${CACHE_ROOT:-/workspace}"; [ -d "$CACHE_ROOT" ] || CACHE_ROOT="$HOME"
export HF_HOME="$CACHE_ROOT/.hf"
export TMPDIR="$CACHE_ROOT/tmp"
mkdir -p "$HF_HOME" "$TMPDIR"

# PRÉREQUIS DRIVER : vLLM >= 0.12 (requis par Ministral 3) tire un torch qui exige
# un driver NVIDIA CUDA >= 12.8. Sur un hôte RunPod trop vieux (ex. CUDA 12.4),
# vLLM échoue avec "NVIDIA driver too old". Choisis un pod en CUDA >= 12.8.

echo "==> 1/5  Installation de vLLM et des dépendances du corpus"
echo "    (vLLM = plusieurs Go : ce premier téléchargement prend 5-15 min)"
pip install --no-cache-dir --upgrade pip
# Ministral 3 (déc. 2025) requiert vLLM >= 0.12 et mistral-common >= 1.8.6.
pip install --no-cache-dir -U "vllm>=0.12.0" "mistral-common>=1.8.6"
# transformers depuis main (Ministral 3 = transformers_version 5.0.0.dev0).
# Fallback si une release suffit : pip install --no-cache-dir -U "transformers>=5.12".
pip install --no-cache-dir -U "git+https://github.com/huggingface/transformers"
# Certaines images (conda) embarquent un prometheus-fastapi-instrumentator trop
# vieux, incompatible avec la Starlette de vLLM : toutes les requêtes HTTP
# renvoient alors 500 ("'_IncludedRouter' object has no attribute 'path'") et le
# serveur paraît injoignable. On le met à jour.
pip install --no-cache-dir -U prometheus-fastapi-instrumentator
pip install --no-cache-dir -r training/requirements-corpus.txt

echo "==> 2/5  Démarrage du serveur Ministral 3 (vLLM) sur le port ${PORT}"
export HUGGING_FACE_HUB_TOKEN="${HF_TOKEN}"
# Chargement au format mistral natif (params.json/tekken.json) : indispensable
# pour Ministral 3 (archi 'mistral3' que les transformers récents seuls
# connaissent) — contourne le parseur de config HF. Le modèle est en FP8 (~8 Go),
# tient dans 20 Go.
python -m vllm.entrypoints.openai.api_server \
  --model "${MODELE_BASE}" \
  --served-model-name "${NOM_SERVI}" \
  --tokenizer-mode mistral \
  --config-format mistral \
  --load-format mistral \
  --limit-mm-per-prompt '{"image": 0}' \
  --port "${PORT}" \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.92 \
  > /tmp/vllm.log 2>&1 &
VLLM_PID=$!
trap 'kill "${VLLM_PID}" 2>/dev/null || true' EXIT

echo "==> 3/5  Attente du chargement du modèle (vLLM 0.23 compile : jusqu'à ~20 min)"
# Sonde via python (toujours présent ; pas de dépendance à curl). urlopen lève
# sur 500 comme sur connexion refusée -> tant que le serveur n'est pas vraiment
# OK (200), on continue d'attendre.
PRET=0
for _ in $(seq 1 240); do
  if python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT}/v1/models', timeout=3).read()" >/dev/null 2>&1; then
    echo "    Ministral prêt."
    PRET=1
    break
  fi
  if ! kill -0 "${VLLM_PID}" 2>/dev/null; then
    echo "ERREUR : vLLM s'est arrêté. Voir /tmp/vllm.log :" >&2
    tail -n 40 /tmp/vllm.log >&2
    exit 1
  fi
  sleep 5
done
# Ne JAMAIS lancer la génération si le serveur n'est pas prêt (sinon échec garanti).
if [ "${PRET}" -ne 1 ]; then
  echo "ERREUR : vLLM n'a pas répondu à temps (200). Fin de /tmp/vllm.log :" >&2
  tail -n 40 /tmp/vllm.log >&2
  exit 1
fi

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
