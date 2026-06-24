"""Tests du service de titre automatique de conversation (B3, V2)."""

from __future__ import annotations

from app.models.session import TITRE_PAR_DEFAUT
from app.services import titres


def test_titre_depuis_question_simple() -> None:
    """Une question claire donne un titre propre, sans ponctuation terminale."""
    titre = titres.depuis_question("Comment bien sécher mes fèves de cacao ?")
    assert titre == "Comment bien sécher mes fèves de cacao"


def test_titre_capitalise_la_premiere_lettre() -> None:
    titre = titres.depuis_question("quand récolter les cabosses")
    assert titre == "Quand récolter les cabosses"


def test_titre_retire_une_amorce_de_politesse() -> None:
    """« Bonjour, … » n'apporte rien au titre et est retiré."""
    titre = titres.depuis_question("Bonjour, comment tailler le cacaoyer ?")
    assert titre == "Comment tailler le cacaoyer"


def test_titre_compacte_les_espaces() -> None:
    titre = titres.depuis_question("  Mes   feuilles\n jaunissent   ")
    assert titre == "Mes feuilles jaunissent"


def test_titre_tronque_sur_une_frontiere_de_mot() -> None:
    """Une question longue est tronquée proprement, suffixée d'une ellipse."""
    question = (
        "Comment puis-je améliorer durablement le rendement de ma vieille "
        "plantation de cacaoyers située dans la région de Daloa cette année ?"
    )
    titre = titres.depuis_question(question)
    assert titre.endswith("…")
    assert len(titre) <= titres.LONGUEUR_MAX + 1
    assert " " in titre  # pas de mot coupé en plein milieu collé à l'ellipse
    assert not titre[:-1].endswith(" ")


def test_titre_question_vide_renvoie_le_defaut() -> None:
    assert titres.depuis_question("   ") == TITRE_PAR_DEFAUT


def test_titre_ponctuation_seule_renvoie_le_defaut() -> None:
    assert titres.depuis_question("?!??") == TITRE_PAR_DEFAUT


def test_titre_amorce_seule_renvoie_le_defaut() -> None:
    """« Bonjour » seul ne laisse aucun contenu titrable."""
    assert titres.depuis_question("Bonjour") == TITRE_PAR_DEFAUT
