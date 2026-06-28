# Migration des embeddings RAG → Qwen3-Embedding-4B

Bascule de l'ancien modèle d'embeddings (XLM-R, 146 Mo, **768-dim**) vers
**Qwen3-Embedding-4B** (Q8, ~4,3 Go, **2560-dim**). Gain : meilleure séparation
sémantique → rappel RAG accru, et cache sémantique exploitable (couplé au garde-fou
lexical déjà livré). Le modèle vit sur le disque du nœud (`hostPath`), pas comme objet k8s.

> ⚠️ **Contrainte de cohérence** : le modèle d'embeddings (4B), l'index RAG (construit
> avec le 4B + préfixe) et le code API (préfixe d'instruction) forment un **tout**.
> Mélanger les versions casse le RAG (dimensions/format incompatibles). On bascule donc
> les trois **ensemble**. Le RAG est *fail-soft* (une incohérence brève = réponse sans
> contexte, jamais une erreur 5xx) et le cache sémantique reste **OFF**.

## Pré-requis (déjà en place)
- Code préfixe d'instruction Qwen3 sur `develop` (`embeddings.py`, `rag_index_builder.py`,
  `build_rag_index.py`) — commit `feat(rag): préfixe d'instruction Qwen3…`.
- GGUF `embeddings-qwen3-4b.gguf` (~4,3 Go) déposé sur le nœud `/opt/opencacao/models/`.
- Garde-fou lexical du cache sémantique livré (PR #54).

---

## Étape 1 — Construire l'index 4B (hors-ligne, ~30-45 min, heures creuses)

```bash
KUBECONFIG=kubeconfig-hetzner.yaml scripts/build_rag_index_4b.sh
```

Le script déploie un pod embeddings 4B **éphémère** (la prod continue de servir
l'ancien index), récupère le `corpus_cure.jsonl` prod (158 paires curées),
construit `rag_index_4b.jsonl` (~10,2 k entrées, **2560-dim**), vérifie la dimension,
puis démonte le pod. Ne touche **rien** en prod.

## Étape 2 — Produire l'image API (code préfixe)

```bash
gh pr create --base main --head develop --title "feat(rag): bascule embeddings Qwen3-4B"
gh pr merge <N> --merge
```

Le merge sur `main` déclenche `release.yml` → image `ghcr.io/couldevlop/opencacao-api:<tag>`.
⚠️ ArgoCD est HS sur ce cluster (K8s 1.35) → **le merge ne déploie pas tout seul**.
Le déploiement reste manuel (étape 3.d). Noter le `<tag>` produit (`gh run list`).

---

## Étape 3 — Bascule (atomique, heures creuses)

```bash
export KUBECONFIG=kubeconfig-hetzner.yaml
NS=opencacao

# a. Sauvegarde de l'index actuel (rollback)
kubectl -n $NS exec deploy/api -- cp /data/rag_index.jsonl /data/rag_index.768.bak

# b. (Prudence) Couper le RAG le temps de la bascule pour éviter toute fenêtre
#    d'incohérence (sinon : fail-soft, contexte dégradé ~1 min). Optionnel.
kubectl -n $NS patch configmap api-config --type merge -p '{"data":{"RAG_ENABLED":"false"}}'

# c. Pousser le nouvel index 4B sur le volume /data (sans tar : flux via cat)
kubectl -n $NS exec -i deploy/api -- sh -c 'cat > /data/rag_index.4b.jsonl' < rag_index_4b.jsonl
kubectl -n $NS exec deploy/api -- sh -c 'mv /data/rag_index.4b.jsonl /data/rag_index.jsonl'

# d. Basculer le pod embeddings sur le 4B (Recreate, ressources relevées)
kubectl -n $NS apply -f deploy/k8s/embeddings-4b.yaml
kubectl -n $NS rollout status deploy/embeddings --timeout=300s

# e. Déployer le code API (préfixe) + purge du cache de réponses
deploy/scripts/roll-image.sh <tag>

# f. Réactiver le RAG (si coupé en b) et redémarrer l'API pour relire la config
kubectl -n $NS patch configmap api-config --type merge -p '{"data":{"RAG_ENABLED":"true"}}'
kubectl -n $NS rollout restart deploy/api && kubectl -n $NS rollout status deploy/api --timeout=300s
```

## Étape 4 — Recalibrer le seuil RAG (le 4B sépare mieux que l'ancien)

`RAG_MIN_SIMILARITE=0.55` était bas car l'ancien modèle séparait mal. Le 4B produit
des cosinus plus francs : commencer plus haut et observer.

```bash
# Sonde : poser 5-6 questions connues, vérifier que les bonnes sources remontent.
# Monter le seuil par paliers (0.60 → 0.70) tant que le rappel reste bon, pour
# couper le bruit. Top_k peut rester à 3.
kubectl -n $NS patch configmap api-config --type merge -p '{"data":{"RAG_MIN_SIMILARITE":"0.65"}}'
kubectl -n $NS rollout restart deploy/api
```

## Étape 5 — Valider
- `curl` quelques questions (cacao) → vérifier sources pertinentes + latence (~+1 s/embed,
  négligeable devant les ~38 s de génération).
- Logs : pas d'`embeddings_indisponible`, pas d'erreur de dimension.
- Surveiller la RAM du nœud (`free -g`) : le 4B prend ~5-6 Go (le nœud a ~20 Go libres).

## Étape 6 — Activer le cache sémantique (optionnel, après validation RAG)
Une fois le 4B en place et le RAG sain :
```bash
kubectl -n $NS patch configmap api-config --type merge -p '{"data":{"SEMANTIC_CACHE_ENABLED":"true"}}'
kubectl -n $NS rollout restart deploy/api
```
Observer les logs `cache_semantique_hit` / `cache_semantique_rejet_lexical`, puis ajuster
`SEMANTIC_CACHE_THRESHOLD` (0.92) et `SEMANTIC_CACHE_LEXICAL_MIN` (0.75) sur trafic réel.

---

## Rollback (si rappel dégradé ou incident)

```bash
export KUBECONFIG=kubeconfig-hetzner.yaml; NS=opencacao
# 1. Restaurer l'ancien index 768-dim
kubectl -n $NS exec deploy/api -- cp /data/rag_index.768.bak /data/rag_index.jsonl
# 2. Restaurer l'ancien pod embeddings (146 Mo)
kubectl -n $NS apply -f deploy/k8s/embeddings.yaml
kubectl -n $NS rollout status deploy/embeddings --timeout=300s
# 3. Revenir à l'image API précédente
deploy/scripts/roll-image.sh <tag-précédent>
```
Les trois éléments redeviennent cohérents (ancien modèle + ancien index + ancien code).
