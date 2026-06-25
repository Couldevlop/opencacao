"""Tests de la boucle de curation du journal (curate_journal, F11).

Couvre la logique pure (faisable hors réseau) : chargement et jointure du journal,
déduplication des cas, construction/validation des paires (garde-fous : aucun dosage,
source obligatoire) et orchestration avec un maître simulé.

Conformément au garde-fou métier, aucun dosage chiffré n'apparaît : la sécurité est
vérifiée par le REJET d'une paire qui en contiendrait.
"""

from __future__ import annotations

import json
from pathlib import Path

from curate_journal import (
    construire_paire,
    curer_journal,
    dedup_cas,
    dernier_vote,
    joindre,
    charger_interactions,
    cles_existantes,
)

_REPONSE_OK = (
    "Le cacao se cultive surtout dans le sud forestier (Gagnoa, Daloa, Soubré). Dans "
    "quelle ville êtes-vous ? Je peux vous orienter vers l'agence ANADER la plus proche. "
    "Sources : ANADER, Conseil du Café-Cacao."
)


def _ecrire(chemin: Path, enregs: list[dict]) -> None:
    chemin.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in enregs) + "\n",
        encoding="utf-8",
    )


# --- Chargement & jointure du journal ---


def test_dernier_vote_garde_le_plus_recent(tmp_path: Path) -> None:
    """Le dernier vote d'une interaction l'emporte (changement d'avis)."""
    fb = tmp_path / "feedback.jsonl"
    _ecrire(
        fb,
        [
            {"id": "a", "vote": "down"},
            {"id": "b", "vote": "up"},
            {"id": "a", "vote": "up"},  # a change d'avis
            {"id": "c", "vote": "bizarre"},  # vote invalide ignoré
        ],
    )
    assert dernier_vote(fb) == {"a": "up", "b": "up"}


def test_joindre_ignore_votes_orphelins(tmp_path: Path) -> None:
    """Un vote sans interaction correspondante est ignoré."""
    interactions = {
        "a": {
            "id": "a",
            "question": "Quand récolter ?",
            "reponse": "Bientôt.",
            "sources": [],
        }
    }
    cas = joindre(interactions, {"a": "down", "z": "up"})
    assert len(cas) == 1 and cas[0]["id"] == "a" and cas[0]["vote"] == "down"


def test_charger_interactions(tmp_path: Path) -> None:
    """Les interactions sont indexées par identifiant ; lignes invalides ignorées."""
    f = tmp_path / "interactions.jsonl"
    f.write_text('{"id": "x", "question": "q"}\n{cassé\n', encoding="utf-8")
    inter = charger_interactions(f)
    assert list(inter) == ["x"]


def test_dedup_cas_negatif_prime(tmp_path: Path) -> None:
    """Une même question vue en 👍 et 👎 n'est gardée qu'une fois, en 👎 (informatif)."""
    cas = [
        {
            "id": "1",
            "vote": "up",
            "question": "Zones de culture du cacao ?",
            "reponse": "...",
        },
        {
            "id": "2",
            "vote": "down",
            "question": "zones de culture du cacao ?",
            "reponse": "...",
        },
    ]
    dedup = dedup_cas(cas)
    assert len(dedup) == 1 and dedup[0]["vote"] == "down"


# --- Construction & validation des paires ---


def test_construire_paire_valide() -> None:
    """Une réécriture conforme (source citée, pas de dosage) produit une paire."""
    verdict = {
        "action": "corriger",
        "instruction": "Quelles sont les zones pour le cacao ?",
        "output": _REPONSE_OK,
    }
    paire, motif = construire_paire(verdict)
    assert paire is not None and motif == ""
    assert paire["instruction"] and paire["output"] and paire["input"] == ""


def test_construire_paire_rejet_du_maitre() -> None:
    """Un cas que le maître juge inexploitable n'est pas transformé en paire."""
    paire, motif = construire_paire(
        {"action": "rejeter", "instruction": "", "output": ""}
    )
    assert paire is None and motif == "rejeté par le maître"


def test_construire_paire_sans_source_ecartee() -> None:
    """Une réponse sans source reconnue est écartée (règle de validation)."""
    verdict = {
        "action": "corriger",
        "instruction": "Question valable sur le cacao ?",
        "output": "Une réponse correcte mais qui ne cite aucune source officielle reconnue du tout.",
    }
    paire, motif = construire_paire(verdict)
    assert paire is None and "source" in motif.lower()


def test_construire_paire_maitre_indisponible() -> None:
    """Un verdict absent (maître injoignable) est signalé proprement."""
    paire, motif = construire_paire(None)
    assert paire is None and motif == "maître indisponible"


# --- Orchestration ---


class _FauxCurateur:
    """Maître simulé : renvoie des verdicts pré-câblés par identifiant de cas."""

    def __init__(self, verdicts: dict[str, dict | None]) -> None:
        self._verdicts = verdicts

    def curer(self, cas: dict) -> dict | None:
        return self._verdicts.get(cas["id"])


def test_curer_journal_compte_et_deduplique() -> None:
    """La passe produit les paires valides, déduplique et compte les rejets."""
    cas = [
        {"id": "1", "vote": "down", "question": "Zones du cacao ?", "reponse": "x"},
        {"id": "2", "vote": "down", "question": "Récolte ?", "reponse": "y"},
        {"id": "3", "vote": "up", "question": "Séchage ?", "reponse": "z"},
    ]
    verdicts = {
        "1": {
            "action": "corriger",
            "instruction": "Quelles zones pour le cacao ?",
            "output": _REPONSE_OK,
        },
        "2": {"action": "rejeter", "instruction": "", "output": ""},
        "3": None,  # maître indisponible
    }
    paires, stats = curer_journal(cas, _FauxCurateur(verdicts), set())
    assert stats.cas == 3
    assert stats.pairs == 1 and len(paires) == 1
    assert stats.rejetes == 1
    assert stats.maitre_indisponible == 1


def test_curer_journal_respecte_les_cles_existantes() -> None:
    """Une instruction déjà présente dans le corpus curé n'est pas réajoutée."""
    from assemble_corpus import _cle

    cas = [{"id": "1", "vote": "down", "question": "Zones ?", "reponse": "x"}]
    verdict = {
        "action": "corriger",
        "instruction": "Quelles zones pour le cacao ?",
        "output": _REPONSE_OK,
    }
    deja = {_cle("Quelles zones pour le cacao ?")}
    paires, stats = curer_journal(cas, _FauxCurateur({"1": verdict}), deja)
    assert paires == [] and stats.doublons == 1


def test_cles_existantes(tmp_path: Path) -> None:
    """Les clés du corpus curé existant sont chargées pour la déduplication."""
    f = tmp_path / "cure.jsonl"
    _ecrire(f, [{"instruction": "Quand tailler ?", "input": "", "output": "..."}])
    from assemble_corpus import _cle

    assert cles_existantes(f) == {_cle("Quand tailler ?")}
