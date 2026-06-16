#!/usr/bin/env bash
# Exporte le modèle fusionné OpenCacao en GGUF Q4_K_M (pour servir en CPU via
# llama.cpp, ex. sur K3s Hetzner). À lancer sur le pod (le modèle fusionné
# models/opencacao-7b doit être présent).
#
# Usage : bash training/scripts/pod_gguf.sh
# Sortie : models/opencacao-7b-Q4_K_M.gguf  (~5 Go) -> à rapatrier via runpodctl.
#
# NOTE mistral3 : Ministral 3 est une archi multimodale récente. llama.cpp récent
# la supporte (un GGUF officiel existe), mais on clone llama.cpp à jour pour en
# bénéficier. La conversion produit le GGUF du modèle de langue (texte) ; la
# partie vision est ignorée — c'est ce qu'on veut pour le conseil textuel.

set -euo pipefail

MERGED="${1:-models/opencacao-7b}"
F16="models/opencacao-7b-f16.gguf"
OUT="models/opencacao-7b-Q4_K_M.gguf"

echo "==> 1/4  Récupération de llama.cpp (à jour)"
[ -d llama.cpp ] || git clone --depth 1 https://github.com/ggml-org/llama.cpp
pip install --no-cache-dir -r llama.cpp/requirements.txt

echo "==> 2/4  Compilation de llama-quantize"
if [ ! -x llama.cpp/build/bin/llama-quantize ]; then
  command -v cmake >/dev/null 2>&1 || pip install --no-cache-dir cmake
  cmake -S llama.cpp -B llama.cpp/build >/dev/null
  cmake --build llama.cpp/build --config Release -j --target llama-quantize
fi

echo "==> 3/4  Conversion HF -> GGUF f16"
python llama.cpp/convert_hf_to_gguf.py "${MERGED}" --outfile "${F16}" --outtype f16

echo "==> 4/4  Quantification Q4_K_M (~5 Go)"
llama.cpp/build/bin/llama-quantize "${F16}" "${OUT}" Q4_K_M
rm -f "${F16}"

echo
echo "Terminé : ${OUT}"
echo "Rapatrie-le :  runpodctl send ${OUT}"
