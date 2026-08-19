"""Test de l'enrichissement programmé (CronJob) : recherche + découverte, SANS indexer.

Le cron enchainait aussi la constitution de l'index. Retiré le 19/08/2026 après
qu'un document officiel hors sujet (politique anti-blanchiment du FIRCA) s'est
invité dans l'index et a capté la question « quel est le rôle du FIRCA ? ». Le
détail du nouveau contrat, et pourquoi il est ainsi, vit dans
``test_enrichir_revue.py``.
"""

from __future__ import annotations

from app.curation import enrichir


async def test_executer_enchaine_recherche_puis_constitution(monkeypatch) -> None:
    appels: list = []

    class FauxJobs:
        def reconcilier_orphelins(self) -> int:
            appels.append("reconcil")
            return 0

        async def creer(self, type_: str, details: dict | None = None) -> dict:
            appels.append(("creer", type_))
            return {"id": type_}

        async def obtenir(self, job_id: str) -> dict:
            return {"id": job_id, "details": {"telecharges": 0}}

    class FauxPipeline:
        async def collecter_sources(self, job_id: str) -> None:
            appels.append(("collecter", job_id))

        async def decouvrir_sources(self, job_id: str) -> None:
            appels.append(("decouvrir", job_id))

        async def constituer_rag(self, job_id: str) -> None:
            appels.append(("constituer", job_id))

    monkeypatch.setattr(enrichir.JobsRegistry, "from_env", classmethod(lambda cls: FauxJobs()))
    monkeypatch.setattr(
        enrichir.PipelineService, "from_env", classmethod(lambda cls, jobs: FauxPipeline())
    )

    await enrichir.executer()

    assert "reconcil" in appels
    assert ("collecter", "recherche_sources") in appels
    assert ("decouvrir", "decouverte_sources") in appels
    # La constitution n'est PLUS automatique : elle attend une revue humaine
    # (incident du 19/08/2026, cf. test_enrichir_revue.py).
    assert ("constituer", "rag_constitution") not in appels
    # L'ordre reste respecté : on cherche avant de découvrir.
    assert appels.index(("collecter", "recherche_sources")) < appels.index(
        ("decouvrir", "decouverte_sources")
    )
