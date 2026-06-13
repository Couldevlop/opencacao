"""Fine-tuning LoRA 4-bit de Ministral 3 8B sur le corpus cacao (CLAUDE §6.1).

Hyperparamètres épinglés. Exécuté ponctuellement sur GPU 24 Go.

Usage :
    python training/scripts/train_lora.py \
        --corpus /corpus/corpus_cacao_rag.jsonl /corpus/corpus_cacao_demarrage.jsonl \
        --output /models/lora-adapter
"""

from __future__ import annotations

import argparse
from pathlib import Path

import torch
from datasets import load_dataset
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
    TrainingArguments,
)
from trl import SFTTrainer

BASE_MODEL = "mistralai/Ministral-3-8B-Instruct-2512"
SEED = 42

LORA_CONFIG = {
    "r": 16,
    "lora_alpha": 32,
    "lora_dropout": 0.05,
    "target_modules": [
        "q_proj",
        "k_proj",
        "v_proj",
        "o_proj",
        "gate_proj",
        "up_proj",
        "down_proj",
    ],
    "bias": "none",
    "task_type": "CAUSAL_LM",
}

TRAINING_ARGS = {
    "num_train_epochs": 3,
    "per_device_train_batch_size": 4,
    "gradient_accumulation_steps": 4,
    "learning_rate": 2e-4,
    "warmup_ratio": 0.03,
    "lr_scheduler_type": "cosine",
    "logging_steps": 10,
    "save_strategy": "epoch",
    "eval_strategy": "epoch",
    "bf16": True,
    "optim": "paged_adamw_8bit",
    "report_to": "none",
    "seed": SEED,
}


def _format_exemple(exemple: dict[str, str], tokenizer: AutoTokenizer) -> dict[str, str]:
    """Met une paire au format de chat du modèle via son propre template.

    Utilise ``tokenizer.apply_chat_template`` plutôt qu'un format codé en dur,
    pour rester correct quel que soit le modèle (Ministral 3, Mistral…).

    Args:
        exemple: Paire ``instruction``/``input``/``output``.
        tokenizer: Tokenizer du modèle de base (fournit le template de chat).

    Returns:
        Dict avec la clé ``text`` (dialogue formaté prêt pour le SFT).
    """
    instruction = exemple["instruction"].strip()
    contexte = exemple.get("input", "").strip()
    prompt = f"{instruction}\n{contexte}".strip() if contexte else instruction
    messages = [
        {"role": "user", "content": prompt},
        {"role": "assistant", "content": exemple["output"].strip()},
    ]
    texte = tokenizer.apply_chat_template(messages, tokenize=False)
    return {"text": texte}


def entrainer(corpus: list[Path], output: Path) -> None:
    """Lance le fine-tuning LoRA et écrit l'adaptateur dans ``output``.

    Args:
        corpus: Un ou plusieurs fichiers JSONL d'entraînement (fusionnés).
        output: Dossier de sortie de l'adaptateur LoRA.
    """
    quantization = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
    )

    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
    tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"

    model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL,
        quantization_config=quantization,
        device_map="auto",
        torch_dtype=torch.bfloat16,
    )
    model = prepare_model_for_kbit_training(model)
    model = get_peft_model(model, LoraConfig(**LORA_CONFIG))
    model.print_trainable_parameters()

    dataset = load_dataset("json", data_files=[str(c) for c in corpus], split="train")
    dataset = dataset.map(
        lambda ex: _format_exemple(ex, tokenizer), remove_columns=dataset.column_names
    )
    split = dataset.train_test_split(test_size=0.1, seed=SEED)

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=split["train"],
        eval_dataset=split["test"],
        dataset_text_field="text",
        max_seq_length=1024,
        args=TrainingArguments(output_dir=str(output), **TRAINING_ARGS),
    )

    trainer.train()
    trainer.save_model(str(output))
    tokenizer.save_pretrained(str(output))
    print(f"Adaptateur LoRA enregistré dans {output}")


def main() -> None:
    """Point d'entrée CLI."""
    parser = argparse.ArgumentParser(description="Fine-tuning LoRA OpenCacao-7B.")
    parser.add_argument("--corpus", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, default=Path("/models/lora-adapter"))
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    entrainer(args.corpus, args.output)


if __name__ == "__main__":
    main()
