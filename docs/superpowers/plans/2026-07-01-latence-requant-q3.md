# Latence — re-quantification Q3 du modèle affiné — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (sprint ops/empirique — exécution inline avec KUBECONFIG). Steps use checkbox (`- [ ]`) tracking. Le juge est l'A/B qualité/latence, pas des tests unitaires.

**Goal:** Produire un GGUF Q3_K_M de notre Ministral-8B affiné, prouver A/B qu'il est ≥ ~10 % plus rapide SANS régression qualité, puis le déployer (+ KV cache q4, RAG cap 400) avec rollback Q4 instantané.

**Architecture:** Job K8s ponctuel de quantification sur le nœud (le Q4 source y est déjà) → benchmark tok/s (llama-bench) + A/B qualité sur pod de test isolé → déploiement gaté → rollback = repointer sur le Q4 conservé.

**Tech Stack:** llama.cpp (`ghcr.io/ggml-org/llama.cpp:full` pour quantize/bench ; `:server` en prod), K8s (Job/Deployment/ConfigMap), hostPath `/opt/opencacao/models`, `curl` pour l'A/B qualité.

## Global Constraints

- **Identité NON négociable** : on re-quantifie NOTRE modèle affiné (Ministral-8B + LoRA). Aucun fine-tune, aucun modèle tiers. Le pivot reste notre modèle entraîné.
- **Gate qualité bloquant** : aucun déploiement du Q3 en prod sans A/B montrant qualité ≈ Q4 (aucune régression factuelle/garde-fou) ET latence ≥ ~10 % meilleure.
- **Rollback instantané** : `opencacao-8b-Q4_K_M.gguf` reste sur le nœud ; repointer `inference.yaml` dessus + réappliquer.
- Souveraineté : outils locaux (llama.cpp), aucun LLM tiers.
- Toutes les commandes cluster : `export KUBECONFIG=kubeconfig-hetzner.yaml`, namespace `opencacao`.
- Commits sans signature ni mention d'outil IA.
- Nœud : 32 Go RAM, ~9 Go libres ; Q3_K_M ≈ 3,7 Go (tient pendant le job, Q4 source conservé).

---

### Task 1: Produire le GGUF Q3_K_M (+ benchmark tok/s)

**Files:**
- Create: `deploy/k8s/jobs/quantize-q3.yaml` (Job ponctuel)

**Interfaces:**
- Consumes: `/opt/opencacao/models/opencacao-8b-Q4_K_M.gguf` (présent sur le nœud).
- Produces: `/opt/opencacao/models/opencacao-8b-Q3_K_M.gguf` ; chiffres tok/s Q3 vs Q4.

- [ ] **Step 1 : Écrire le Job de quantification**

Create `deploy/k8s/jobs/quantize-q3.yaml` :

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: quantize-q3
  namespace: opencacao
spec:
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: quantize
          image: ghcr.io/ggml-org/llama.cpp:full
          command: ["/bin/sh", "-c"]
          args:
            - |
              set -e
              cd /models
              echo "== quantize Q4_K_M -> Q3_K_M =="
              ./llama-quantize opencacao-8b-Q4_K_M.gguf opencacao-8b-Q3_K_M.gguf Q3_K_M
              ls -lh opencacao-8b-Q3_K_M.gguf
          volumeMounts:
            - name: modeles
              mountPath: /models
          resources:
            requests: { cpu: "2", memory: "3Gi" }
            limits: { cpu: "8", memory: "8Gi" }
      volumes:
        - name: modeles
          hostPath:
            path: /opt/opencacao/models
            type: Directory
