"""Surveillance des CronJobs d'enrichissement (« dead-man's switch »).

Un job d'enrichissement qui *échoue* alerte lui-même (cf. ``enrichir.py``). Mais un
CronJob **arrêté, suspendu ou jamais déclenché** ne peut pas, par définition,
s'alerter. Ce watchdog comble ce trou : il interroge l'API server pour le CronJob
``enrichissement-rag`` et envoie un email si :

 - le CronJob est **suspendu** (``spec.suspend = true``) ;
 - il n'a **aucune exécution réussie** enregistrée ;
 - sa **dernière réussite est trop ancienne** (au-delà de ``WATCHDOG_MAX_AGE_H``,
   défaut 26 h pour une cadence quotidienne) — couvre l'échec répété comme l'arrêt.

À lancer dans un CronJob indépendant, décalé après l'enrichissement :
``python -m app.curation.watchdog``.

Variables : ``ENRICH_CRONJOB`` (nom, défaut ``enrichissement-rag``),
``WATCHDOG_MAX_AGE_H`` (heures). Email : cf. ``app.core.email``.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

from app.core import email
from app.core.logging import configure_logging, get_logger
from app.curation.k8s import ClusterClient, ClusterIndisponible

logger = get_logger(__name__)

_MAX_AGE_DEFAUT_H = 26.0


def _parse_iso(valeur: str) -> datetime | None:
    """Parse un horodatage RFC3339 (« ...Z ») en datetime aware UTC, ou None."""
    try:
        quand = datetime.fromisoformat(valeur.replace("Z", "+00:00"))
    except ValueError:
        return None
    return quand if quand.tzinfo else quand.replace(tzinfo=UTC)


def analyser(cronjob: dict, maintenant: datetime, max_age_h: float) -> str | None:
    """Analyse l'état d'un CronJob et retourne un message d'alerte, ou None si sain.

    Args:
        cronjob: Objet CronJob (batch/v1) tel que renvoyé par l'API server.
        maintenant: Instant de référence (UTC).
        max_age_h: Âge maximal toléré (heures) depuis la dernière réussite.

    Returns:
        Un message d'alerte si un problème est détecté, sinon None.
    """
    nom = cronjob.get("metadata", {}).get("name", "?")
    spec = cronjob.get("spec", {})
    status = cronjob.get("status", {})

    if spec.get("suspend"):
        return (
            f"Le CronJob « {nom} » est SUSPENDU (spec.suspend=true) : "
            "l'enrichissement quotidien ne s'exécute plus."
        )

    dernier = status.get("lastSuccessfulTime")
    if not dernier:
        return f"Le CronJob « {nom} » n'a aucune exécution réussie enregistrée."

    quand = _parse_iso(str(dernier))
    if quand is None:
        # Horodatage illisible : on évite une fausse alerte.
        logger.warning("watchdog_horodatage_illisible", valeur=dernier)
        return None

    age_h = (maintenant - quand).total_seconds() / 3600
    if age_h > max_age_h:
        return (
            f"Le CronJob « {nom} » n'a pas réussi depuis {age_h:.0f} h "
            f"(dernière réussite : {dernier}, seuil : {max_age_h:.0f} h). "
            "Il est probablement arrêté ou en échec répété — vérifiez :\n"
            "  kubectl -n opencacao get cronjob,jobs -l app=enrichissement"
        )
    return None


async def executer() -> bool:
    """Interroge le CronJob et alerte si nécessaire.

    Returns:
        True si une alerte a été envoyée, False si tout va bien (ou si le cluster
        est inaccessible — auquel cas l'incident est journalisé, sans email).
    """
    nom = os.environ.get("ENRICH_CRONJOB", "enrichissement-rag")
    max_age_h = float(os.environ.get("WATCHDOG_MAX_AGE_H", _MAX_AGE_DEFAUT_H))
    try:
        client = ClusterClient.from_serviceaccount()
    except ClusterIndisponible as exc:
        logger.warning("watchdog_hors_cluster", error=str(exc))
        return False

    try:
        cronjob = await client.get_json(
            f"/apis/batch/v1/namespaces/{client.namespace}/cronjobs/{nom}"
        )
    except ClusterIndisponible as exc:
        logger.error("watchdog_lecture_echec", error=str(exc))
        return False
    finally:
        await client.close()

    alerte = analyser(cronjob, datetime.now(UTC), max_age_h)
    if alerte is None:
        logger.info("watchdog_ok", cronjob=nom)
        return False

    logger.warning("watchdog_alerte", cronjob=nom, message=alerte)
    await email.envoyer_alerte(f"⚠ OpenCacao — supervision du CronJob « {nom} »", alerte)
    return True


def main() -> None:
    """Point d'entrée du CronJob de supervision."""
    configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
    logger.info("watchdog_debut")
    asyncio.run(executer())
    logger.info("watchdog_fin")


if __name__ == "__main__":  # pragma: no cover
    main()
