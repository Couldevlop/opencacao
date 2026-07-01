"""Tests de construction des messages d'inférence (build_messages)."""

from __future__ import annotations

from app.services.prompts import SYSTEM_PROMPT, build_messages


def test_tour_unique() -> None:
    """Sans historique : system + user uniquement."""
    msgs = build_messages("Quand récolter ?")
    assert [m["role"] for m in msgs] == ["system", "user"]
    assert "Quand récolter ?" in msgs[-1]["content"]


def test_multitours_insere_l_historique() -> None:
    """L'historique est inséré entre le system et la dernière question, dans l'ordre."""
    historique = [
        {"role": "user", "content": "Comment récolter ?"},
        {"role": "assistant", "content": "Récoltez les cabosses mûres."},
    ]
    msgs = build_messages("Et le séchage ?", None, historique)
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
    assert msgs[1]["content"] == "Comment récolter ?"
    assert "Et le séchage ?" in msgs[-1]["content"]


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
    assert "question" in msgs[-1]["content"]


def test_sans_contexte_injecte_consigne_anti_fabrication() -> None:
    # Souveraineté : sans extrait RAG, le message doit porter une consigne explicite
    # « n'invente rien, oriente ANADER » (généralise le correctif de l'agent Prix).
    msgs = build_messages("Quelle densité de plantation ?")
    contenu = msgs[-1]["content"].lower()
    assert "anader" in contenu
    assert "invente" in contenu or "vérifier" in contenu
    # La question reste présente.
    assert "densité de plantation" in msgs[-1]["content"]


def test_system_prompt_sans_clause_certain() -> None:
    # Faille de grounding retirée : le modèle ne cite une source que si elle est dans
    # le contexte fourni — jamais « parce qu'il en est certain » (source de mémoire).
    assert "ou si tu en es certain" not in SYSTEM_PROMPT


def test_system_prompt_consigne_brievete_ferme() -> None:
    # Le levier de latence : une consigne ferme de brièveté (pas la molle « reste concis »).
    assert "10 phrases maximum" in SYSTEM_PROMPT


def test_system_prompt_conserve_les_regles_critiques() -> None:
    # Non-régression : la concision ne doit effacer AUCUN garde-fou métier.
    assert "UNIQUEMENT le cacao" in SYSTEM_PROMPT
    assert "dosages précis" in SYSTEM_PROMPT
    assert "jamais toi-même un numéro" in SYSTEM_PROMPT


def test_system_prompt_condense() -> None:
    # Trim pour réduire le préremplissage : nettement plus court qu'avant (2129 car.),
    # mais toutes les règles préservées (cf. test_system_prompt_conserve_les_regles_critiques).
    assert len(SYSTEM_PROMPT) < 1300
    assert "invente" in SYSTEM_PROMPT
