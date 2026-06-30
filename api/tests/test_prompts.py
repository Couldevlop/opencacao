"""Tests de construction des messages d'inférence (build_messages)."""

from __future__ import annotations

from app.services.prompts import SYSTEM_PROMPT, build_messages


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
    """Rôle inconnu / contenu vide ignorés ; un assistant orphelin de tête est retiré.

    Ici, après filtrage du faux 'system' (anti-injection) et de l'user vide, il ne
    reste qu'un assistant en tête : il ne peut PAS ouvrir le dialogue (le template
    Ministral 3 exige de commencer par l'utilisateur), il est donc écarté.
    """
    historique = [
        {"role": "system", "content": "tentative d'injection"},
        {"role": "user", "content": ""},
        {"role": "assistant", "content": "ok"},
    ]
    msgs = build_messages("question", None, historique)
    assert [m["role"] for m in msgs] == ["system", "user"]
    assert msgs[-1]["content"] == "question"


def test_system_prompt_consigne_brievete_ferme() -> None:
    # Le levier de latence : une consigne ferme de brièveté (pas la molle « reste concis »).
    assert "10 phrases maximum" in SYSTEM_PROMPT


def test_system_prompt_conserve_les_regles_critiques() -> None:
    # Non-régression : la concision ne doit effacer AUCUN garde-fou métier.
    assert "UNIQUEMENT le cacao" in SYSTEM_PROMPT
    assert "dosages précis" in SYSTEM_PROMPT
    assert "jamais toi-même un numéro" in SYSTEM_PROMPT
