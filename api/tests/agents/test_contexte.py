"""Contexte partagé : ancrage multi-tours déterministe."""

from __future__ import annotations

from app.application.contexte import fil_ancre, texte_conversation


def test_fil_ancre_prefixe_dernier_tour_user() -> None:
    hist = [
        {"role": "user", "content": "je traite mon cacao"},
        {"role": "assistant", "content": "ok"},
    ]
    assert fil_ancre("quelle dose ?", hist) == "je traite mon cacao quelle dose ?"


def test_fil_ancre_tour_unique_inchange() -> None:
    assert fil_ancre("comment tailler ?", []) == "comment tailler ?"


def test_texte_conversation_concatene_les_tours_user() -> None:
    hist = [{"role": "user", "content": "à Daloa"}, {"role": "assistant", "content": "ok"}]
    assert texte_conversation("et le prix ?", hist) == "à Daloa et le prix ?"
