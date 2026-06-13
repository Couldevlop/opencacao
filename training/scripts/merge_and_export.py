"""Fusion de l'adaptateur LoRA avec le modèle de base (CLAUDE §6).

Produit le modèle fusionné ``opencacao-7b/`` prêt pour vLLM, et écrit un hash
SHA-256 du modèle pour le versionnement (CLAUDE §11.2).

Usage :
    python training/scripts/merge_and_export.py \
        --base mistralai/Mistral-7B-Instruct-v0.3 \
        --adapter models/lora-adapter \
        --output models/opencacao-7b
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer


def _hash_dossier(dossier: Path) -> str:
    """Calcule un SHA-256 stable sur les fichiers de poids du dossier."""
    sha = hashlib.sha256()
    for fichier in sorted(dossier.rglob("*")):
        if fichier.is_file() and fichier.suffix in {".safetensors", ".bin"}:
            sha.update(fichier.name.encode())
            with fichier.open("rb") as handle:
                for bloc in iter(lambda: handle.read(1 << 20), b""):
                    sha.update(bloc)
    return sha.hexdigest()


def fusionner(base: str, adapter: Path, output: Path) -> None:
    """Fusionne l'adaptateur dans le modèle de base et exporte le résultat.

    Args:
        base: Identifiant ou chemin du modèle de base.
        adapter: Dossier de l'adaptateur LoRA.
        output: Dossier de sortie du modèle fusionné.
    """
    tokenizer = AutoTokenizer.from_pretrained(base)
    model = AutoModelForCausalLM.from_pretrained(base, torch_dtype=torch.bfloat16)

    model = PeftModel.from_pretrained(model, str(adapter))
    model = model.merge_and_unload()

    output.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(output), safe_serialization=True)
    tokenizer.save_pretrained(str(output))

    digest = _hash_dossier(output)
    (output / "SHA256SUM").write_text(f"{digest}  opencacao-7b\n", encoding="utf-8")
    print(f"Modèle fusionné exporté dans {output}")
    print(f"SHA-256 : {digest}")


def main() -> None:
    """Point d'entrée CLI."""
    parser = argparse.ArgumentParser(description="Fusion LoRA + modèle de base.")
    parser.add_argument("--base", default="mistralai/Mistral-7B-Instruct-v0.3")
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("models/opencacao-7b"))
    args = parser.parse_args()

    fusionner(args.base, args.adapter, args.output)


if __name__ == "__main__":
    main()
