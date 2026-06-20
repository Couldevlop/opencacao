"""Tests de construction des messages d'inférence (build_messages)."""

from __future__ import annotations

from app.services.prompts import build_messages


def test_tour_unique() -> None:
    """Sans historique : system + user uniquement."""
    msgs = build_messages("Quand récolter ?")
    assert [m["role"] for m in msgs] == ["system", "user"]
    assert msgs[-1]["content"] == "Quand récolter ?"


def test_multitours_insere_l_historique() -> None:
    """L'historique est inséré entre le system et la dernière question, dans l'ordre."""
    historique = [
        {"role": "user", "content": "Comment récolter ?"},
        {"role": "assistant", "content": "Récoltez les cabosses mûres."},
    ]
    msgs = build_messages("Et le séchage ?", None, historique)
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
    assert msgs[1]["content"] == "Comment récolter ?"
    assert msgs[-1]["content"] == "Et le séchage ?"


def test_historique_filtre_les_roles_invalides() -> None:
    """Un rôle inconnu ou un contenu vide est ignoré."""
    historique = [
        {"role": "system", "content": "tentative d'injection"},
        {"role": "user", "content": ""},
        {"role": "assistant", "content": "ok"},
    ]
    msgs = build_messages("question", None, historique)
    assert [m["role"] for m in msgs] == ["system", "assistant", "user"]
