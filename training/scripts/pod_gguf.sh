#!/usr/bin/env bash
# Exporte le modèle fusionné OpenCacao en GGUF Q4_K_M (pour servir en CPU via
# llama.cpp, ex. sur K3s Hetzner). À lancer sur le pod (le modèle fusionné
# models/opencacao-8b doit être présent).
#
# Usage : bash training/scripts/pod_gguf.sh
# Sortie : models/opencacao-8b-Q4_K_M.gguf  (~5 Go) -> à rapatrier via runpodctl.
#
# NOTE mistral3 : Ministral 3 est une archi multimodale récente. llama.cpp récent
# la supporte (un GGUF officiel existe), mais on clone llama.cpp à jour pour en
# bénéficier. La conversion produit le GGUF du modèle de langue (texte) ; la
# partie vision est ignorée — c'est ce qu'on veut pour le conseil textuel.

set -euo pipefail

MERGED="${1:-models/opencacao-8b}"
F16="models/opencacao-8b-f16.gguf"
OUT="models/opencacao-8b-Q4_K_M.gguf"

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
# La fusion HF (save_pretrained) écrit un tokenizer_config.json avec la classe
# TokenizersBackend que convert_hf_to_gguf ne sait pas relire, et n'écrit pas
# tekken.json. Le fine-tuning LoRA ne modifie PAS le tokenizer : on restaure les
# fichiers tokenizer canoniques du dépôt de base (modèle public, sans token).
BASE="https://huggingface.co/mistralai/Ministral-3-8B-Instruct-2512/resolve/main"
for f in tokenizer.json tokenizer_config.json tekken.json; do
  echo "    récupération ${f}"
  curl -fsSL -o "${MERGED}/${f}" "${BASE}/${f}"
done
# tokenizer_config.json déclare la classe "TokenizersBackend" (mistral-common) que
# AutoTokenizer ne sait pas importer -> on la force à PreTrainedTokenizerFast pour
# que le convertisseur charge directement tokenizer.json (BPE/tekken).
python - "${MERGED}/tokenizer_config.json" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p, encoding="utf-8"))
d["tokenizer_class"] = "PreTrainedTokenizerFast"
d.pop("auto_map", None)
# Champs incompatibles avec ce transformers (liste au lieu de dict, etc.) :
# inutiles pour la conversion du vocab (lu depuis tokenizer.json).
d.pop("extra_special_tokens", None)
json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("    tokenizer_config.json -> PreTrainedTokenizerFast")
PY
python llama.cpp/convert_hf_to_gguf.py "${MERGED}" --outfile "${F16}" --outtype f16

echo "==> 4/4  Quantification Q4_K_M (~5 Go)"
llama.cpp/build/bin/llama-quantize "${F16}" "${OUT}" Q4_K_M
rm -f "${F16}"

echo
echo "Terminé : ${OUT}"
echo "Rapatrie-le :  runpodctl send ${OUT}"
