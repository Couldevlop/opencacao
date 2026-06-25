"""Tests du générateur de corpus de refus (build_refusals, F3).

Vérifie que TOUTE paire générée est conforme (champs, longueurs, source citée et
surtout AUCUN dosage chiffré — CLAUDE §13), que la génération est déterministe et
couvre toutes les catégories de refus.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import build_refusals as b  # noqa: E402
from enrich_corpus import _valider_paire  # noqa: E402
from evaluate import contient_dosage  # noqa: E402


def test_toutes_les_paires_sont_valides() -> None:
    """Chaque paire passe la validation du corpus (donc : aucune sans source/dosage)."""
    for paire in b.generer():
        problemes = _valider_paire(0, paire)
        assert not problemes, f"{paire['instruction']!r} -> {problemes}"


def test_aucun_dosage_dans_les_reponses() -> None:
    """Garde-fou explicite : aucune réponse de refus ne contient de dosage chiffré."""
    assert all(not contient_dosage(p["output"]) for p in b.generer())


def test_reponses_orientent_vers_anader() -> None:
    """Tout refus oriente vers l'ANADER (source + redirection métier)."""
    assert all("anader" in p["output"].lower() for p in b.generer())


def test_volume_et_couverture() -> None:
    """Le générateur produit un volume conséquent et couvre toutes les catégories."""
    assert len(b.generer()) >= 250
    for nom, fabrique in b.GENERATEURS.items():
        assert fabrique(), f"catégorie vide : {nom}"


def test_generation_deterministe() -> None:
    """Deux exécutions produisent exactement le même corpus (reproductibilité)."""
    assert b.generer() == b.generer()


def test_filtrer_deduplique_contre_existant() -> None:
    """Une instruction déjà présente n'est pas réajoutée."""
    from assemble_corpus import _cle

    paires = b.generer()
    deja = {_cle(paires[0]["instruction"])}
    gardees, stats = b.filtrer(paires, deja)
    cles = {_cle(p["instruction"]) for p in gardees}
    assert _cle(paires[0]["instruction"]) not in cles
    assert stats["doublons"] >= 1
