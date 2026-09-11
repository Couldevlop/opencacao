"""Une source que le modèle invente ne doit pas rester écrite dans la réponse.

Écart de production du 11/09/2026, sur une question météo : « D'après les prévisions…
3,2 mm en 24 heures… **Sources : Conseil du Café-Cacao.** » L'organisme de conseil
agricole ne fait pas la pluie, et son nom n'était nulle part dans le contexte injecté.

Le système avait pourtant bien fait son travail : l'ancrage a rejeté la source, la
confiance est tombée à « faible », aucun badge n'a été affiché. Mais le TEXTE, lui,
portait toujours l'attribution. On détectait la fabrication, on la classait — et on
l'affichait quand même. Devant un public, c'est la phrase qu'on retient.
"""

from __future__ import annotations

from app.services import postprocess


def test_une_source_non_ancree_disparait_du_texte() -> None:
    reponse = "Il pleuvra 3,2 mm à Daloa cette semaine. Sources : Conseil du Café-Cacao."

    propre = postprocess.retirer_sources_non_ancrees(reponse, [])

    assert "Conseil du Café-Cacao" not in propre
    assert "3,2 mm" in propre


def test_une_source_ancree_reste_ecrite() -> None:
    """La citation légitime est le cœur du produit : on n'y touche pas."""
    reponse = "Le prix est de 1 200 F CFA/kg. Sources : Conseil du Café-Cacao."

    propre = postprocess.retirer_sources_non_ancrees(reponse, ["Conseil du Café-Cacao"])

    assert propre == reponse


def test_seules_les_sources_fabriquees_tombent() -> None:
    """Une attribution mixte garde ce qui est fondé et perd le reste."""
    reponse = "Taillez après la récolte. Sources : ANADER, Conseil du Café-Cacao."

    propre = postprocess.retirer_sources_non_ancrees(reponse, ["ANADER"])

    assert "ANADER" in propre
    assert "Conseil du Café-Cacao" not in propre


def test_un_texte_sans_attribution_est_rendu_tel_quel() -> None:
    reponse = "Taillez votre cacaoyer après la récolte."

    assert postprocess.retirer_sources_non_ancrees(reponse, []) == reponse


def test_la_phrase_finale_ne_laisse_pas_de_moignon() -> None:
    """« Sources : . » serait pire que la mention d'origine."""
    reponse = "Il pleuvra demain. Sources : FAO."

    propre = postprocess.retirer_sources_non_ancrees(reponse, [])

    assert "Sources" not in propre
    assert propre.strip().endswith("demain.")


def test_le_singulier_est_traite_comme_le_pluriel() -> None:
    """Le modèle écrit « Source : » aussi souvent que « Sources : »."""
    reponse = "Il pleuvra demain. Source : CNRA"

    assert "CNRA" not in postprocess.retirer_sources_non_ancrees(reponse, [])
