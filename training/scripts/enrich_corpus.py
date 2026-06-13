"""Validation et enrichissement du corpus d'instruction-tuning.

Vérifie chaque paire Q/R (CLAUDE §5.3) : JSON valide, champs obligatoires,
longueurs, absence de dosages phytosanitaires chiffrés, présence d'une source.

Usage :
    python training/scripts/enrich_corpus.py --check corpus/corpus_cacao_demarrage.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

CHAMPS_OBLIGATOIRES = ("instruction", "input", "output")
MIN_INSTRUCTION, MAX_INSTRUCTION = 10, 500
MIN_OUTPUT, MAX_OUTPUT = 50, 2000

# Sources reconnues (au moins une attendue dans les réponses techniques).
SOURCES = ("CNRA", "ANADER", "Conseil du Café-Cacao", "FAO")

# Dosage chiffré associé à un produit phytosanitaire — interdit dans le corpus.
_RE_DOSAGE = re.compile(
    r"\d+\s?(ml|cl|l|g|kg|grammes?|litres?|cc|cm3)\b.*"
    r"(fongicide|insecticide|herbicide|pesticide|phytosanitaire|acaricide)",
    re.IGNORECASE,
)
_RE_DOSAGE_INVERSE = re.compile(
    r"(fongicide|insecticide|herbicide|pesticide|phytosanitaire|acaricide).*"
    r"\d+\s?(ml|cl|l|g|kg|grammes?|litres?|cc|cm3)\b",
    re.IGNORECASE,
)


@dataclass
class Probleme:
    """Anomalie détectée sur une ligne du corpus."""

    ligne: int
    message: str


def _valider_paire(numero: int, paire: dict[str, object]) -> list[Probleme]:
    problemes: list[Probleme] = []

    for champ in CHAMPS_OBLIGATOIRES:
        if champ not in paire:
            problemes.append(Probleme(numero, f"champ obligatoire manquant : '{champ}'"))

    instruction = str(paire.get("instruction", ""))
    output = str(paire.get("output", ""))

    if not MIN_INSTRUCTION <= len(instruction) <= MAX_INSTRUCTION:
        problemes.append(
            Probleme(numero, f"longueur instruction hors bornes ({len(instruction)})")
        )
    if not MIN_OUTPUT <= len(output) <= MAX_OUTPUT:
        problemes.append(Probleme(numero, f"longueur output hors bornes ({len(output)})"))

    if _RE_DOSAGE.search(output) or _RE_DOSAGE_INVERSE.search(output):
        problemes.append(Probleme(numero, "dosage phytosanitaire chiffré détecté (interdit)"))

    if not any(source.lower() in output.lower() for source in SOURCES):
        problemes.append(Probleme(numero, "aucune source citée"))

    return problemes


def valider_corpus(chemin: Path) -> list[Probleme]:
    """Valide un fichier corpus JSONL et retourne la liste des problèmes.

    Args:
        chemin: Chemin du fichier JSONL.

    Returns:
        Liste des anomalies (vide si le corpus est valide).
    """
    problemes: list[Probleme] = []
    with chemin.open(encoding="utf-8") as handle:
        for numero, ligne in enumerate(handle, start=1):
            ligne = ligne.strip()
            if not ligne:
                continue
            try:
                paire = json.loads(ligne)
            except json.JSONDecodeError as exc:
                problemes.append(Probleme(numero, f"JSON invalide : {exc.msg}"))
                continue
            problemes.extend(_valider_paire(numero, paire))
    return problemes


def main() -> int:
    """Point d'entrée CLI. Retourne 0 si valide, 1 sinon."""
    parser = argparse.ArgumentParser(description="Validation du corpus OpenCacao.")
    parser.add_argument("corpus", type=Path, help="Fichier JSONL à valider.")
    parser.add_argument("--check", action="store_true", help="Mode validation (défaut).")
    args = parser.parse_args()

    if not args.corpus.exists():
        print(f"Fichier introuvable : {args.corpus}", file=sys.stderr)
        return 1

    problemes = valider_corpus(args.corpus)
    if problemes:
        for p in problemes:
            print(f"  ligne {p.ligne}: {p.message}", file=sys.stderr)
        print(f"\n{len(problemes)} problème(s) détecté(s).", file=sys.stderr)
        return 1

    print(f"Corpus valide : {args.corpus}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
