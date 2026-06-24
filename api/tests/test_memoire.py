"""Tests de la mémoire conversationnelle : fenêtre glissante + résumé (B2, V2)."""

from __future__ import annotations

from app.application import memoire


def _conversation(nb_tours: int) -> list[dict[str, str]]:
    """Construit nb_tours échanges user/assistant numérotés (du plus ancien au récent)."""
    messages: list[dict[str, str]] = []
    for i in range(nb_tours):
        messages.append({"role": "user", "content": f"Question {i} sur le cacao"})
        messages.append({"role": "assistant", "content": f"Conseil {i} pour le cacao"})
    return messages


def test_historique_court_passe_inchange() -> None:
    """Sous le seuil, l'historique complet est conservé (comportement V1)."""
    historique = _conversation(3)  # 6 messages <= seuil
    assert memoire.fenetre_dialogue(historique) == historique


def test_historique_long_est_resume_puis_fenetre() -> None:
    """Au-delà du seuil : un résumé en tête puis seulement la fenêtre récente."""
    historique = _conversation(12)  # 24 messages > seuil (16)
    fenetre = memoire.fenetre_dialogue(historique, fenetre=8, seuil=16)

    # 1 message de résumé + 8 messages récents.
    assert len(fenetre) == 9
    assert fenetre[0]["role"] == "assistant"
    assert fenetre[0]["content"].startswith("Résumé de nos échanges précédents")
    # La fenêtre conserve mot pour mot les 8 derniers messages.
    assert fenetre[1:] == historique[-8:]


def test_fenetre_demarre_sur_un_message_utilisateur() -> None:
    """La fenêtre est calée sur un tour complet (commence par une question)."""
    historique = _conversation(12)
    fenetre = memoire.fenetre_dialogue(historique, fenetre=7, seuil=16)
    # Le premier message après le résumé est une question utilisateur.
    assert fenetre[1]["role"] == "user"


def test_resume_reprend_les_questions_et_le_dernier_conseil() -> None:
    """Le résumé extrait les questions anciennes récentes et le dernier conseil donné."""
    historique = _conversation(12)
    resume = memoire.fenetre_dialogue(historique, fenetre=4, seuil=16)[0]["content"]
    # La question ancienne la plus récente (juste avant la fenêtre) figure au résumé.
    assert "Question 9 sur le cacao" in resume
    assert "Dernier conseil donné" in resume


def test_resume_borne_le_nombre_de_points() -> None:
    """Le résumé ne liste qu'un nombre borné de questions (prompt maîtrisé)."""
    historique = _conversation(30)
    resume = memoire.fenetre_dialogue(historique, fenetre=4, seuil=16)[0]["content"]
    puces_questions = [
        ligne for ligne in resume.splitlines() if ligne.startswith("- Le producteur a demandé")
    ]
    assert len(puces_questions) <= memoire.MAX_POINTS


def test_resume_tronque_les_messages_longs() -> None:
    """Un message très long est compacté dans le résumé."""
    historique = _conversation(20)
    # Dernière question ancienne (index 34, conservée par le résumé) rendue démesurée.
    historique[34]["content"] = "x " * 500
    resume = memoire.fenetre_dialogue(historique, fenetre=4, seuil=16)[0]["content"]
    assert "…" in resume
    # Aucune ligne du résumé n'explose la borne de longueur.
    for ligne in resume.splitlines():
        assert len(ligne) <= memoire.LONGUEUR_POINT + 40
