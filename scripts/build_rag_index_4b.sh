#!/usr/bin/env bash
# Construit l'index RAG avec Qwen3-Embedding-4B (2560-dim), hors-ligne et SANS
# toucher à la prod : un pod embeddings 4B ÉPHÉMÈRE (embeddings-build) est déployé
# le temps du build, le pod embeddings de prod (146 Mo) continue de servir l'index
# actuel. Le résultat (rag_index_4b.jsonl) est écrit en local, prêt pour la bascule.
#
# Le build_rag_index.py applique déjà le préfixe d'instruction Qwen3 (commit develop)
# — index et requête partageront donc le même format.
#
# Durée : ~30-45 min sur le nœud (10k passages, 4B CPU). À lancer en heures creuses.
#
# Usage :
#   KUBECONFIG=kubeconfig-hetzner.yaml scripts/build_rag_index_4b.sh
#
# Prérequis :
#   - /opt/opencacao/models/embeddings-qwen3-4b.gguf présent sur le nœud (déjà déposé).
#   - kubectl configuré (KUBECONFIG), corpus local présent (corpus/*.jsonl).
set -euo pipefail

NS="${NS:-opencacao}"
PORT_LOCAL="${PORT_LOCAL:-18010}"
OUT="${OUT:-rag_index_4b.jsonl}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

cleanup() {
  echo "→ Démontage du pod de build"
  [ -n "${PF_PID:-}" ] && kill "$PF_PID" 2>/dev/null || true
  kubectl -n "$NS" delete deploy/embeddings-build svc/embeddings-build --ignore-not-found >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "→ 1/5 Rapatriement du corpus_cure.jsonl prod (paires curées via console)"
if kubectl -n "$NS" exec deploy/api -- test -f /data/corpus_cure.jsonl 2>/dev/null; then
  kubectl -n "$NS" exec deploy/api -- cat /data/corpus_cure.jsonl > corpus/corpus_cure.jsonl
  echo "  corpus_cure.jsonl : $(wc -l < corpus/corpus_cure.jsonl) paires"
else
  echo "  (aucun corpus_cure prod — on garde la copie locale)"
fi

echo "→ 2/5 Déploiement du pod embeddings 4B éphémère (embeddings-build)"
kubectl -n "$NS" apply -f - <<'YAML'
apiVersion: apps/v1
kind: Deployment
metadata: { name: embeddings-build, labels: { app: embeddings-build } }
spec:
  replicas: 1
  selector: { matchLabels: { app: embeddings-build } }
  strategy: { type: Recreate }
  template:
    metadata: { labels: { app: embeddings-build } }
    spec:
      containers:
        - name: llamacpp
          image: ghcr.io/ggml-org/llama.cpp:server
          args: ["--embeddings","-m","/models/embeddings-qwen3-4b.gguf",
                  "--host","0.0.0.0","--port","8001",
                  "-c","2048","-b","2048","-ub","512","-t","8"]   # build : 8 threads (nuit)
          ports: [{ containerPort: 8001 }]
          volumeMounts: [{ name: modele, mountPath: /models, readOnly: true }]
          resources:
            requests: { cpu: "2", memory: "5Gi" }
            limits: { cpu: "8", memory: "8Gi" }
          readinessProbe:
            httpGet: { path: /health, port: 8001 }
            initialDelaySeconds: 20
            periodSeconds: 10
            failureThreshold: 40
      volumes: [{ name: modele, hostPath: { path: /opt/opencacao/models, type: Directory } }]
---
apiVersion: v1
kind: Service
metadata: { name: embeddings-build }
spec:
  selector: { app: embeddings-build }
  ports: [{ port: 8001, targetPort: 8001 }]
YAML

echo "→ 3/5 Attente de la disponibilité du pod 4B (chargement ~4,3 Go)…"
kubectl -n "$NS" rollout status deploy/embeddings-build --timeout=300s

echo "→ 4/5 Port-forward + construction de l'index"
kubectl -n "$NS" port-forward svc/embeddings-build "${PORT_LOCAL}:8001" >/tmp/pf_build.log 2>&1 &
PF_PID=$!
sleep 6
curl -sf --max-time 10 "http://127.0.0.1:${PORT_LOCAL}/health" >/dev/null \
  || { echo "  ✗ embeddings-build injoignable"; cat /tmp/pf_build.log; exit 1; }

python training/scripts/build_rag_index.py \
  --sources corpus/corpus_cacao_rag.jsonl corpus/corpus_cacao_demarrage.jsonl corpus/corpus_cure.jsonl \
  --embeddings-url "http://127.0.0.1:${PORT_LOCAL}" \
  --out "$OUT"

echo "→ 5/5 Vérification de l'index produit"
python - "$OUT" <<'PY'
import json, sys
chemin = sys.argv[1]
n = 0; dim = None
with open(chemin, encoding="utf-8") as f:
    for ligne in f:
        ligne = ligne.strip()
        if not ligne:
            continue
        n += 1
        if dim is None:
            dim = len(json.loads(ligne)["vecteur"])
print(f"  index : {n} entrées | dimension = {dim}")
assert dim == 2560, f"dimension inattendue ({dim}) — le pod ne sert pas le 4B ?"
print("  ✓ OK (Qwen3-4B = 2560-dim). Prêt pour la bascule : voir docs/MIGRATION_embeddings_4b.md")
PY