```

> Note : l'entrée de l'image `:full` peut préfixer les binaires par `llama-` sans `./`.
> Si `./llama-quantize` échoue (introuvable), utiliser `llama-quantize` (dans le PATH).

- [ ] **Step 2 : Lancer le Job et vérifier la production**

```bash
export KUBECONFIG=kubeconfig-hetzner.yaml
kubectl apply -f deploy/k8s/jobs/quantize-q3.yaml
kubectl -n opencacao wait --for=condition=complete job/quantize-q3 --timeout=900s
kubectl -n opencacao logs job/quantize-q3 | tail -20
```
Expected : log « quantize … », taille finale ~3,5-3,8 Go.

Vérifier le fichier sur le nœud :
```bash
kubectl -n opencacao exec deploy/inference -- sh -c "ls -lh /models/opencacao-8b-Q3_K_M.gguf"
```
Expected : le fichier existe (~3,7 Go), le Q4 source toujours présent.

- [ ] **Step 3 : Benchmark objectif tok/s (Q3 vs Q4) via llama-bench**

Job de bench (les deux modèles, même nœud/CPU) — Create `deploy/k8s/jobs/bench-q3.yaml` :

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: bench-q3
  namespace: opencacao
spec:
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: bench
          image: ghcr.io/ggml-org/llama.cpp:full
          command: ["/bin/sh", "-c"]
          args:
            - |
              set -e
              cd /models
              for m in opencacao-8b-Q4_K_M.gguf opencacao-8b-Q3_K_M.gguf; do
                echo "== $m =="
                ./llama-bench -m "$m" -t 12 -p 256 -n 128 2>&1 | tail -6 || \
                llama-bench -m "$m" -t 12 -p 256 -n 128 2>&1 | tail -6
              done
          volumeMounts:
            - name: modeles
              mountPath: /models
          resources:
            requests: { cpu: "2", memory: "3Gi" }
            limits: { cpu: "12", memory: "9Gi" }
      volumes:
        - name: modeles
          hostPath:
            path: /opt/opencacao/models
            type: Directory
```

```bash
kubectl apply -f deploy/k8s/jobs/bench-q3.yaml
kubectl -n opencacao wait --for=condition=complete job/bench-q3 --timeout=600s
kubectl -n opencacao logs job/bench-q3
```
Relever `tg` (tokens/s génération) et `pp` (prompt eval) pour Q3 vs Q4.
**Note du gain** : Q3 tg ÷ Q4 tg. Objectif ≥ ~1,10.

- [ ] **Step 4 : Commit le manifeste des jobs**

```bash
git add deploy/k8s/jobs/quantize-q3.yaml deploy/k8s/jobs/bench-q3.yaml
git commit -m "ops(latence): jobs de quantification Q3 et de benchmark tok/s"
```

---

### Task 2: A/B qualité sur pod de test isolé (le gate)

**Files:**
- Create: `deploy/k8s/jobs/inference-test-q3.yaml` (Deployment + Service temporaires)

**Interfaces:**
- Consumes: `/models/opencacao-8b-Q3_K_M.gguf` (Task 1).
- Produces: verdict qualité Q3 vs Q4 (gate).

- [ ] **Step 1 : Déployer une inférence de test servant le Q3**

Create `deploy/k8s/jobs/inference-test-q3.yaml` (calque de `inference.yaml`, nom `inference-test`, KV q4, GGUF Q3) :

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: inference-test
  namespace: opencacao
spec:
  replicas: 1
  selector: { matchLabels: { app: inference-test } }
  template:
    metadata: { labels: { app: inference-test } }
    spec:
      containers:
        - name: llamacpp
          image: ghcr.io/ggml-org/llama.cpp:server
          args:
            - "-m"
            - "/models/opencacao-8b-Q3_K_M.gguf"
            - "--alias"
            - "opencacao-8b"
            - "--host"
            - "0.0.0.0"
            - "--port"
            - "8000"
            - "-c"
            - "4096"
            - "-t"
            - "12"
            - "-fa"
            - "on"
            - "--cache-type-k"
            - "q4_0"
            - "--cache-type-v"
            - "q4_0"
            - "-b"
            - "2048"
            - "-ub"
            - "512"
          ports: [{ containerPort: 8000 }]
          volumeMounts:
            - { name: modele, mountPath: /models, readOnly: true }
          resources:
            requests: { cpu: "2", memory: "4Gi" }
            limits: { cpu: "12", memory: "9Gi" }
      volumes:
        - name: modele
          hostPath: { path: /opt/opencacao/models, type: Directory }
