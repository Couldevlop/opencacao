# Guide d'entraînement — OpenCacao-7B

Ce guide détaille la section 6 de [`CLAUDE_OpenCacao.md`](../CLAUDE_OpenCacao.md).
L'entraînement est **ponctuel** : on loue un GPU le temps du fine-tuning, puis on
libère l'instance.

## Prérequis

- Un GPU avec au moins **24 Go de VRAM** (RTX 4090, A10G, A100…) — RunPod, Vast.ai,
  Lambda Labs (~1-2 USD/h).
- Un token Hugging Face avec accès à `mistralai/Mistral-7B-Instruct-v0.3`
  (variable `HF_TOKEN`).
- Un corpus validé d'au moins quelques centaines de paires (objectif : 500+).

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
git clone <dépôt> && cd opencacao
export HF_TOKEN=hf_xxx
make train
# ou :
docker compose -f docker-compose.training.yml up --build
```

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
