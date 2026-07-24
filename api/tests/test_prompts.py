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


def test_system_prompt_conserve_les_regles_critiques() -> None:
    # Non-régression : la concision ne doit effacer AUCUN garde-fou métier.
    assert "UNIQUEMENT le cacao" in SYSTEM_PROMPT
    assert "dosages précis" in SYSTEM_PROMPT
    assert "jamais toi-même un numéro" in SYSTEM_PROMPT


def test_system_prompt_condense() -> None:
    # Trim pour réduire le préremplissage : le prompt STRICT (repli drapeau off) reste
    # nettement plus court qu'avant (2129 car.), toutes les règles préservées (cf.
    # test_system_prompt_conserve_les_regles_critiques). Le prompt réchauffé (défaut),
    # lui, privilégie le ton chaleureux sur la concision brute — pas de contrainte de
    # longueur sur SYSTEM_PROMPT.
    from app.services.prompts import SYSTEM_PROMPT_STRICT

    assert len(SYSTEM_PROMPT_STRICT) < 1300
    assert "invente" in SYSTEM_PROMPT_STRICT


def test_system_prompt_rechauffe_garde_les_garde_fous() -> None:
    from app.services.prompts import SYSTEM_PROMPT

    assert "UNIQUEMENT le cacao" in SYSTEM_PROMPT
    assert "dosages précis" in SYSTEM_PROMPT
    assert "N'invente JAMAIS" in SYSTEM_PROMPT
    assert "ANADER" in SYSTEM_PROMPT
    # Ton réchauffé : plus de consigne sèche « sans rappel ni reformulation ».
    assert "sans rappel ni reformulation" not in SYSTEM_PROMPT


def test_system_prompt_strict_conserve_a_l_identique() -> None:
    from app.services.prompts import SYSTEM_PROMPT_STRICT

    assert "10 phrases maximum" in SYSTEM_PROMPT_STRICT
    # Texte exact conservé mot pour mot (le "général" fait partie du texte d'origine).
    assert "sans rappel général ni reformulation" in SYSTEM_PROMPT_STRICT


def test_build_messages_avec_consigne_de_clarification() -> None:
    from app.services.prompts import build_messages

    msgs = build_messages("Mes feuilles jaunissent", consigne="Pose une question brève.")
    assert msgs[0]["role"] == "system"
    assert "Pose une question brève." in msgs[-1]["content"]
    assert "Mes feuilles jaunissent" in msgs[-1]["content"]
    # La consigne remplace le contexte RAG : pas de bloc « base de connaissances ».
    assert "base de connaissances" not in msgs[-1]["content"]


def test_build_messages_choisit_le_prompt_systeme() -> None:
    from app.services.prompts import SYSTEM_PROMPT_STRICT, build_messages

    msgs = build_messages("q", system_prompt=SYSTEM_PROMPT_STRICT)
    assert msgs[0]["content"] == SYSTEM_PROMPT_STRICT
