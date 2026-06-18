"""Tests de l'API de la console de curation (TestClient + auth)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.curation.main as cur
from app.curation.jobs import JobsRegistry
from app.curation.store import CurationStore


def _seed(tmp_path: Path) -> None:
    (tmp_path / "interactions.jsonl").write_text(
        json.dumps(
            {
                "id": "a" * 8,
                "question": "Quand récolter le cacao ?",
                "reponse": "Quand les cabosses sont mûres.",
                "confiance": "faible",
                "sources": [],
                "redirection_anader": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )


@pytest.fixture
def console(tmp_path: Path, monkeypatch) -> tuple[TestClient, Path]:
    _seed(tmp_path)
    monkeypatch.setattr(cur, "_store", CurationStore(tmp_path, tmp_path / "corpus_cure.jsonl"))
    monkeypatch.setattr(cur, "_MOT_DE_PASSE", "")
    return TestClient(cur.app), tmp_path


def test_sante(console) -> None:
    client, _ = console
    assert client.get("/api/sante").json() == {"status": "ok"}


def test_stats_et_liste(console) -> None:
    client, _ = console
    assert client.get("/api/stats").json()["total"] == 1
    items = client.get("/api/a-curer").json()
    assert items[0]["id"] == "a" * 8


def test_valider_ecrit_le_corpus(console) -> None:
    client, tmp = console
    resp = client.post(
        "/api/valider",
        json={
            "interaction_id": "a" * 8,
            "instruction": "Quand récolter le cacao ?",
            "output": "Récoltez les cabosses bien mûres et colorées. Sources : CNRA.",
        },
    )
    assert resp.status_code == 202
    assert (tmp / "corpus_cure.jsonl").exists()


def test_valider_dosage_rejete_422(console) -> None:
    client, _ = console
    resp = client.post(
        "/api/valider",
        json={
            "interaction_id": "a" * 8,
            "instruction": "Comment traiter ?",
            "output": "Appliquez 2 l/ha de bouillie bordelaise sur les cabosses.",
        },
    )
    assert resp.status_code == 422


def test_rejeter(console) -> None:
    client, _ = console
    assert client.post("/api/rejeter", json={"interaction_id": "a" * 8}).status_code == 202


def test_erreur_interne_renvoie_500(monkeypatch) -> None:
    class Casse:
        def a_curer(self) -> list[dict]:
            raise RuntimeError("boom")

    monkeypatch.setattr(cur, "_store", Casse())
    monkeypatch.setattr(cur, "_MOT_DE_PASSE", "")
    client = TestClient(cur.app, raise_server_exceptions=False)
    assert client.get("/api/a-curer").status_code == 500


# --- Authentification par session (cookie signé) ---


def test_token_signe_round_trip() -> None:
    token = cur._creer_token()
    assert cur._token_valide(token) is True
    assert cur._token_valide(None) is False
    assert cur._token_valide("12345.signaturebidon") is False
    assert cur._token_valide(token + "x") is False  # signature altérée


def test_session_endpoint_sans_mot_de_passe(console) -> None:
    client, _ = console  # fixture: _MOT_DE_PASSE = ""
    etat = client.get("/api/session").json()
    assert etat == {"auth_requise": False, "authentifie": True}


def _client_protege(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(cur, "_store", CurationStore(tmp_path, tmp_path / "c.jsonl"))
    monkeypatch.setattr(cur, "_MOT_DE_PASSE", "secret")
    monkeypatch.setattr(cur, "_UTILISATEUR", "curateur")
    return TestClient(cur.app)


def test_acces_protege_sans_session_401(tmp_path, monkeypatch) -> None:
    client = _client_protege(tmp_path, monkeypatch)
    assert client.get("/api/stats").status_code == 401
    assert client.get("/api/session").json()["authentifie"] is False


def test_login_mauvais_identifiants_401(tmp_path, monkeypatch) -> None:
    client = _client_protege(tmp_path, monkeypatch)
    resp = client.post("/api/login", json={"utilisateur": "curateur", "mot_de_passe": "faux"})
    assert resp.status_code == 401


def test_login_pose_un_cookie_et_donne_acces(tmp_path, monkeypatch) -> None:
    client = _client_protege(tmp_path, monkeypatch)
    resp = client.post("/api/login", json={"utilisateur": "curateur", "mot_de_passe": "secret"})
    assert resp.status_code == 200
    assert "curation_session" in resp.headers.get("set-cookie", "")
    # Accès autorisé avec un cookie de session valide.
    cookies = {"curation_session": cur._creer_token()}
    assert client.get("/api/stats", cookies=cookies).status_code == 200


# --- Sécurité OWASP : en-têtes ---


def test_entetes_securite_presents(console) -> None:
    client, _ = console
    resp = client.get("/api/sante")
    csp = resp.headers.get("content-security-policy", "")
    assert "script-src 'self'" in csp  # autorise le JS de la console
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("x-frame-options") == "DENY"


# --- Pipeline : reindex RAG, préparation fine-tuning, suivi des jobs ---


class FakePipeline:
    """Pipeline simulé : crée/maj des jobs sans embeddings ni cluster réels."""

    def __init__(self, jobs: JobsRegistry) -> None:
        self._jobs = jobs
        self.reindex_conflit = False
        self.prepare_conflit = False

    async def demarrer_reindex(self) -> dict | None:
        if self.reindex_conflit:
            return None
        return await self._jobs.creer("rag_reindex")

    async def reindexer_rag(self, job_id: str) -> None:
        await self._jobs.maj(job_id, statut="reussi", message="ok")

    async def preparer_finetuning(self) -> dict | None:
        if self.prepare_conflit:
            return None
        job = await self._jobs.creer("finetuning_prepare")
        return await self._jobs.maj(
            job["id"], statut="reussi", message="prêt", details={"procedure": "pod_train.sh"}
        )


@pytest.fixture
def pipeline_console(tmp_path: Path, monkeypatch) -> tuple[TestClient, FakePipeline]:
    registre = JobsRegistry(tmp_path / "jobs.jsonl")
    faux = FakePipeline(registre)
    monkeypatch.setattr(cur, "_jobs", registre)
    monkeypatch.setattr(cur, "_pipeline", faux)
    monkeypatch.setattr(cur, "_MOT_DE_PASSE", "")
    return TestClient(cur.app), faux


def test_rag_reindex_202(pipeline_console) -> None:
    client, _ = pipeline_console
    resp = client.post("/api/rag/reindex")
    assert resp.status_code == 202
    assert resp.json()["job_id"]
    # Le job a bien été enregistré.
    jobs = client.get("/api/jobs").json()
    assert any(j["type"] == "rag_reindex" for j in jobs)


def test_rag_reindex_conflit_409(pipeline_console) -> None:
    client, faux = pipeline_console
    faux.reindex_conflit = True
    assert client.post("/api/rag/reindex").status_code == 409


def test_finetuning_prepare_202(pipeline_console) -> None:
    client, _ = pipeline_console
    resp = client.post("/api/finetuning/prepare")
    assert resp.status_code == 202
    assert "pod_train.sh" in resp.json()["details"]["procedure"]


def test_finetuning_prepare_conflit_409(pipeline_console) -> None:
    client, faux = pipeline_console
    faux.prepare_conflit = True
    assert client.post("/api/finetuning/prepare").status_code == 409


def test_jobs_liste_et_detail(pipeline_console) -> None:
    client, _ = pipeline_console
    job_id = client.post("/api/rag/reindex").json()["job_id"]
    detail = client.get(f"/api/jobs/{job_id}")
    assert detail.status_code == 200
    assert detail.json()["id"] == job_id


def test_job_detail_inconnu_404(pipeline_console) -> None:
    client, _ = pipeline_console
    assert client.get("/api/jobs/" + "0" * 16).status_code == 404


def test_job_detail_id_invalide_422(pipeline_console) -> None:
    client, _ = pipeline_console
    # Format d'id invalide -> rejeté par la validation de chemin.
    assert client.get("/api/jobs/pas-un-id").status_code == 422


def test_pipeline_protege_sans_session_401(tmp_path, monkeypatch) -> None:
    registre = JobsRegistry(tmp_path / "jobs.jsonl")
    monkeypatch.setattr(cur, "_jobs", registre)
    monkeypatch.setattr(cur, "_pipeline", FakePipeline(registre))
    monkeypatch.setattr(cur, "_MOT_DE_PASSE", "secret")
    monkeypatch.setattr(cur, "_UTILISATEUR", "curateur")
    client = TestClient(cur.app)
    assert client.post("/api/rag/reindex").status_code == 401
    assert client.post("/api/finetuning/prepare").status_code == 401
    assert client.get("/api/jobs").status_code == 401
