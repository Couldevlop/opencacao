"""Tests de l'API de la console de curation (TestClient + auth)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPBasicCredentials
from fastapi.testclient import TestClient

import app.curation.main as cur
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


# --- Authentification HTTP Basic optionnelle ---


def test_auth_desactivee_par_defaut(monkeypatch) -> None:
    monkeypatch.setattr(cur, "_MOT_DE_PASSE", "")
    assert cur._verifier_acces(None) is None


def test_auth_refuse_sans_identifiants(monkeypatch) -> None:
    monkeypatch.setattr(cur, "_MOT_DE_PASSE", "secret")
    with pytest.raises(HTTPException):
        cur._verifier_acces(None)


def test_auth_accepte_les_bons_identifiants(monkeypatch) -> None:
    monkeypatch.setattr(cur, "_MOT_DE_PASSE", "secret")
    monkeypatch.setattr(cur, "_UTILISATEUR", "curateur")
    creds = HTTPBasicCredentials(username="curateur", password="secret")
    assert cur._verifier_acces(creds) is None
