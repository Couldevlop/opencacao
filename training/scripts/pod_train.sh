#!/usr/bin/env bash
# Entraînement LoRA d'OpenCacao-7B sur un pod GPU loué (RunPod, etc.).
#
# Tourne ENTIÈREMENT sur le pod : installe les dépendances d'entraînement,
# affine Ministral 3 8B en LoRA 4-bit sur le corpus (RAG + démarrage), fusionne
# l'adaptateur avec le modèle de base, et exporte le modèle prêt pour vLLM.
#
# Pré-requis sur le pod :
#   - Template avec Python + PyTorch + CUDA (ex. « Runpod Pytorch 2.8.0 »)
#   - Le code du dépôt présent (git clone)
#   - Le corpus généré : corpus/corpus_cacao_rag.jsonl (via pod_generate.sh)
#   - Variable HF_TOKEN exportée (Mistral est un modèle gated)
#
# Usage (depuis la racine du dépôt, sur le pod) :
#   export HF_TOKEN=hf_xxx
#   bash training/scripts/pod_train.sh
#
# Remarque reproductibilité : on conserve le PyTorch déjà présent sur le pod
# (au lieu de réinstaller la version épinglée), car le pod fournit un PyTorch
# compatible CUDA. Le chemin 100 % épinglé reste docker-compose.training.yml,
# pour une VM GPU disposant de Docker + nvidia-container-runtime.

set -euo pipefail

ADAPTER_DIR="models/lora-adapter"
MERGED_DIR="models/opencacao-8b"
CORPUS_ENTRAINEMENT="corpus/corpus_entrainement.jsonl"
# Variante BF16 (la version Instruct par défaut est en FP8, incompatible QLoRA 4-bit).
BASE_MODEL="mistralai/Ministral-3-8B-Instruct-2512-BF16"

if [[ -z "${HF_TOKEN:-}" ]]; then
  echo "ERREUR : exporte HF_TOKEN (token Hugging Face) avant de lancer." >&2
  exit 1
fi
export HUGGING_FACE_HUB_TOKEN="${HF_TOKEN}"

# Liste des corpus d'entraînement réellement présents.
CORPUS=()
[[ -f corpus/corpus_cacao_rag.jsonl ]] && CORPUS+=("corpus/corpus_cacao_rag.jsonl")
[[ -f corpus/corpus_cacao_demarrage.jsonl ]] && CORPUS+=("corpus/corpus_cacao_demarrage.jsonl")
# Exemples de REFUS (dosages phytosanitaires, médical, image, hors-filière) :
# apprend au modèle à refuser et rediriger vers l'ANADER au lieu d'inventer une
# dose. Cf. constat de test (le modèle nu donnait un dosage halluciné).
[[ -f corpus/corpus_refus.jsonl ]] && CORPUS+=("corpus/corpus_refus.jsonl")
# Corpus CURÉ : paires Q/R validées par un expert via la console de curation
# (réponses réelles corrigées). C'est la boucle d'amélioration continue.
[[ -f corpus/corpus_cure.jsonl ]] && CORPUS+=("corpus/corpus_cure.jsonl")
if [[ ${#CORPUS[@]} -eq 0 ]]; then
  echo "ERREUR : aucun corpus trouvé. Génère d'abord corpus/corpus_cacao_rag.jsonl" >&2
  echo "  (bash training/scripts/pod_generate.sh)" >&2
  exit 1
fi
echo "==> Sources de corpus : ${CORPUS[*]}"

echo "==> 1/5  Installation des dépendances d'entraînement (PyTorch du pod conservé)"
echo "    (premier téléchargement potentiellement long : transformers, peft, etc.)"
pip install --upgrade pip
# On retire la ligne torch épinglée pour ne pas downgrader le PyTorch du pod.
grep -v '^torch' training/requirements.txt > /tmp/req-train.txt
pip install -r /tmp/req-train.txt
# Ministral 3 (config.json -> transformers_version 5.0.0.dev0, archi mistral3)
# n'est chargeable qu'avec une transformers de la branche main, et opencv (tiré
# par la pile multimodale) exige numpy>=2. Mêmes correctifs que pod_generate.sh.
pip install -U "git+https://github.com/huggingface/transformers"
pip install -U "numpy>=2"

echo "==> 2/5  Assemblage + validation + déduplication du corpus"
echo "    (combine sources & corpus curé -> ${CORPUS_ENTRAINEMENT} ; écarte invalides/doublons)"
python training/scripts/assemble_corpus.py --sources "${CORPUS[@]}" --out "${CORPUS_ENTRAINEMENT}"

echo "==> 3/5  Smoke-test : chargement du modèle + 3 pas (jetable, fail-fast)"
echo "    (valide la classe multimodale, le ciblage LoRA et l'API TRL avant le run long)"
python training/scripts/train_lora.py --corpus "${CORPUS_ENTRAINEMENT}" --output /tmp/lora-smoke --max-steps 3
rm -rf /tmp/lora-smoke

echo "==> 4/5  Fine-tuning LoRA 4-bit (run complet)"
python training/scripts/train_lora.py --corpus "${CORPUS_ENTRAINEMENT}" --output "${ADAPTER_DIR}"

echo "==> 5/5  Fusion de l'adaptateur avec le modèle de base"
python training/scripts/merge_and_export.py \
  --base "${BASE_MODEL}" \
  --adapter "${ADAPTER_DIR}" \
  --output "${MERGED_DIR}"

echo
echo "Terminé. Modèle fusionné dans ${MERGED_DIR}/ (prêt pour vLLM)."
echo "Récupère-le sur ton PC, par exemple :"
echo "  tar czf opencacao-8b.tar.gz -C models opencacao-8b && runpodctl send opencacao-8b.tar.gz"
echo
echo "Export GGUF (pour servir en CPU via llama.cpp) : bash training/scripts/pod_gguf.sh"
echo "Puis redéploiement sur le cluster : bash deploy/redeploy_model.sh <chemin-du.gguf>"
