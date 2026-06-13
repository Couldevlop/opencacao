# syntax=docker/dockerfile:1
# Image d'entraînement GPU (CUDA). Exécutée ponctuellement sur GPU loué.
# PyTorch récent requis pour charger Ministral 3 (déc. 2025).
FROM pytorch/pytorch:2.5.1-cuda12.1-cudnn9-runtime

ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/cache/huggingface

WORKDIR /workspace

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY scripts ./scripts

# Par défaut : entraînement LoRA sur le corpus RAG + le corpus de démarrage.
# Surchargé par docker-compose.training.yml si besoin.
CMD ["python", "scripts/train_lora.py", \
     "--corpus", "/corpus/corpus_cacao_rag.jsonl", "/corpus/corpus_cacao_demarrage.jsonl", \
     "--output", "/models/lora-adapter"]
