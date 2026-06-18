"""Tests de la logique partagée d'indexation RAG (fusion additive)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.rag_index_builder import (
    ajouter_entrees,
    charger_paires,
    construire_entrees,
    detecter_source,
    ecrire_index,
    filtrer_nouvelles,
    lire_index,
    lire_textes_indexes,
    paires_nouvelles,
)


def _ecrire_jsonl(chemin: Path, lignes: list[dict]) -> None:
    chemin.write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in lignes) + "\n",
        encoding="utf-8",
    )


def test_detecter_source() -> None:
    assert detecter_source("Voir le CNRA pour plus d'infos") == "CNRA"
    assert detecter_source("Selon l'ANADER…") == "ANADER"
    assert detecter_source("Aucune source ici") == ""


def test_charger_paires_ignore_vide_et_invalide(tmp_path: Path) -> None:
    fichier = tmp_path / "c.jsonl"
    fichier.write_text(
        json.dumps({"instruction": "Q1", "output": "R1"})
        + "\n\n"
        + "{json cassé}\n"
        + json.dumps({"instruction": "", "output": "R2"})  # instruction vide -> ignorée
        + "\n"
        + json.dumps({"instruction": "Q3", "output": "R3"})
        + "\n",
        encoding="utf-8",
    )
    paires = charger_paires([fichier, tmp_path / "absent.jsonl"])
    assert paires == [("Q1", "R1"), ("Q3", "R3")]


def test_lire_index_absent_et_corrompu(tmp_path: Path) -> None:
    assert lire_index(tmp_path / "rien.jsonl") == []
    idx = tmp_path / "i.jsonl"
    idx.write_text(
        json.dumps({"texte": "T", "source": "CNRA", "vecteur": [0.1, 0.2]})
        + "\n"
        + "pas du json\n"
        + json.dumps({"source": "X"})  # sans texte/vecteur -> ignoré
        + "\n",
        encoding="utf-8",
    )
    entrees = lire_index(idx)
    assert len(entrees) == 1
    assert entrees[0]["texte"] == "T"


def test_paires_nouvelles_dedup_par_reponse() -> None:
    existant = [{"texte": "déjà là", "source": "", "vecteur": [0.0]}]
    paires = [("Qa", "déjà là"), ("Qb", "nouveau"), ("Qc", "nouveau")]
    nouvelles = paires_nouvelles(existant, paires)
    # "déjà là" déjà indexé ; "nouveau" gardé une seule fois.
    assert nouvelles == [("Qb", "nouveau")]


def test_construire_entrees_arrondit_et_detecte_source() -> None:
    entrees = construire_entrees([("Q", "Réponse ANADER")], [[0.123456789, 0.5]])
    assert entrees[0]["texte"] == "Réponse ANADER"
    assert entrees[0]["source"] == "ANADER"
    assert entrees[0]["vecteur"] == [0.123457, 0.5]


def test_construire_entrees_longueurs_incoherentes() -> None:
    with pytest.raises(ValueError):
        construire_entrees([("Q", "R")], [])


def test_ecrire_index_atomique(tmp_path: Path) -> None:
    cible = tmp_path / "sous" / "index.jsonl"
    ecrire_index(cible, [{"texte": "T", "source": "", "vecteur": [0.1]}])
    relu = lire_index(cible)
    assert relu[0]["texte"] == "T"
    # Aucun fichier temporaire résiduel.
    assert not list(cible.parent.glob("*.tmp"))


def test_lire_textes_indexes_en_flux(tmp_path: Path) -> None:
    idx = tmp_path / "i.jsonl"
    _ecrire_jsonl(
        idx,
        [
            {"texte": "réponse A", "source": "CNRA", "vecteur": [0.1]},
            {"texte": "réponse B", "source": "", "vecteur": [0.2]},
        ],
    )
    assert lire_textes_indexes(idx) == {"réponse A", "réponse B"}
    assert lire_textes_indexes(tmp_path / "absent.jsonl") == set()


def test_filtrer_nouvelles_contre_textes_connus() -> None:
    connus = {"déjà là"}
    paires = [("Qa", "déjà là"), ("Qb", "nouveau"), ("Qc", "nouveau")]
    assert filtrer_nouvelles(connus, paires) == [("Qb", "nouveau")]


def test_ajouter_entrees_append(tmp_path: Path) -> None:
    idx = tmp_path / "i.jsonl"
    _ecrire_jsonl(idx, [{"texte": "base", "source": "CNRA", "vecteur": [1.0]}])
    ajouter_entrees(idx, [{"texte": "ajout", "source": "", "vecteur": [2.0]}])
    textes = {e["texte"] for e in lire_index(idx)}
    assert textes == {"base", "ajout"}  # l'existant est conservé
    # Aucun écrit si rien à ajouter (pas de ligne vide).
    ajouter_entrees(idx, [])
    assert len(lire_index(idx)) == 2


def test_fusion_additive_ne_reduit_jamais(tmp_path: Path) -> None:
    """Reconstruction depuis le corpus curé : l'index ne perd jamais d'entrée."""
    index = tmp_path / "rag_index.jsonl"
    _ecrire_jsonl(index, [{"texte": "fait de base", "source": "CNRA", "vecteur": [1.0, 0.0]}])
    cure = tmp_path / "corpus_cure.jsonl"
    _ecrire_jsonl(cure, [{"instruction": "Nouvelle Q", "output": "fait curé ANADER"}])

    existant = lire_index(index)
    nouvelles = paires_nouvelles(existant, charger_paires([cure]))
    fusion = existant + construire_entrees(nouvelles, [[0.0, 1.0]])
    ecrire_index(index, fusion)

    relu = lire_index(index)
    textes = {e["texte"] for e in relu}
    assert textes == {"fait de base", "fait curé ANADER"}  # +1, jamais -1
