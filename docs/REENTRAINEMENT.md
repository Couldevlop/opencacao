# Réentraînement OpenCacao-8B — cycle d'amélioration continue

Ce runbook formalise la boucle : **interactions réelles → curation → corpus →
LoRA → GGUF → redéploiement**. L'entraînement tourne sur un **pod GPU loué**
(RunPod, ~24 Go) ; le service tourne en CPU/GGUF sur le cluster K3s (Hetzner).

> ## Depuis la console de curation (boutons)
>
> La console (`curation.opencacao.openlabconsulting.com`) expose deux actions du
> pipeline, avec un **suivi des jobs** (statut, message, log) :
>
> - **🔁 Reconstruire le RAG** — ajoute les faits **curés** à l'index RAG
>   (vectorisation via le service d'embeddings interne), puis **redémarre l'API**
>   (rolling, sans coupure). Reconstruction **additive** : ne retire jamais
>   d'entrée existante. Remplace les étapes manuelles RAG 3-4 ci-dessous pour
>   l'enrichissement courant.
> - **🎓 Préparer le fine-tuning** — assemble/valide/dédoublonne le corpus curé
>   en `corpus_entrainement_cure.jsonl` (volume partagé) et affiche la **procédure
>   exacte** à lancer sur un pod GPU. Le CPU du cluster ne peut pas entraîner :
>   la console *prépare et instruit*, l'opérateur *déclenche* le pod (étapes 3-5).
>
> Permission cluster minimale (moindre privilège) : la console a un ServiceAccount
> autorisé à `get/patch` le **seul** Deployment `api` (cf. `deploy/k8s/curation-rbac.yaml`).

> Cadence conseillée : déclencher quand la console de curation a accumulé un lot
> significatif de paires validées (≥ quelques dizaines), ou périodiquement.

## 1. Récupérer le corpus curé (depuis le cluster)

Les réponses validées par un expert via la **console de curation** sont stockées
dans `corpus_cure.jsonl` sur le volume du cluster. Rapatrie-les :

```bash
export KUBECONFIG=...            # accès au cluster
make corpus-cure                 # -> corpus/corpus_cure.jsonl
```

## 2. Assembler le corpus d'entraînement

Combine les sources (RAG + démarrage + refus + curé), **valide** (longueurs,
sources, aucun dosage) et **déduplique** :

```bash
make corpus-assemble             # -> corpus/corpus_entrainement.jsonl
```

Les paires invalides ou en double sont écartées et comptées (rien de silencieux).

## 3. Entraîner sur le pod GPU

Sur le pod (dépôt cloné, corpus présent, `HF_TOKEN` exporté) :

```bash
export HF_TOKEN=hf_xxx
bash training/scripts/pod_train.sh
```

`pod_train.sh` ré-assemble le corpus (y compris `corpus_cure.jsonl`), fait un
smoke-test, entraîne le LoRA 4-bit, puis fusionne l'adaptateur avec la base
(`models/opencacao-8b/`).

## 4. Exporter en GGUF (CPU)

```bash
bash training/scripts/pod_gguf.sh           # -> models/opencacao-8b-Q4_K_M.gguf
```

Récupère le GGUF sur ton PC (`runpodctl send` / `receive`).

## 5. Redéployer sur le cluster

```bash
export KUBECONFIG=...
make redeploy-model GGUF=models/opencacao-8b-Q4_K_M.gguf VERSION=1.1.0
```

Le script : envoie le GGUF sur le nœud → redémarre l'inférence (recharge le
modèle) → **purge le cache** (sinon d'anciennes réponses seraient resservies) →
bumpe `MODEL_VERSION`. Reporte la version dans `deploy/k8s/api.yaml` puis commit.

## 6. Re-pré-chauffer le cache

Le cache étant vide, repeuple les FAQ pour des réponses instantanées :

```bash
python scripts/prewarm_cache.py https://opencacao.openlabconsulting.com
```

## RAG — apprendre des faits sans réentraînement

Le RAG (génération augmentée par récupération) permet d'intégrer un fait validé
**immédiatement**, sans réentraîner : on récupère à la requête les passages les
plus proches du corpus et on les injecte dans le prompt. Choix d'architecture :
**embeddings via llama.cpp** (modèle multilingue local) + **index NumPy en
mémoire** (pas de base vectorielle externe — souverain et instantané à cette
échelle). Désactivé par défaut (`RAG_ENABLED=false`).

Activation :

1. **Déposer un modèle d'embeddings GGUF** (multilingue, ~100-150 Mo) sur le nœud :
   `/opt/opencacao/models/embeddings.gguf`.
2. **Déployer le service d'embeddings** (interne) :
   `kubectl -n opencacao apply -f deploy/k8s/embeddings.yaml`
3. **Construire l'index** (via le service d'embeddings, par port-forward) :
   ```bash
   kubectl -n opencacao port-forward svc/embeddings 8001:8001 &
   make rag-index                       # -> rag_index.jsonl
   ```
   Puis déposer `rag_index.jsonl` sur le volume partagé (`/data/rag_index.jsonl`).
4. **Activer** : `RAG_ENABLED=true` dans le ConfigMap `api-config`, puis
   `kubectl -n opencacao rollout restart deploy/api`.

À chaque enrichissement du corpus curé, **reconstruire l'index** (étape 3) suffit
pour que les nouveaux faits soient récupérables — sans réentraînement.

## Garde-fous (rappel)

- Aucune paire contenant un **dosage phytosanitaire chiffré** n'entre dans le
  corpus (validé à la curation **et** à l'assemblage).
- Validation humaine **obligatoire** : on n'entraîne jamais sur la sortie brute
  du modèle, uniquement sur des réponses revues par un expert.
- Le corpus reste **privé** (dépôt public) — jamais committé tel quel.
