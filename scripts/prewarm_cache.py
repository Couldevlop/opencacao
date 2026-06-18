"""Pré-chauffe le cache de réponses en posant les questions fréquentes à l'API.

Les réponses sont mises en cache par l'API (TTL 7 jours) : les visiteurs obtiennent
ensuite une réponse INSTANTANÉE sur ces classiques, sans attendre l'inférence CPU.
À relancer après chaque redéploiement du modèle (le cache repart à vide).

Usage:
    python scripts/prewarm_cache.py [URL_BASE]
    OPENCACAO_URL=https://opencacao.openlabconsulting.com python scripts/prewarm_cache.py

L'envoi est séquentiel : chaque question attend la réponse (~30 s en CPU), ce qui
reste sous la limite de débit (20 req/min/IP). Compter ~15-20 min pour la liste.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Source unique de la liste FAQ : le module de l'API (api/app). Le script tourne
# depuis la racine du dépôt -> on ajoute api/ au chemin d'import.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

from app.application.faq import QUESTIONS_FAQ as QUESTIONS  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("prewarm")


def _demander(base: str, question: str) -> int:
    """Pose une question à l'API et retourne le code HTTP."""
    corps = json.dumps({"question": question, "langue": "fr", "canal": "web"}).encode()
    requete = urllib.request.Request(  # noqa: S310 - URL maîtrisée (paramètre interne)
        base.rstrip("/") + "/v1/chat",
        data=corps,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(requete, timeout=300) as reponse:  # noqa: S310
        return reponse.status


def main() -> int:
    """Pré-chauffe le cache et retourne 0 si au moins une réponse a réussi."""
    base = (
        sys.argv[1]
        if len(sys.argv) > 1
        else os.environ.get("OPENCACAO_URL", "http://localhost:8080")
    )
    logger.info("Pré-chauffe du cache sur %s — %d questions", base, len(QUESTIONS))

    reussites = 0
    for index, question in enumerate(QUESTIONS, start=1):
        debut = time.monotonic()
        try:
            statut = _demander(base, question)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning(
                "[%2d/%d] ÉCHEC (%s) — %s", index, len(QUESTIONS), exc, question
            )
            continue
        duree = time.monotonic() - debut
        if statut == 200:
            reussites += 1
            logger.info(
                "[%2d/%d] OK en %5.1fs — %s", index, len(QUESTIONS), duree, question
            )
        else:
            logger.warning(
                "[%2d/%d] HTTP %s — %s", index, len(QUESTIONS), statut, question
            )

    logger.info("Terminé : %d/%d réponses en cache.", reussites, len(QUESTIONS))
    return 0 if reussites else 1


if __name__ == "__main__":
    raise SystemExit(main())
