# Réentraînement OpenCacao-8B — cycle d'amélioration continue

Ce runbook formalise la boucle : **interactions réelles → curation → corpus →
LoRA → GGUF → redéploiement**. L'entraînement tourne sur un **pod GPU loué**
(RunPod, ~24 Go) ; le service tourne en CPU/GGUF sur le cluster K3s (Hetzner).

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

## Garde-fous (rappel)

- Aucune paire contenant un **dosage phytosanitaire chiffré** n'entre dans le
  corpus (validé à la curation **et** à l'assemblage).
- Validation humaine **obligatoire** : on n'entraîne jamais sur la sortie brute
  du modèle, uniquement sur des réponses revues par un expert.
- Le corpus reste **privé** (dépôt public) — jamais committé tel quel.
