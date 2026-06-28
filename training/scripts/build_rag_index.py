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
import http.client
import json
import logging
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Le module partagé vit dans l'API (api/app). Le script est lancé depuis la racine
# du dépôt (cloné sur le pod) : on ajoute api/ au chemin d'import.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api"))

from app.services.rag_index_builder import (  # noqa: E402
    ajouter_entrees,
    charger_paires,
    construire_entrees,
    filtrer_nouvelles,
    formater_pour_embedding,
    lire_textes_indexes,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("build_rag_index")

_LOT = 32
# Nombre de tentatives par lot. Le backoff exponentiel (1+2+4+8+16+32 ≈ 63 s) couvre
# un redémarrage de port-forward, fréquent sur un build long de plusieurs milliers
# de passages — sans quoi une déconnexion transitoire ferait tout échouer.
_TENTATIVES = 6
# Erreurs réseau transitoires à réessayer (déconnexion du tunnel, timeout, 5xx).
_ERREURS_TRANSITOIRES = (
    urllib.error.URLError,
    http.client.HTTPException,
    OSError,
    json.JSONDecodeError,
    KeyError,
)


def _embed_batch(
    url: str, textes: list[str], tentatives: int = _TENTATIVES
) -> list[list[float]]:
    """Vectorise UN lot via le service d'embeddings, avec retry sur erreur transitoire.

    Args:
        url: Base du service d'embeddings (OpenAI-compatible).
        textes: Instructions du lot (préfixées Qwen3 ici, comme à la requête).
        tentatives: Nombre d'essais avant abandon (backoff exponentiel).

    Returns:
        Les vecteurs du lot, dans l'ordre.

    Raises:
        RuntimeError: si le lot échoue après toutes les tentatives.
    """
    entrees = [formater_pour_embedding(t) for t in textes]
    corps = json.dumps({"input": entrees, "model": "embeddings"}).encode()
    derniere: Exception | None = None
    for essai in range(tentatives):
        try:
            requete = urllib.request.Request(  # noqa: S310 - URL interne maîtrisée
                url.rstrip("/") + "/v1/embeddings",
                data=corps,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(requete, timeout=180) as reponse:  # noqa: S310
                donnees = json.loads(reponse.read())["data"]
            return [item["embedding"] for item in donnees]
        except _ERREURS_TRANSITOIRES as exc:
            derniere = exc
            attente = 2**essai
            logger.warning(
                "  lot KO (essai %d/%d) : %s — nouvelle tentative dans %ds",
                essai + 1,
                tentatives,
                exc,
                attente,
            )
            time.sleep(attente)
    raise RuntimeError(
        f"embeddings : échec du lot après {tentatives} tentatives"
    ) from derniere


def construire(sources: list[Path], embeddings_url: str, sortie: Path) -> int:
    """Construit l'index de façon **résumable** et l'écrit. Retourne le nombre d'entrées.

    Reprise : les réponses déjà présentes dans une sortie partielle (build interrompu)
    sont sautées — on ne ré-embedde que le manquant, et chaque lot est ajouté
    immédiatement (append). Un incident ne fait donc perdre qu'un lot, pas tout.
    """
    paires = charger_paires(sources)
    if not paires:
        logger.error("aucune paire à indexer")
        return 0
    deja = lire_textes_indexes(sortie)  # reprise : réponses déjà écrites
    a_faire = filtrer_nouvelles(deja, paires)
    if deja:
        logger.info("reprise : %d déjà indexées, %d restantes", len(deja), len(a_faire))
    for debut in range(0, len(a_faire), _LOT):
        morceau = a_faire[debut : debut + _LOT]
        vecteurs = _embed_batch(
            embeddings_url, [instruction for instruction, _ in morceau]
        )
        ajouter_entrees(sortie, construire_entrees(morceau, vecteurs))
        logger.info("  index %d/%d", min(debut + _LOT, len(a_faire)), len(a_faire))
    total = len(lire_textes_indexes(sortie))
    logger.info("Index RAG écrit : %d entrées -> %s", total, sortie)
    return total


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
