"""Tests de l'analyse du watchdog des CronJobs (pure, sans cluster ni réseau)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.curation import watchdog

_MAINTENANT = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)


def _cronjob(*, suspend: bool = False, derniere_reussite: datetime | None = None) -> dict:
    status: dict = {}
    if derniere_reussite is not None:
        status["lastSuccessfulTime"] = derniere_reussite.strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "metadata": {"name": "enrichissement-rag"},
        "spec": {"suspend": suspend},
        "status": status,
    }


def test_sain_recent_pas_d_alerte() -> None:
    """Une réussite récente (< seuil) ne déclenche aucune alerte."""
    cj = _cronjob(derniere_reussite=_MAINTENANT - timedelta(hours=9))
    assert watchdog.analyser(cj, _MAINTENANT, 26.0) is None


def test_suspendu_alerte() -> None:
    cj = _cronjob(suspend=True, derniere_reussite=_MAINTENANT - timedelta(hours=1))
    message = watchdog.analyser(cj, _MAINTENANT, 26.0)
    assert message is not None
    assert "SUSPENDU" in message


def test_aucune_reussite_alerte() -> None:
    message = watchdog.analyser(_cronjob(), _MAINTENANT, 26.0)
    assert message is not None
    assert "aucune exécution réussie" in message


def test_trop_ancien_alerte() -> None:
    """Une dernière réussite au-delà du seuil signale un cron arrêté/en échec."""
    cj = _cronjob(derniere_reussite=_MAINTENANT - timedelta(hours=30))
    message = watchdog.analyser(cj, _MAINTENANT, 26.0)
    assert message is not None
    assert "pas réussi depuis" in message


def test_horodatage_illisible_pas_de_fausse_alerte() -> None:
    cj = {
        "metadata": {"name": "enrichissement-rag"},
        "spec": {},
        "status": {"lastSuccessfulTime": "pas-une-date"},
    }
    assert watchdog.analyser(cj, _MAINTENANT, 26.0) is None


def test_parse_iso() -> None:
    assert watchdog._parse_iso("2026-06-20T03:00:17Z") == datetime(
        2026, 6, 20, 3, 0, 17, tzinfo=UTC
    )
    assert watchdog._parse_iso("invalide") is None
