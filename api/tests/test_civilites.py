"""Tests du court-circuit des civilités (tours de parole sans demande de conseil).

Les tests NÉGATIFS portent tout le risque : ce module s'exécute AVANT les garde-fous,
donc tout ce qui n'est pas une pure formule de politesse doit lui échapper.
"""

from __future__ import annotations

import pytest

from app.services import civilites
from app.services.civilites import Civilite


@pytest.mark.parametrize(
    "question",
    ["Bonjour", "bonjour !", "Bonsoir", "Salut", "Bonjour, comment allez-vous ?", "Coucou"],
)
def test_une_formule_daccueil_seule_est_une_salutation(question: str) -> None:
    """Un tour qui ne contient qu'une salutation est reconnu comme tel."""
    assert civilites.detecter(question) is Civilite.SALUTATION


@pytest.mark.parametrize(
    "question",
    ["Bonjour, mes cabosses noircissent", "Salut, quand récolter ?", "Bonsoir j'ai un souci"],
)
def test_une_salutation_suivie_dune_demande_nest_pas_une_civilite(question: str) -> None:
    """Le tour reste une demande de conseil dès qu'il subsiste du texte après la formule.

    C'est LE test qui protège le chemin nominal : sans lui, « Bonjour, mes cabosses
    noircissent » recevrait un bonjour poli au lieu d'un conseil.
    """
    assert civilites.detecter(question) is None


def test_une_demande_de_dosage_polie_reste_soumise_aux_garde_fous() -> None:
    """Une question phytosanitaire enrobée de politesse n'est PAS court-circuitée.

    Le module s'exécutant avant les garde-fous, l'y laisser passer les contournerait.
    """
    assert civilites.detecter("Bonjour, quelle dose de fongicide pour mes cabosses ?") is None


@pytest.mark.parametrize(
    "question", ["Merci", "merci beaucoup !", "Merci bien", "Je vous remercie"]
)
def test_un_remerciement_est_reconnu(question: str) -> None:
    """Un remerciement seul est une civilité."""
    assert civilites.detecter(question) is Civilite.REMERCIEMENT


@pytest.mark.parametrize("question", ["Au revoir", "à bientôt", "Bonne journée", "Bye"])
def test_un_adieu_est_reconnu(question: str) -> None:
    """Une formule de congé seule est une civilité."""
    assert civilites.detecter(question) is Civilite.ADIEU


@pytest.mark.parametrize(
    "question",
    ["Qui es-tu ?", "tu es qui", "C'est quoi OpenCacao ?", "Que peux-tu faire ?", "Tu sers à quoi"],
)
def test_une_question_didentite_est_reconnue(question: str) -> None:
    """« Qui es-tu ? » est un tour de conversation, pas une demande agronomique."""
    assert civilites.detecter(question) is Civilite.IDENTITE


@pytest.mark.parametrize("question", ["Ok", "d'accord", "très bien", "compris"])
def test_un_acquiescement_est_reconnu(question: str) -> None:
    """Un simple acquiescement ne mérite pas une consultation complète."""
    assert civilites.detecter(question) is Civilite.ACQUIESCEMENT


@pytest.mark.parametrize(
    "question",
    [
        "Quand récolter les cabosses de cacao ?",
        "Mes feuilles jaunissent",
        "",
        "   ",
        "Quel est le prix du cacao ?",
    ],
)
def test_une_vraie_demande_nest_jamais_une_civilite(question: str) -> None:
    """Une question agronomique — ou un tour vide — ne déclenche pas le court-circuit."""
    assert civilites.detecter(question) is None


def test_la_salutation_invite_le_producteur_a_decrire_sa_plantation() -> None:
    """La réponse à un bonjour ouvre le dialogue au lieu de conseiller dans le vide."""
    texte = civilites.repondre(Civilite.SALUTATION)
    assert "OpenCacao" in texte
    assert "?" in texte  # elle rend la parole au producteur


def test_la_salutation_rappelle_le_fil_deja_engage() -> None:
    """Sur une conversation reprise, le bonjour rappelle ce dont on parlait."""
    texte = civilites.repondre(Civilite.SALUTATION, rappel="vos cabosses qui noircissent, à Soubré")
    assert "vos cabosses qui noircissent, à Soubré" in texte


def test_la_reponse_didentite_ne_se_fait_pas_passer_pour_un_agent_anader() -> None:
    """OpenCacao dit ce qu'il est, et renvoie à l'ANADER pour le terrain (souveraineté)."""
    texte = civilites.repondre(Civilite.IDENTITE)
    assert "ANADER" in texte


@pytest.mark.parametrize("civilite", list(Civilite))
def test_toute_civilite_recoit_une_reponse_non_vide(civilite: Civilite) -> None:
    """Aucun type de civilité ne laisse le producteur sans réponse."""
    assert civilites.repondre(civilite).strip()


@pytest.mark.parametrize("civilite", [Civilite.REMERCIEMENT, Civilite.ADIEU])
def test_un_rappel_de_fil_nest_pas_colle_aux_formules_de_cloture(civilite: Civilite) -> None:
    """On ne relance pas quelqu'un qui remercie ou qui prend congé."""
    assert "Soubré" not in civilites.repondre(civilite, rappel="vos cabosses, à Soubré")
