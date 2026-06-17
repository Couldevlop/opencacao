"""Tests du store de curation (CurationStore)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.curation.store import CurationStore, DosageRefuse, ValidationInvalide


def _ecrire(chemin: Path, enregistrements: list[dict]) -> None:
    chemin.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in enregistrements) + "\n",
        encoding="utf-8",
    )


def _store(tmp_path: Path) -> CurationStore:
    return CurationStore(tmp_path, tmp_path / "corpus_cure.jsonl")


def test_a_curer_priorise_et_exclut_refus(tmp_path: Path) -> None:
    """Les 👎 remontent en tête ; les refus (redirection ANADER) sont exclus."""
    _ecrire(
        tmp_path / "interactions.jsonl",
        [
            {
                "id": "a" * 8,
                "question": "Q1",
                "reponse": "R1",
                "confiance": "elevee",
                "sources": ["CNRA"],
                "redirection_anader": False,
            },
            {
                "id": "b" * 8,
                "question": "Q2",
                "reponse": "R2",
                "confiance": "faible",
                "sources": [],
                "redirection_anader": False,
            },
            {
                "id": "c" * 8,
                "question": "Q3 dosage",
                "reponse": "refus",
                "confiance": "elevee",
                "sources": [],
                "redirection_anader": True,
            },
        ],
    )
    _ecrire(tmp_path / "feedback.jsonl", [{"id": "a" * 8, "vote": "down"}])

    items = _store(tmp_path).a_curer()
    ids = [i["id"] for i in items]
    assert ("c" * 8) not in ids  # refus exclu
    assert ids[0] == "a" * 8  # 👎 prioritaire malgré une confiance élevée
    assert set(ids) == {"a" * 8, "b" * 8}


async def test_valider_ecrit_le_corpus_et_marque(tmp_path: Path) -> None:
    """Valider écrit une paire au format corpus et retire l'item de la liste."""
    _ecrire(
        tmp_path / "interactions.jsonl",
        [
            {
                "id": "a" * 8,
                "question": "Quand récolter le cacao ?",
                "reponse": "x",
                "confiance": "moyenne",
                "sources": [],
                "redirection_anader": False,
            }
        ],
    )
    store = _store(tmp_path)
    await store.valider(
        "a" * 8,
        "Quand récolter le cacao ?",
        "Récoltez les cabosses bien mûres et colorées. Sources : CNRA.",
    )
    ligne = json.loads((tmp_path / "corpus_cure.jsonl").read_text(encoding="utf-8").strip())
    assert ligne["instruction"] == "Quand récolter le cacao ?"
    assert ligne["input"] == ""
    assert "CNRA" in ligne["output"]
    assert store.a_curer() == []  # déjà traité
    assert store.statistiques()["valides"] == 1


async def test_valider_refuse_un_dosage(tmp_path: Path) -> None:
    """Une réponse contenant un dosage n'est jamais versée au corpus."""
    store = _store(tmp_path)
    with pytest.raises(DosageRefuse):
        await store.valider(
            "a" * 8, "Comment traiter ?", "Appliquez 2 l/ha de bouillie bordelaise."
        )
    assert not (tmp_path / "corpus_cure.jsonl").exists()


async def test_valider_refuse_hors_bornes(tmp_path: Path) -> None:
    """Une réponse trop courte est rejetée (bornes du corpus)."""
    store = _store(tmp_path)
    with pytest.raises(ValidationInvalide):
        await store.valider("a" * 8, "Question valable ?", "trop court")


async def test_rejeter_retire_de_la_liste(tmp_path: Path) -> None:
    """Rejeter écarte l'interaction des éléments à curer."""
    _ecrire(
        tmp_path / "interactions.jsonl",
        [
            {
                "id": "a" * 8,
                "question": "Q",
                "reponse": "R",
                "confiance": "faible",
                "sources": [],
                "redirection_anader": False,
            }
        ],
    )
    store = _store(tmp_path)
    await store.rejeter("a" * 8)
    assert store.a_curer() == []
    assert store.statistiques()["rejetes"] == 1


def test_from_env(monkeypatch, tmp_path: Path) -> None:
    """La fabrique lit DATASET_DIR / CORPUS_CURE depuis l'environnement."""
    monkeypatch.setenv("DATASET_DIR", str(tmp_path))
    store = CurationStore.from_env()
    assert isinstance(store, CurationStore)
