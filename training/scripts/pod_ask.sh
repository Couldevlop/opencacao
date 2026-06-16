#!/usr/bin/env bash
# Pose une question à OpenCacao servi en local (port 8000) et affiche la réponse.
#
# Usage (depuis la racine du dépôt, sur le pod, serveur déjà lancé) :
#   bash training/scripts/pod_ask.sh
#   bash training/scripts/pod_ask.sh "Comment lutter contre la pourriture brune ?"
#
# Sans argument : pose une question de démonstration (swollen shoot).

set -euo pipefail

QUESTION="${1:-Mes feuilles de cacaoyer jaunissent et les rameaux gonflent, que faire ?}"

python - "${QUESTION}" <<'PY'
import json
import sys
import urllib.request

question = sys.argv[1]
charge = json.dumps(
    {
        "model": "opencacao",
        "messages": [{"role": "user", "content": question}],
        "max_tokens": 350,
        "temperature": 0.3,
    }
).encode("utf-8")
requete = urllib.request.Request(
    "http://localhost:8000/v1/chat/completions",
    data=charge,
    headers={"Content-Type": "application/json"},
)
reponse = json.load(urllib.request.urlopen(requete, timeout=180))
print("\n=== QUESTION ===")
print(question)
print("\n=== RÉPONSE OPENCACAO ===")
print(reponse["choices"][0]["message"]["content"])
PY
