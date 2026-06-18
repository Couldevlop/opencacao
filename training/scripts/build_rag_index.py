"""Construit l'index RAG à partir du corpus (embeddings via llama.cpp).

Pour chaque paire {instruction, output}, vectorise l'INSTRUCTION (clé de
recherche) via le service d'embeddings (OpenAI-compatible) et stocke
{texte: output, source, vecteur} en JSONL. À relancer après enrichissement du
corpus (notamment corpus_cure.jsonl) : un fait validé devient récupérable
**sans réentraînement**.

La logique pure (lecture des paires, détection de source, écriture de l'index)
est partagée avec la console de curation via ``app.services.rag_index_builder``.

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
import sys
import urllib.request
from pathlib import Path

# Le module partagé vit dans l'API (api/app). Le script est lancé depuis la racine
# du dépôt (cloné sur le pod) : on ajoute api/ au chemin d'import.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api"))

from app.services.rag_index_builder import (  # noqa: E402
    charger_paires,
    construire_entrees,
    ecrire_index,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("build_rag_index")


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


def construire(sources: list[Path], embeddings_url: str, sortie: Path) -> int:
    """Construit l'index (reconstruction complète) et l'écrit. Retourne le nombre d'entrées."""
    paires = charger_paires(sources)
    if not paires:
        logger.error("aucune paire à indexer")
        return 0
    vecteurs = _embed(embeddings_url, [instruction for instruction, _ in paires])
    entrees = construire_entrees(paires, vecteurs)
    ecrire_index(sortie, entrees)
    logger.info("Index RAG écrit : %d entrées -> %s", len(entrees), sortie)
    return len(entrees)


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
