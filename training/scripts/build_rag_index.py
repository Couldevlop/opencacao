"""Construit l'index RAG à partir du corpus (embeddings via llama.cpp).

Pour chaque paire {instruction, output}, vectorise l'INSTRUCTION (clé de
recherche) via le service d'embeddings (OpenAI-compatible) et stocke
{texte: output, source, vecteur} en JSONL. À relancer après enrichissement du
corpus (notamment corpus_cure.jsonl) : un fait validé devient récupérable
**sans réentraînement**.

Usage :
    python training/scripts/build_rag_index.py \\
        --sources corpus/corpus_cacao_rag.jsonl corpus/corpus_cacao_demarrage.jsonl \\
                  corpus/corpus_cure.jsonl \\
        --embeddings-url http://localhost:8001 --out rag_index.jsonl
"""

from __future__ import annotations

import argparse
import json
import logging
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("build_rag_index")

_SOURCES = ("CNRA", "ANADER", "Conseil du Café-Cacao", "FAO")


def _source(output: str) -> str:
    """Première source reconnue citée dans le texte, ou chaîne vide."""
    for source in _SOURCES:
        if source.lower() in output.lower():
            return source
    return ""


def _embed(url: str, textes: list[str], lot: int = 32) -> list[list[float]]:
    """Vectorise les textes par lots via le service d'embeddings."""
    vecteurs: list[list[float]] = []
    for debut in range(0, len(textes), lot):
        morceau = textes[debut : debut + lot]
        corps = json.dumps({"input": morceau, "model": "embeddings"}).encode()
        requete = urllib.request.Request(  # noqa: S310 - URL interne maîtrisée
            url.rstrip("/") + "/v1/embeddings",
            data=corps,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(requete, timeout=120) as reponse:  # noqa: S310
            donnees = json.loads(reponse.read())["data"]
        vecteurs.extend(item["embedding"] for item in donnees)
        logger.info("  embeddings %d/%d", min(debut + lot, len(textes)), len(textes))
    return vecteurs


def _charger_paires(sources: list[Path]) -> list[tuple[str, str]]:
    """Lit les paires (instruction, output) non vides des fichiers corpus."""
    paires: list[tuple[str, str]] = []
    for source in sources:
        if not source.exists():
            logger.warning("source absente (ignorée) : %s", source)
            continue
        for ligne in source.read_text(encoding="utf-8").splitlines():
            ligne = ligne.strip()
            if not ligne:
                continue
            try:
                paire = json.loads(ligne)
            except json.JSONDecodeError:
                continue
            instruction = str(paire.get("instruction", "")).strip()
            output = str(paire.get("output", "")).strip()
            if instruction and output:
                paires.append((instruction, output))
    return paires


def construire(sources: list[Path], embeddings_url: str, sortie: Path) -> int:
    """Construit l'index et l'écrit en JSONL. Retourne le nombre d'entrées."""
    paires = _charger_paires(sources)
    if not paires:
        logger.error("aucune paire à indexer")
        return 0
    vecteurs = _embed(embeddings_url, [instruction for instruction, _ in paires])
    sortie.parent.mkdir(parents=True, exist_ok=True)
    with sortie.open("w", encoding="utf-8") as handle:
        for (_, output), vecteur in zip(paires, vecteurs, strict=True):
            # Arrondi à 6 décimales : index ~2x plus léger, sans impact sur le cosinus.
            vecteur = [round(x, 6) for x in vecteur]
            handle.write(
                json.dumps(
                    {"texte": output, "source": _source(output), "vecteur": vecteur},
                    ensure_ascii=False,
                )
                + "\n"
            )
    logger.info("Index RAG écrit : %d entrées -> %s", len(paires), sortie)
    return len(paires)


def main() -> int:
    """Point d'entrée CLI. Retourne 0 si l'index contient au moins une entrée."""
    parser = argparse.ArgumentParser(description="Construit l'index RAG.")
    parser.add_argument("--sources", type=Path, nargs="+", required=True)
    parser.add_argument("--embeddings-url", default="http://localhost:8001")
    parser.add_argument("--out", type=Path, default=Path("rag_index.jsonl"))
    args = parser.parse_args()
    return 0 if construire(args.sources, args.embeddings_url, args.out) else 1


if __name__ == "__main__":
    raise SystemExit(main())
