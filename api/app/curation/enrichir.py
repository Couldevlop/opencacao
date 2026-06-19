"""Enrichissement automatique programmé du RAG (exécuté par un CronJob Kubernetes).

Télécharge les sources officielles (idempotent : ne reprend que les nouvelles ou
mises à jour) puis les constitue dans l'index RAG, et recharge l'API. Réutilise la
même logique que la console (`PipelineService`) ; les jobs apparaissent dans le
suivi de la console.

Lancement : ``python -m app.curation.enrichir``.
"""

from __future__ import annotations

import asyncio

from app.core.logging import configure_logging, get_logger
from app.curation.jobs import JobsRegistry
from app.curation.pipeline import PipelineService

logger = get_logger(__name__)


async def executer() -> None:
    """Exécute un cycle complet : recherche des sources puis constitution RAG."""
    jobs = JobsRegistry.from_env()
    jobs.reconcilier_orphelins()
    pipeline = PipelineService.from_env(jobs)

    recherche = await jobs.creer("recherche_sources")
    await pipeline.collecter_sources(recherche["id"])

    constitution = await jobs.creer("rag_constitution")
    await pipeline.constituer_rag(constitution["id"])


def main() -> None:
    """Point d'entrée du CronJob."""
    configure_logging("INFO")
    logger.info("enrichissement_programme_debut")
    asyncio.run(executer())
    logger.info("enrichissement_programme_fin")


if __name__ == "__main__":
    main()
