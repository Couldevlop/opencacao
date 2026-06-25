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
    """Exécute un cycle complet : recherche + découverte de sources, puis constitution."""
    jobs = JobsRegistry.from_env()
    jobs.reconcilier_orphelins()
    pipeline = PipelineService.from_env(jobs)

    recherche = await jobs.creer("recherche_sources")
    await pipeline.collecter_sources(recherche["id"])

    decouverte = await jobs.creer("decouverte_sources")
    await pipeline.decouvrir_sources(decouverte["id"])

    constitution = await jobs.creer("rag_constitution")
    await pipeline.constituer_rag(constitution["id"])


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
