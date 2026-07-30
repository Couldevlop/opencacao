"""Pré-chauffe le cache de réponses en posant des questions à l'API.

Les réponses sont mises en cache par l'API (TTL 7 jours) : les visiteurs obtiennent
ensuite une réponse INSTANTANÉE sur ces questions, sans attendre l'inférence CPU. Le
cache exact et le cache sémantique sont alimentés par le même appel — rien de plus à
faire pour le second, l'API l'indexe si ``SEMANTIC_CACHE_ENABLED`` est levé.

**Le piège à connaître (spec §9.3).** La clé de cache inclut ``APP_VERSION`` : un
déploiement postérieur au pré-chauffage **invalide tout**. Le pré-chauffage est donc la
DERNIÈRE opération avant une démonstration. Ce script relève la version au début et à
la fin, et échoue bruyamment si elle a bougé — sans quoi on entrerait en scène en
croyant le cache chaud alors qu'il est vide.

Usage:
    python scripts/prewarm_cache.py [URL_BASE]
    python scripts/prewarm_cache.py URL --questions docs/demo/questions.txt
    python scripts/prewarm_cache.py URL --verifier

Options:
    --questions FICHIER  Une question par ligne (``#`` = commentaire). S'ajoute à la
                         FAQ, ou la remplace avec --seulement-fichier.
    --seulement-fichier  N'utilise que le fichier, sans la FAQ.
    --verifier           Repose chaque question après coup et vérifie qu'elle revient
                         vite. C'est la seule preuve que le cache est réellement chaud.

L'envoi est séquentiel : chaque question attend sa réponse (~30 s en CPU), ce qui reste
sous la limite de débit (20 req/min/IP). Compter ~15-20 min pour la FAQ complète, le
double avec --verifier.
"""

from __future__ import annotations

import argparse
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

from app.application.faq import QUESTIONS_FAQ  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("prewarm")

# Au-delà de ce délai, une réponse n'a manifestement pas été servie par le cache : elle
# a été générée. Le cache exact répond en dizaines de millisecondes ; la marge est large
# pour absorber le réseau et l'edge.
SEUIL_CACHE_S = 3.0


def lire_questions(chemin: Path) -> list[str]:
    """Lit une liste de questions depuis un fichier texte.

    Args:
        chemin: Fichier contenant une question par ligne. Les lignes vides et celles
            commençant par ``#`` sont ignorées.

    Returns:
        Les questions, dans l'ordre du fichier, sans doublon.
    """
    vues: dict[str, None] = {}
    for ligne in chemin.read_text(encoding="utf-8").splitlines():
        question = ligne.strip()
        if question and not question.startswith("#"):
            vues.setdefault(question, None)
    return list(vues)


def _appeler(
    base: str, chemin: str, corps: dict | None = None, timeout: float = 300.0
) -> tuple[int, dict | None]:
    """Appelle l'API et retourne ``(statut, charge décodée ou None)``."""
    donnees = json.dumps(corps).encode() if corps is not None else None
    requete = urllib.request.Request(  # noqa: S310 - URL maîtrisée (paramètre interne)
        base.rstrip("/") + chemin,
        data=donnees,
        headers={"Content-Type": "application/json"},
        method="POST" if donnees else "GET",
    )
    with urllib.request.urlopen(requete, timeout=timeout) as reponse:  # noqa: S310
        try:
            return reponse.status, json.loads(reponse.read())
        except ValueError:
            return reponse.status, None


def version_applicative(base: str) -> str | None:
    """Relève la version applicative servie, ou None si l'API ne répond pas."""
    try:
        _, charge = _appeler(base, "/v1/version", timeout=20.0)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.warning("Version illisible (%s)", exc)
        return None
    return str(charge.get("api_version")) if charge else None


def _demander(base: str, question: str) -> tuple[int, float]:
    """Pose une question et retourne ``(statut HTTP, durée en secondes)``."""
    debut = time.monotonic()
    statut, _ = _appeler(base, "/v1/chat", {"question": question, "langue": "fr", "canal": "web"})
    return statut, time.monotonic() - debut


def chauffer(base: str, questions: list[str]) -> int:
    """Pose chaque question une fois, pour remplir le cache.

    Args:
        base: URL de base de l'API.
        questions: Questions à mettre en cache.

    Returns:
        Le nombre de réponses obtenues.
    """
    reussites = 0
    total = len(questions)
    for index, question in enumerate(questions, start=1):
        try:
            statut, duree = _demander(base, question)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning("[%2d/%d] ÉCHEC (%s) — %s", index, total, exc, question)
            continue
        if statut == 200:
            reussites += 1
            logger.info("[%2d/%d] OK en %5.1fs — %s", index, total, duree, question)
        else:
            logger.warning("[%2d/%d] HTTP %s — %s", index, total, statut, question)
    return reussites


