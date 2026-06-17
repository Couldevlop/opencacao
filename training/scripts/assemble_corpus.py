"""Assemble le corpus d'entraînement à partir de plusieurs fichiers JSONL.

Combine le corpus existant et le corpus **curé** (``corpus_cure.jsonl``, issu de
la console de curation), déduplique par instruction normalisée, **valide** chaque
paire avec les mêmes règles que ``enrich_corpus`` (champs, longueurs, aucun dosage
chiffré, source citée) et écrit un corpus unique prêt pour l'entraînement LoRA.
Les paires invalides ou en double sont écartées et comptées (jamais silencieux).

Usage :
    python training/scripts/assemble_corpus.py \\
        --sources corpus/corpus_cacao_rag.jsonl corpus/corpus_cacao_demarrage.jsonl \\
                  corpus/corpus_refus.jsonl corpus/corpus_cure.jsonl \\
        --out corpus/corpus_entrainement.jsonl
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import unicodedata
from pathlib import Path

from enrich_corpus import _valider_paire

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("assemble_corpus")

_ESPACES = re.compile(r"\s+")


def _cle(instruction: str) -> str:
    """Clé de déduplication : minuscules, sans accents, espaces normalisés."""
    sans_accents = "".join(
        c
        for c in unicodedata.normalize("NFKD", instruction)
        if not unicodedata.combining(c)
    )
    return _ESPACES.sub(" ", sans_accents.lower()).strip()


def assembler(sources: list[Path], sortie: Path) -> dict[str, int]:
    """Combine, valide et déduplique les sources vers un corpus unique.

    Args:
        sources: Fichiers JSONL d'entrée (ordre = priorité ; le 1er gagne en doublon).
        sortie: Fichier JSONL combiné à écrire.

    Returns:
        Statistiques : lues, invalides, doublons, gardees.
    """
    vues: set[str] = set()
    gardees: list[dict] = []
    stats = {"lues": 0, "invalides": 0, "doublons": 0, "gardees": 0}

    for source in sources:
        if not source.exists():
            logger.warning("source absente (ignorée) : %s", source)
            continue
        for numero, ligne in enumerate(
            source.read_text(encoding="utf-8").splitlines(), start=1
        ):
            ligne = ligne.strip()
            if not ligne:
                continue
            stats["lues"] += 1
            try:
                paire = json.loads(ligne)
            except json.JSONDecodeError:
                stats["invalides"] += 1
                logger.warning("  %s:%d JSON invalide (écarté)", source.name, numero)
                continue
            problemes = _valider_paire(numero, paire)
            if problemes:
                stats["invalides"] += 1
                logger.warning(
                    "  %s:%d écarté — %s", source.name, numero, problemes[0].message
                )
                continue
            cle = _cle(str(paire["instruction"]))
            if cle in vues:
                stats["doublons"] += 1
                continue
            vues.add(cle)
            gardees.append(
                {
                    "instruction": paire["instruction"],
                    "input": paire.get("input", ""),
                    "output": paire["output"],
                }
            )

    sortie.parent.mkdir(parents=True, exist_ok=True)
    with sortie.open("w", encoding="utf-8") as handle:
        for paire in gardees:
            handle.write(json.dumps(paire, ensure_ascii=False) + "\n")
    stats["gardees"] = len(gardees)
    return stats


def main() -> int:
    """Point d'entrée CLI. Retourne 0 si au moins une paire est gardée."""
    parser = argparse.ArgumentParser(description="Assemble le corpus d'entraînement.")
    parser.add_argument("--sources", type=Path, nargs="+", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    stats = assembler(args.sources, args.out)
    logger.info(
        "Corpus assemblé : %d gardées, %d doublons, %d invalides (sur %d lues) -> %s",
        stats["gardees"],
        stats["doublons"],
        stats["invalides"],
        stats["lues"],
        args.out,
    )
    return 0 if stats["gardees"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
