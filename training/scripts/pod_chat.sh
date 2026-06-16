#!/usr/bin/env bash
# Teste le modèle OpenCacao fusionné directement via transformers (sans vLLM).
# Pratique sur un pod d'entraînement, où vLLM n'est pas installé.
#
# Usage (racine du dépôt, sur le pod) :
#   bash training/scripts/pod_chat.sh
#   bash training/scripts/pod_chat.sh "Comment lutter contre la pourriture brune ?"

set -euo pipefail

MODELE="models/opencacao-7b"
QUESTION="${1:-Mes feuilles de cacaoyer jaunissent et les rameaux gonflent, que faire ?}"
export HF_HOME="${HF_HOME:-/workspace/.hf}"

python - "$MODELE" "$QUESTION" <<'PY'
import sys

import torch
from transformers import AutoModelForImageTextToText, AutoTokenizer

modele, question = sys.argv[1], sys.argv[2]
print("Chargement du modèle (~30 s)...", flush=True)
tok = AutoTokenizer.from_pretrained(modele, fix_mistral_regex=True)
model = AutoModelForImageTextToText.from_pretrained(
    modele, dtype=torch.bfloat16, device_map="auto"
)
model.eval()

messages = [{"role": "user", "content": question}]
inputs = tok.apply_chat_template(
    messages, add_generation_prompt=True, return_tensors="pt", return_dict=True
).to(model.device)
with torch.no_grad():
    sortie = model.generate(
        **inputs, max_new_tokens=350, do_sample=True, temperature=0.3, top_p=0.9
    )
reponse = tok.decode(
    sortie[0][inputs["input_ids"].shape[1] :], skip_special_tokens=True
)
print("\n=== QUESTION ===\n" + question)
print("\n=== RÉPONSE OPENCACAO ===\n" + reponse)
PY
