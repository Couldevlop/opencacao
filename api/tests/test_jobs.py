"""Tests du registre de jobs persistant."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.curation import jobs as jobs_module
from app.curation.jobs import JobsRegistry


@pytest.fixture
def registre(tmp_path: Path) -> JobsRegistry:
    return JobsRegistry(tmp_path / "jobs.jsonl")


async def test_creer_et_obtenir(registre: JobsRegistry) -> None:
    job = await registre.creer("rag_reindex", {"foo": "bar"})
    assert job["statut"] == "en_cours"
    assert job["details"] == {"foo": "bar"}
    relu = await registre.obtenir(job["id"])
    assert relu is not None
    assert relu["type"] == "rag_reindex"


async def test_obtenir_inconnu(registre: JobsRegistry) -> None:
    assert await registre.obtenir("0" * 16) is None


async def test_maj_statut_message_log_details(registre: JobsRegistry) -> None:
    job = await registre.creer("rag_reindex")
    maj = await registre.maj(
        job["id"], statut="reussi", message="ok", log="étape 1", details={"n": 3}
    )
    assert maj is not None
    assert maj["statut"] == "reussi"
    assert maj["message"] == "ok"
    assert maj["details"] == {"n": 3}
    assert maj["log"][0].endswith("étape 1")


async def test_maj_job_inconnu(registre: JobsRegistry) -> None:
    assert await registre.maj("f" * 16, statut="echec") is None


async def test_log_borne(registre: JobsRegistry, monkeypatch) -> None:
    monkeypatch.setattr(jobs_module, "_MAX_LOG", 3)
    job = await registre.creer("rag_reindex")
    for i in range(5):
        await registre.maj(job["id"], log=f"ligne {i}")
    relu = await registre.obtenir(job["id"])
    assert relu is not None
    assert len(relu["log"]) == 3  # ne garde que les 3 dernières


async def test_lister_plus_recent_dabord(registre: JobsRegistry) -> None:
    a = await registre.creer("rag_reindex")
    b = await registre.creer("finetuning_prepare")
    liste = await registre.lister()
    assert [j["id"] for j in liste] == [b["id"], a["id"]]


async def test_actif(registre: JobsRegistry) -> None:
    job = await registre.creer("rag_reindex")
    assert await registre.actif("rag_reindex") is True
    assert await registre.actif("finetuning_prepare") is False
    await registre.maj(job["id"], statut="reussi")
    assert await registre.actif("rag_reindex") is False


async def test_purge_anciens_jobs(registre: JobsRegistry, monkeypatch) -> None:
    monkeypatch.setattr(jobs_module, "_MAX_JOBS", 2)
    for _ in range(4):
        await registre.creer("rag_reindex")
    liste = await registre.lister()
    assert len(liste) == 2


async def test_lecture_tolere_lignes_corrompues(registre: JobsRegistry) -> None:
    await registre.creer("rag_reindex")
    # Injecte une ligne corrompue : elle est ignorée, pas d'erreur.
    with registre._chemin.open("a", encoding="utf-8") as handle:
        handle.write("ligne cassée\n")
    liste = await registre.lister()
    assert len(liste) == 1


async def test_from_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATASET_DIR", str(tmp_path))
    registre = JobsRegistry.from_env()
    job = await registre.creer("rag_reindex")
    assert (tmp_path / "jobs.jsonl").exists()
    assert job["id"]