---
apiVersion: v1
kind: Service
metadata: { name: inference-test, namespace: opencacao }
spec:
  selector: { app: inference-test }
  ports: [{ port: 8000, targetPort: 8000 }]
```

```bash
kubectl apply -f deploy/k8s/jobs/inference-test-q3.yaml
kubectl -n opencacao rollout status deployment/inference-test --timeout=300s
```

- [ ] **Step 2 : A/B qualité — mêmes prompts sur Q3 (test) et Q4 (prod)**

Depuis le pod API (qui voit les deux services internes), envoyer un prompt REPRÉSENTATIF
(system + une question) aux deux inférences et comparer. Exemple minimal (chat completions) :

```bash
kubectl -n opencacao exec deploy/api -- sh -c '
Q="Comment reconnaitre le swollen shoot du cacaoyer ?"
BODY=$(printf "{\"model\":\"opencacao-8b\",\"messages\":[{\"role\":\"user\",\"content\":\"%s\"}],\"max_tokens\":300,\"temperature\":0.2}" "$Q")
for URL in http://inference:8000 http://inference-test:8000; do
  echo "== $URL =="
  wget -qO- --header="Content-Type: application/json" --post-data="$BODY" "$URL/v1/chat/completions" | head -c 700; echo
done
'
```
Répéter avec ~6-8 questions (agronomie, prix, taille, ombrage, hors-filière, densité).
**Juger** : le Q3 reste-t-il factuellement correct, cohérent, sans dérive vs Q4 ?

> Le chemin API complet (garde-fous, RAG, prix officiel) reste porté par le code, pas
> par le modèle : l'A/B ici teste la QUALITÉ BRUTE du modèle Q3. Les garde-fous
> (prix 1200, refus hors-filière) seront revalidés end-to-end en Task 3 après bascule.

- [ ] **Step 3 : GATE — décision**

- **PASS** si Q3 : qualité ≈ Q4 (aucune régression factuelle nette) ET tg ≥ ~1,10× (Task 1 step 3). → continuer Task 3.
- **FAIL qualité** : supprimer le Q3, envisager l'escalade f16→Q3 (hors ce plan, box scratch) OU abandonner le levier 1 (garder Q4). → NE PAS déployer le Q3.
- Consigner la décision + les chiffres.

- [ ] **Step 4 : Démonter le pod de test (libère la RAM du nœud)**

```bash
kubectl delete -f deploy/k8s/jobs/inference-test-q3.yaml
```
> Toujours démonter avant la Task 3 : deux inférences 8B ne tiennent pas ensemble en RAM.

---

### Task 3: Déploiement gaté (Q3 + KV q4 + cap 400) & vérification prod

**Files:**
- Modify: `deploy/k8s/inference.yaml` (GGUF Q3 + KV q4)
- Modify: `deploy/k8s/api.yaml` (`MODEL_VERSION`, `RAG_PASSAGE_MAX_CHARS`)

**Précondition : le GATE de Task 2 est PASS.** Sinon, sauter cette tâche.

- [ ] **Step 1 : Pointer l'inférence sur le Q3 + KV q4**

Dans `deploy/k8s/inference.yaml` : remplacer `/models/opencacao-8b-Q4_K_M.gguf` par
`/models/opencacao-8b-Q3_K_M.gguf` ; remplacer les deux `q8_0` (`--cache-type-k`/`-v`)
par `q4_0`.

- [ ] **Step 2 : Tracer la version modèle + cap RAG**

Dans `deploy/k8s/api.yaml` : `MODEL_VERSION: "1.1.0"` → `"1.2.0"` (Q3) ;
`RAG_PASSAGE_MAX_CHARS: "480"` → `"400"`.

- [ ] **Step 3 : Commit + livrer via la routine**

```bash
git add deploy/k8s/inference.yaml deploy/k8s/api.yaml
git commit -m "perf(latence): servir le modèle affiné en Q3_K_M + KV cache q4 + cap RAG 400"
git push origin develop
gh pr create --base main --head develop --title "perf(latence): modèle affiné en Q3_K_M (A/B validé)" --body "Re-quantification Q3 de notre Ministral-8B affiné (pivot intact, sans fine-tune) + KV q4 + cap 400. A/B validé : qualité ≈ Q4, latence ≥ 10%. Rollback Q4 conservé sur le nœud."
gh pr merge <num> --merge
git checkout develop && git merge --ff-only origin/main && git push origin develop
```
Attendre `release.yml` (le manifeste n'affecte pas l'image API mais on garde la routine) ; relever le tag.

- [ ] **Step 4 : Appliquer inférence + ConfigMap en prod**

```bash
export KUBECONFIG=kubeconfig-hetzner.yaml
kubectl apply -f deploy/k8s/inference.yaml
kubectl -n opencacao rollout status deployment/inference --timeout=300s
kubectl -n opencacao patch configmap api-config --type merge -p '{"data":{"MODEL_VERSION":"1.2.0","RAG_PASSAGE_MAX_CHARS":"400"}}'
bash deploy/scripts/roll-image.sh <X.Y.Z>   # aligne APP_VERSION + purge cache
```

- [ ] **Step 5 : Vérification prod end-to-end (garde-fous + latence)**

Warmup puis mesurer via `POST /v1/chat` (JSON UTF-8 depuis fichier) : latence (baseline
~15 s à battre) ET garde-fous — prix → **1200 FCFA** ; « comment cultiver le maïs » →
**refus/redirection** ; une question agronomie → réponse correcte non tronquée ;
`/v1/version` doit montrer `model_version: 1.2.0`.

**Gate final** : latence en baisse ≥ ~10 % ET garde-fous intacts. Sinon → rollback.

- [ ] **Step 6 : (si régression) Rollback instantané**

```bash
# inference.yaml repointé sur Q4 + KV q8 (git checkout du fichier), puis :
kubectl apply -f deploy/k8s/inference.yaml
kubectl -n opencacao rollout status deployment/inference --timeout=300s
```
(Le `opencacao-8b-Q4_K_M.gguf` est resté sur le nœud.)

---

### Task 4: Vérification suite + clôture

- [ ] **Step 1 : Suite pytest (sécurité — rien dans api/app n'a changé)**

Run: `cd api && pytest -q`
Expected: PASS, couverture ≥ 97 % (aucune régression : seuls des manifestes ont changé).

- [ ] **Step 2 : Nettoyer les Jobs terminés**

```bash
export KUBECONFIG=kubeconfig-hetzner.yaml
kubectl -n opencacao delete job quantize-q3 bench-q3 --ignore-not-found
```

- [ ] **Step 3 : Consigner les mesures** (tok/s Q3 vs Q4, latence prod avant/après, verdict qualité) dans la mémoire chantier-latence.

---

## Self-Review

**Spec coverage :**
- Levier 1 (Q3 re-quant) → Task 1 (production + bench) + Task 2 (A/B) + Task 3 (deploy). ✓
- Levier 2 (KV q4) → Task 2 (pod test) + Task 3 step 1. ✓
- Levier 3 (RAG cap 400) → Task 3 step 2. ✓
- Gate A/B bloquant → Task 2 step 3 + Task 3 step 5. ✓
- Rollback Q4 conservé → Task 3 step 6. ✓
- Identité (notre modèle, pas de fine-tune) → Global Constraints. ✓
- Escalade f16→Q3 si qualité échoue → Task 2 step 3 (hors ce plan, noté). ✓

**Placeholder scan :** `<num>`/`<X.Y.Z>` = valeurs runtime (n° PR, tag). Le reste est concret (YAML complet, commandes exactes).

**Type consistency :** noms de fichiers GGUF (`opencacao-8b-Q3_K_M.gguf`), noms de ressources (`quantize-q3`, `bench-q3`, `inference-test`), clés ConfigMap (`MODEL_VERSION`, `RAG_PASSAGE_MAX_CHARS`) cohérents entre tâches.
