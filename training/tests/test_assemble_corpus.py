"""Tests de l'assembleur de corpus (assemble_corpus)."""

from __future__ import annotations

import json
from pathlib import Path

from assemble_corpus import _cle, assembler

# Paires valides (longueurs ok, source citée, pas de dosage).
_VALIDE_1 = {
    "instruction": "Quand récolter les cabosses de cacao ?",
    "input": "",
    "output": "Récoltez les cabosses bien mûres, fermes et colorées. Une récolte à point garantit de meilleures fèves. Sources : CNRA.",
}
_VALIDE_2 = {
    "instruction": "Comment sécher les fèves de cacao ?",
    "input": "",
    "output": "Étalez les fèves en couche fine au soleil et brassez régulièrement pendant plusieurs jours. Sources : ANADER.",
}


def _ecrire(chemin: Path, paires: list[dict]) -> None:
    chemin.write_text(
        "\n".join(json.dumps(p, ensure_ascii=False) for p in paires) + "\n",
        encoding="utf-8",
    )


def test_cle_normalise() -> None:
    """La clé de dédup ignore casse, accents et espaces."""
    assert _cle("Quand RÉCOLTER ?") == _cle("quand  recolter ?")


def test_assemble_valide_et_deduplique(tmp_path: Path) -> None:
    """Combine, garde les valides, déduplique sans casse/accents."""
    _ecrire(tmp_path / "a.jsonl", [_VALIDE_1, _VALIDE_2])
    # Doublon de _VALIDE_1 (casse/accents différents) dans une 2e source.
    doublon = {**_VALIDE_1, "instruction": "quand recolter les cabosses de cacao ?"}
    _ecrire(tmp_path / "b.jsonl", [doublon])
    sortie = tmp_path / "out.jsonl"

    stats = assembler([tmp_path / "a.jsonl", tmp_path / "b.jsonl"], sortie)

    assert stats["gardees"] == 2
    assert stats["doublons"] == 1
    lignes = sortie.read_text(encoding="utf-8").splitlines()
    assert len(lignes) == 2


def test_assemble_ecarte_invalides_et_dosages(tmp_path: Path) -> None:
    """Les paires sans source, hors bornes ou avec dosage sont écartées."""
    sans_source = {
        "instruction": "Comment tailler ?",
        "input": "",
        "output": "Coupez les gourmands régulièrement pour aérer l'arbre et favoriser la fructification au fil des saisons.",
    }
    dosage = {
        "instruction": "Comment traiter la pourriture ?",
        "input": "",
        "output": "Appliquez 2 g/L de fongicide sur les cabosses atteintes. Sources : CNRA.",
    }
    _ecrire(tmp_path / "a.jsonl", [_VALIDE_1, sans_source, dosage])
    sortie = tmp_path / "out.jsonl"

    stats = assembler([tmp_path / "a.jsonl"], sortie)

    assert stats["gardees"] == 1  # seul _VALIDE_1 passe
    assert stats["invalides"] == 2


def test_assemble_source_absente_ignoree(tmp_path: Path) -> None:
    """Un fichier source manquant est ignoré sans erreur."""
    _ecrire(tmp_path / "a.jsonl", [_VALIDE_1])
    sortie = tmp_path / "out.jsonl"
    stats = assembler([tmp_path / "a.jsonl", tmp_path / "absent.jsonl"], sortie)
    assert stats["gardees"] == 1
