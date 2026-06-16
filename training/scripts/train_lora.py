"""Fine-tuning LoRA 4-bit de Ministral 3 8B sur le corpus cacao (CLAUDE §6.1).

Hyperparamètres épinglés. Exécuté ponctuellement sur GPU 24 Go.

Ministral 3 8B est un modèle **multimodal** (architecture ``mistral3`` =
``ministral3`` texte + ``pixtral`` vision) : on le charge donc via
``AutoModelForImageTextToText`` et non ``AutoModelForCausalLM``. La version
Instruct officielle est en FP8, incompatible avec la quantification 4-bit de
bitsandbytes ; on entraîne sur la variante **BF16** (``…-2512-BF16``). Le LoRA
ne cible que le modèle de langue (pas la tour vision), via une regex sur les
modules ``language_model``.

Usage :
    python training/scripts/train_lora.py \
        --corpus corpus/corpus_cacao_rag.jsonl corpus/corpus_cacao_demarrage.jsonl \
        --output models/lora-adapter

    # validation rapide du pipeline (charge le modèle + 3 steps, jetable) :
    python training/scripts/train_lora.py --corpus corpus/*.jsonl \
        --output /tmp/lora-smoke --max-steps 3
"""

from __future__ import annotations

import argparse
from pathlib import Path

import torch
from datasets import load_dataset
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from transformers import (
    AutoModelForImageTextToText,
    AutoTokenizer,
    BitsAndBytesConfig,
)
from trl import SFTConfig, SFTTrainer

# Variante BF16 : la version Instruct par défaut est en FP8, qu'on ne peut pas
# re-quantifier proprement en 4-bit (QLoRA). La BF16 est la bonne base d'affinage.
BASE_MODEL = "mistralai/Ministral-3-8B-Instruct-2512-BF16"
SEED = 42

# Le LoRA ne cible QUE le modèle de langue : regex (fullmatch PEFT) sur les
# projections d'attention/MLP situées sous ``language_model`` afin d'exclure la
# tour vision ``pixtral`` (inutile pour un corpus 100 % texte).
LORA_TARGET_MODULES = (
    r".*language_model.*\.(q_proj|k_proj|v_proj|o_proj|gate_proj|up_proj|down_proj)"
)

LORA_CONFIG = {
    "r": 16,
    "lora_alpha": 32,
    "lora_dropout": 0.05,
    "target_modules": LORA_TARGET_MODULES,
    "bias": "none",
    "task_type": "CAUSAL_LM",
}

TRAINING_ARGS = {
    "num_train_epochs": 1,
    "per_device_train_batch_size": 4,
    "gradient_accumulation_steps": 4,
    "learning_rate": 2e-4,
    "warmup_ratio": 0.03,
    "lr_scheduler_type": "cosine",
    "logging_steps": 10,
    "save_strategy": "epoch",
    "eval_strategy": "no",
    # Indispensable sur un GPU 24 Go modeste (ex. L4) : réduit la mémoire
    # d'activation et évite que paged_adamw_8bit ne swappe (cause d'une lenteur
    # extrême ~200 s/pas).
    "gradient_checkpointing": True,
    "gradient_checkpointing_kwargs": {"use_reentrant": False},
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


def entrainer(corpus: list[Path], output: Path, max_steps: int = -1) -> None:
    """Lance le fine-tuning LoRA et écrit l'adaptateur dans ``output``.

    Args:
        corpus: Un ou plusieurs fichiers JSONL d'entraînement (fusionnés).
        output: Dossier de sortie de l'adaptateur LoRA.
        max_steps: Si > 0, plafonne le nombre de pas (smoke-test rapide) ;
            -1 (défaut) entraîne sur le nombre d'epochs configuré.
    """
    quantization = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
    )

    # fix_mistral_regex=True : corrige le motif regex du tokenizer tekken
    # (sinon tokenisation incorrecte, cf. avertissement transformers 5.x).
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL, fix_mistral_regex=True)
    tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"

    model = AutoModelForImageTextToText.from_pretrained(
        BASE_MODEL,
        quantization_config=quantization,
        device_map="auto",
        dtype=torch.bfloat16,
    )
    model = prepare_model_for_kbit_training(model)
    model = get_peft_model(model, LoraConfig(**LORA_CONFIG))
    model.print_trainable_parameters()

    dataset = load_dataset("json", data_files=[str(c) for c in corpus], split="train")
    dataset = dataset.map(
        lambda ex: _format_exemple(ex, tokenizer), remove_columns=dataset.column_names
    )
    split = dataset.train_test_split(test_size=0.1, seed=SEED)

    sft_config = SFTConfig(
        output_dir=str(output),
        dataset_text_field="text",
        max_length=1024,
        # packing désactivé : sans Flash-Attention (indisponible sur ce stack
        # CUDA 13 / torch 2.11), le packing provoque une cross-contamination de
        # l'attention entre paires -> dégrade la qualité. Le collateur padde
        # alors dynamiquement à la longueur réelle (paires courtes) -> rapide ET
        # propre. Le gain de vitesse vient de gradient_checkpointing.
        packing=False,
        max_steps=max_steps,
        **TRAINING_ARGS,
    )
    trainer = SFTTrainer(
        model=model,
        args=sft_config,
        train_dataset=split["train"],
        eval_dataset=split["test"],
        processing_class=tokenizer,
    )

    trainer.train()
    trainer.save_model(str(output))
    tokenizer.save_pretrained(str(output))
    print(f"Adaptateur LoRA enregistré dans {output}")


def main() -> None:
    """Point d'entrée CLI."""
    parser = argparse.ArgumentParser(description="Fine-tuning LoRA OpenCacao.")
    parser.add_argument("--corpus", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, default=Path("models/lora-adapter"))
    parser.add_argument(
        "--max-steps",
        type=int,
        default=-1,
        help="Plafond de pas (>0 = smoke-test) ; -1 utilise les epochs.",
    )
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    entrainer(args.corpus, args.output, max_steps=args.max_steps)


if __name__ == "__main__":
    main()