def verifier(base: str, questions: list[str]) -> list[str]:
    """Repose chaque question et retourne celles qui n'ont PAS été servies par le cache.

    C'est la seule preuve que le pré-chauffage a fonctionné : une question chauffée
    revient en dizaines de millisecondes, une question manquante repaie la génération.
    Sans cette passe, on suppose le cache chaud au lieu de le savoir.

    Args:
        base: URL de base de l'API.
        questions: Questions supposées en cache.

    Returns:
        Les questions restées froides.
    """
    froides: list[str] = []
    total = len(questions)
    for index, question in enumerate(questions, start=1):
        try:
            statut, duree = _demander(base, question)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning("[%2d/%d] injoignable (%s) — %s", index, total, exc, question)
            froides.append(question)
            continue
        if statut != 200 or duree > SEUIL_CACHE_S:
            logger.warning("[%2d/%d] FROIDE (%4.1fs) — %s", index, total, duree, question)
            froides.append(question)
        else:
            logger.info("[%2d/%d] chaude (%4.2fs) — %s", index, total, duree, question)
    return froides


def _analyser_arguments(argv: list[str]) -> argparse.Namespace:
    """Analyse la ligne de commande."""
    analyseur = argparse.ArgumentParser(description="Pré-chauffe le cache de réponses.")
    analyseur.add_argument(
        "url",
        nargs="?",
        default=os.environ.get("OPENCACAO_URL", "http://localhost:8080"),
        help="URL de base de l'API.",
    )
    analyseur.add_argument("--questions", type=Path, help="Fichier de questions (une par ligne).")
    analyseur.add_argument(
        "--seulement-fichier", action="store_true", help="N'utilise que le fichier, sans la FAQ."
    )
    analyseur.add_argument(
        "--verifier",
        action="store_true",
        help="Repose chaque question et vérifie qu'elle revient du cache.",
    )
    return analyseur.parse_args(argv)


def questions_a_chauffer(options: argparse.Namespace) -> list[str]:
    """Assemble la liste des questions selon les options, sans doublon.

    Args:
        options: Options de ligne de commande analysées.

    Returns:
        Les questions à pré-chauffer, dans l'ordre.
    """
    questions = [] if options.seulement_fichier else list(QUESTIONS_FAQ)
    if options.questions:
        questions += [q for q in lire_questions(options.questions) if q not in questions]
    return questions


def main(argv: list[str] | None = None) -> int:
    """Pré-chauffe le cache, puis vérifie si demandé.

    Returns:
        ``0`` si le cache est prêt ; ``1`` si aucune réponse n'a abouti, si des
        questions sont restées froides, ou si la version applicative a changé en cours
        de route — auquel cas le cache vient d'être invalidé.
    """
    options = _analyser_arguments(argv if argv is not None else sys.argv[1:])
    base = options.url

    questions = questions_a_chauffer(options)
    if not questions:
        logger.error("Aucune question à pré-chauffer.")
        return 1

    version_avant = version_applicative(base)
    logger.info(
        "Pré-chauffe sur %s — %d questions (version servie : %s)",
        base,
        len(questions),
        version_avant or "inconnue",
    )

    reussites = chauffer(base, questions)
    logger.info("Chauffé : %d/%d réponses.", reussites, len(questions))

    froides: list[str] = []
    if options.verifier:
        logger.info(
            "Vérification (au-delà de %.0f s, une réponse n'est pas cachée)…", SEUIL_CACHE_S
        )
        froides = verifier(base, questions)

    version_apres = version_applicative(base)
    if version_avant and version_apres and version_avant != version_apres:
        # Le pire cas : croire le cache chaud alors qu'il est vide. Autant le savoir ici
        # que devant huit cents personnes.
        logger.error(
            "LA VERSION A CHANGÉ EN COURS DE ROUTE (%s -> %s) : APP_VERSION entre dans la "
            "clé de cache, donc TOUT le pré-chauffage est invalidé. Recommencez, et ne "
            "déployez plus ensuite.",
            version_avant,
            version_apres,
        )
        return 1

    if froides:
        logger.error("%d question(s) restée(s) froide(s) — le cache n'est pas prêt.", len(froides))
        return 1
    if not reussites:
        return 1
    logger.info("Cache prêt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
