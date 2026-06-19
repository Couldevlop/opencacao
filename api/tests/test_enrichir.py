"""Test de l'enrichissement programmé (CronJob) : enchaîne recherche + constitution."""

from __future__ import annotations

from app.curation import enrichir


async def test_executer_enchaine_recherche_puis_constitution(monkeypatch) -> None:
    appels: list = []

    class FauxJobs:
        def reconcilier_orphelins(self) -> int:
            appels.append("reconcil")
            return 0

        async def creer(self, type_: str) -> dict:
            appels.append(("creer", type_))
            return {"id": type_}

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
    assert ("constituer", "rag_constitution") in appels
    # L'ordre est respecté : recherche AVANT constitution.
    assert appels.index(("collecter", "recherche_sources")) < appels.index(
        ("constituer", "rag_constitution")
    )
