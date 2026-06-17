"""Tests des garde-fous métier — un test par règle de refus (CLAUDE §4.3)."""

from __future__ import annotations

import pytest

from app.models.domain import CategorieRefus
from app.services import guardrails


def test_refus_dosage_phytosanitaire_avec_intention() -> None:
    """Une demande de dosage phytosanitaire est refusée et redirige vers l'ANADER."""
    refus = guardrails.evaluer(
        "Quelle dose de fongicide dois-je appliquer contre la pourriture brune ?"
    )
    assert refus is not None
    assert refus.categorie is CategorieRefus.PHYTOSANITAIRE
    assert "ANADER" in refus.message


def test_refus_dosage_phytosanitaire_valeur_chiffree() -> None:
    """Un nombre + unité associé à un produit phyto déclenche le refus."""
    refus = guardrails.evaluer("Je mets 50 ml d'insecticide par pulvérisateur, c'est bon ?")
    assert refus is not None
    assert refus.categorie is CategorieRefus.PHYTOSANITAIRE


def test_refus_demande_medicale() -> None:
    """Une demande médicale/vétérinaire est refusée."""
    refus = guardrails.evaluer("Mon enfant a de la fièvre, quel médicament donner ?")
    assert refus is not None
    assert refus.categorie is CategorieRefus.MEDICAL


def test_refus_diagnostic_sur_image() -> None:
    """Une demande d'identification sur photo est refusée sans agent."""
    refus = guardrails.evaluer("Regarde la photo de ma cabosse et dis-moi la maladie.")
    assert refus is not None
    assert refus.categorie is CategorieRefus.DIAGNOSTIC_IMAGE


def test_refus_hors_filiere() -> None:
    """Une question clairement hors filière cacao est refusée."""
    refus = guardrails.evaluer("Comment investir dans le bitcoin ?")
    assert refus is not None
    assert refus.categorie is CategorieRefus.HORS_FILIERE


@pytest.mark.parametrize(
    "question",
    [
        "Mes feuilles de cacaoyer jaunissent, que faire ?",
        "Comment reconnaître la pourriture brune des cabosses ?",
        "Quand récolter les cabosses de cacao ?",
        "Combien de temps doit durer la fermentation du cacao ?",
    ],
)
def test_questions_legitimes_passent(question: str) -> None:
    """Les questions agronomiques légitimes ne déclenchent aucun garde-fou."""
    assert guardrails.evaluer(question) is None


def test_culture_connexe_autorisee() -> None:
    """Une question sur une culture connexe (anacarde) reste dans le périmètre."""
    assert guardrails.evaluer("Comment entretenir mes anacardiers en saison sèche ?") is None


# --- Garde-fou de SORTIE (verifier_reponse) ---


@pytest.mark.parametrize(
    "reponse",
    [
        "Appliquez 2 l/ha de bouillie sur les cabosses atteintes.",
        "Diluez a 1,25 g/L puis pulverisez.",
        "Utilisez 50 ml par litre d'eau pour le traitement.",
    ],
)
def test_verifier_reponse_bloque_un_dosage(reponse: str) -> None:
    """Une réponse contenant un dosage chiffré est bloquée (redirection ANADER)."""
    refus = guardrails.verifier_reponse(reponse)
    assert refus is not None
    assert refus.categorie is CategorieRefus.PHYTOSANITAIRE


@pytest.mark.parametrize(
    "reponse",
    [
        "Récoltez les cabosses bien mûres, fermes et colorées. Sources : CNRA.",
        "Espacez les cacaoyers d'environ 3 mètres pour une bonne aération.",
        "La fermentation dure 5 à 7 jours en caisse.",
    ],
)
def test_verifier_reponse_laisse_passer_le_legitime(reponse: str) -> None:
    """Une réponse agronomique normale (sans dosage phyto) passe."""
    assert guardrails.verifier_reponse(reponse) is None
