"""Enrichissement automatique programmé du RAG (exécuté par un CronJob Kubernetes).

Télécharge les sources officielles (idempotent : ne reprend que les nouvelles ou
mises à jour) puis les constitue dans l'index RAG, et recharge l'API. Réutilise la
même logique que la console (`PipelineService`) ; les jobs apparaissent dans le
suivi de la console.

Lancement : ``python -m app.curation.enrichir``.
"""

from __future__ import annotations

import asyncio

from app.core import email
from app.core.logging import configure_logging, get_logger
from app.curation.jobs import JobsRegistry
from app.curation.pipeline import PipelineService

logger = get_logger(__name__)


async def executer() -> None:
    """Cherche et découvre de nouvelles sources, puis PRÉVIENT — sans indexer.

    **Pourquoi ce cron n'indexe plus.** Le 19/08/2026, `Politique-LCB-FT-FIRCA.pdf`
    — une politique de conformité anti-blanchiment, publiée par une source parfaitement
    officielle et parfaitement étrangère à l'agronomie — est entrée dans l'index et a
    capté la question « quel est le rôle du FIRCA ? » : la réponse est passée de trois
    sources et une confiance élevée à un exposé sur le blanchiment de capitaux.

    Ce jour-là quelqu'un regardait. La nuit, personne ne regarde. L'inspection des
    quatre sites officiels montre qu'ils publient un mélange d'agronomie et
    d'administratif — rapports financiers, chartes, politiques qualité — et que le
    second est majoritaire chez certains. Indexer sans filtre, c'est accepter que
    n'importe lequel de ces documents s'invite dans les réponses aux producteurs.

    On ne prétend pas trier automatiquement ce qui est « agronomique » : c'est
    justement ce qu'on ne sait pas faire de façon fiable à partir d'un PDF. On remet
    donc l'humain dans la boucle — la file de revue de la console existe pour ça — et
    on le prévient qu'il a du travail, sans quoi on aurait seulement remplacé une
    indexation aveugle par un corpus qui ne grandit plus.
    """
    jobs = JobsRegistry.from_env()
    jobs.reconcilier_orphelins()
    pipeline = PipelineService.from_env(jobs)

    recherche = await jobs.creer("recherche_sources")
    await pipeline.collecter_sources(recherche["id"])

    decouverte = await jobs.creer("decouverte_sources")
    await pipeline.decouvrir_sources(decouverte["id"])

    job = await jobs.obtenir(decouverte["id"]) or {}
    telecharges = int((job.get("details") or {}).get("telecharges", 0))
    if not telecharges:
        logger.info("enrichissement_aucune_nouveaute")
        return

    logger.info("enrichissement_documents_a_revoir", documents=telecharges)
    await email.envoyer_alerte(
        f"OpenCacao — {telecharges} document(s) officiel(s) à revoir",
        f"La découverte nocturne a rapatrié {telecharges} nouveau(x) document(s) "
        "depuis les sites officiels de la filière.\n\n"
        "Ils ne sont PAS indexés : depuis l'incident du 19/08/2026, l'indexation "
        "automatique est désactivée. Un document officiel mais hors sujet "
        "agronomique dégrade les réponses faites aux producteurs.\n\n"
        "À faire : ouvrir la console de curation, écarter ce qui n'est pas "
        "agronomique, puis lancer « Constituer le RAG ».\n\n"
        "Mesurez le rappel avant et après : l'index précédent reste sur le volume.",
    )


async def _executer_supervise() -> None:
    """Exécute l'enrichissement et alerte par email en cas d'échec, puis relève."""
    try:
        await executer()
    except Exception as exc:  # noqa: BLE001 - on alerte puis on relève (job en échec)
        logger.error("enrichissement_programme_echec", error=str(exc))
        await email.envoyer_alerte(
            "⚠ OpenCacao — échec de l'enrichissement quotidien",
            "Le CronJob d'enrichissement du RAG (recherche + découverte + "
            "constitution) a échoué.\n\n"
            f"Erreur : {exc}\n\n"
            "Consultez les journaux du job :\n"
            "  kubectl -n opencacao logs job/<nom-du-job> --tail=200\n"
            "  kubectl -n opencacao get jobs -l app=enrichissement",
        )
        raise


def main() -> None:
    """Point d'entrée du CronJob."""
    configure_logging("INFO")
    logger.info("enrichissement_programme_debut")
    asyncio.run(_executer_supervise())
    logger.info("enrichissement_programme_fin")


if __name__ == "__main__":  # pragma: no cover
    main()
