# Guide d'entraînement — OpenCacao-7B

Ce guide détaille la section 6 de [`CLAUDE_OpenCacao.md`](../CLAUDE_OpenCacao.md).
L'entraînement est **ponctuel** : on loue un GPU le temps du fine-tuning, puis on
libère l'instance.

## Prérequis

- Un GPU **16–20 Go de VRAM suffisent** pour le LoRA 4-bit (QLoRA) — ex. RTX 4000
  Ada 20 Go, RTX 3090/4090 24 Go. RunPod / Vast.ai (~0,30–0,70 USD/h). 24 Go =
  plus de confort (contexte/batch), pas une obligation.
- Un token Hugging Face avec accès à `mistralai/Mistral-7B-Instruct-v0.3`
  (variable `HF_TOKEN`).
- Le corpus généré `corpus/corpus_cacao_rag.jsonl` (voir
  [`corpus_rag_guide.md`](corpus_rag_guide.md)), entraîné avec le corpus de
  démarrage.

## Chemin recommandé sur pod RunPod (sans Docker)

Un pod RunPod est déjà un conteneur CUDA : on n'y lance pas `docker compose`.
Deux scripts turnkey font tout, après `git clone` du dépôt et `export HF_TOKEN` :

```bash
bash training/scripts/pod_generate.sh 10000   # 1) génère le corpus (sert Mistral via vLLM)
bash training/scripts/pod_train.sh            # 2) entraîne le LoRA + fusionne le modèle
```

Le chemin Docker ci-dessous (`make train`) reste valable sur une **VM GPU**
disposant de Docker + nvidia-container-runtime (reproductibilité, versions
PyTorch épinglées).

## Étapes

### 1. Valider le corpus

```bash
make corpus-check
# ou directement :
python training/scripts/enrich_corpus.py --check corpus/corpus_cacao_demarrage.jsonl
```

Le validateur vérifie : JSON valide, champs obligatoires, longueurs, absence de
dosages phytosanitaires chiffrés, présence d'au moins une source citée.

### 2. Lancer l'entraînement (sur l'instance GPU)

```bash
git clone https://github.com/Couldevlop/opencacao.git && cd opencacao
export HF_TOKEN=hf_xxx
make train
# ou directement :
docker compose -f docker-compose.training.yml up --build
```

L'entraînement porte sur `corpus/corpus_cacao_rag.jsonl` **+** le corpus de
démarrage (voir la commande dans `docker-compose.training.yml`).

Configuration LoRA et hyperparamètres : épinglés dans `training/scripts/train_lora.py`
(r=16, alpha=32, 4-bit nf4, 3 époques, lr=2e-4, scheduler cosine). Découpage
train/validation 90/10, seed=42.

Sortie : adaptateur LoRA (~150 Mo) dans `models/lora-adapter/`.
Durée typique pour 500 paires sur 3 époques : 30 à 90 min selon le GPU.

### 3. Fusionner l'adaptateur avec le modèle de base

```bash
make merge
# ou :
python training/scripts/merge_and_export.py \
    --base mistralai/Mistral-7B-Instruct-v0.3 \
    --adapter models/lora-adapter \
    --output models/opencacao-7b
```

Sortie : modèle fusionné dans `models/opencacao-7b/`, prêt pour vLLM.

### 4. (Optionnel) Export GGUF pour le service CPU

Pour servir le modèle sans GPU via llama-cpp, convertir le modèle fusionné au format
GGUF quantifié (Q4_K_M) avec les outils `llama.cpp` :

```bash
python convert_hf_to_gguf.py models/opencacao-7b --outfile models/opencacao-7b.gguf
llama-quantize models/opencacao-7b.gguf models/opencacao-7b-Q4_K_M.gguf Q4_K_M
```

### 5. Libérer l'instance GPU

Récupérer les artefacts (`models/`), puis arrêter l'instance.

## Reproductibilité

Toutes les versions sont épinglées (`training/requirements.txt`), le seed est fixé
à 42, et le modèle fusionné est versionné par hash SHA-256.
