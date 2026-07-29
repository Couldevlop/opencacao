"""Tests du garde-fou anti-diagnostic sur les sorties de constat visuel (D3)."""

from __future__ import annotations

import pytest

from app.services.guardrails import contient_diagnostic


@pytest.mark.parametrize(
    "texte",
    [
        "Il s'agit de la pourriture brune des cabosses.",
        "Ces symptômes évoquent le swollen shoot.",
        "Attaque de mirides caractérisée.",
        "Le Phytophthora est en cause.",
        "C'est une anthracnose.",
    ],
)
def test_un_nom_de_maladie_est_refuse(texte):
    assert contient_diagnostic(texte) is not None


@pytest.mark.parametrize(
    "texte",
    [
        "Les cabosses présentent des taches brunes étendues sur environ un tiers de leur surface.",
        "Feuillage vert clair, quelques feuilles tachetées, port général affaissé.",
        "Vue d'ensemble : ombrage dense, sous-bois peu entretenu.",
    ],
)
def test_une_description_de_symptome_passe(texte):
    """Décrire ce qu'on voit est autorisé ; nommer la cause ne l'est pas."""
    assert contient_diagnostic(texte) is None


def test_le_terme_fautif_est_rendu_pour_la_journalisation():
    fautif = contient_diagnostic("Ces cabosses ont la pourriture brune.")
    assert fautif is not None
    assert "pourriture brune" in fautif.lower()


def test_la_detection_ignore_la_casse_et_les_accents():
    assert contient_diagnostic("POURRITURE BRUNE confirmée") is not None
    assert contient_diagnostic("swollen-shoot probable") is not None


@pytest.mark.parametrize(
    "texte",
    [
        "Appliquez un fongicide cuprique sur les cabosses atteintes.",
        "Un insecticide homologué réglera le problème.",
        "Utilisez un produit de traitement adapté.",
    ],
)
def test_nommer_un_produit_est_refuse(texte):
    """D3 : le constat ne nomme jamais un produit, pas plus qu'une maladie."""
    assert contient_diagnostic(texte) is not None


def test_le_produit_fautif_est_rendu_pour_la_journalisation():
    assert contient_diagnostic("Appliquez un fongicide cuprique.") == "fongicide"


def test_les_gestes_sans_produit_restent_autorises():
    """Récolte sanitaire, élagage, aération : ni maladie ni produit, donc admis."""
    texte = (
        "Ramassez et évacuez les cabosses atteintes, élaguez pour aérer la parcelle, "
        "puis montrez ces photos à votre agent ANADER."
    )
    assert contient_diagnostic(texte) is None
