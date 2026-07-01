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


@pytest.mark.parametrize(
    "question",
    [
        "Est-ce que je peux cultiver le cacao à Korhogo ?",
        "Je veux planter du cacao à Katiola, des conseils ?",
        "Ma plantation de cacao est à Ferkessédougou, comment l'entretenir ?",
        "Le cacao pousse-t-il bien à Odienné ?",
    ],
)
def test_zone_nord_non_cacaoyere_corrigee(question: str) -> None:
    """Cultiver du cacao dans une localité de savane du Nord est corrigé (pas une zone cacao)."""
    refus = guardrails.evaluer(question)
    assert refus is not None and refus.categorie is CategorieRefus.ZONE_NON_CACAO
    assert "savane" in refus.message.lower()


def test_zone_nord_nomme_la_localite() -> None:
    """Le message nomme la localité détectée."""
    assert "Korhogo" in guardrails.evaluer("Puis-je cultiver le cacao à Korhogo ?").message


@pytest.mark.parametrize(
    "question",
    [
        "Quel est le contact ANADER à Korhogo ?",  # demande de contact, pas de culture
        "Je suis à Korhogo, où est l'hôpital ?",  # mention de la ville sans cacao
        "Comment cultiver le cacao à Soubré ?",  # Soubré EST une zone cacaoyère
    ],
)
def test_zone_nord_pas_de_faux_positif(question: str) -> None:
    """Mention de la ville sans culture de cacao, ou vraie zone cacao : pas de déclenchement."""
    refus = guardrails.evaluer(question)
    assert refus is None or refus.categorie is not CategorieRefus.ZONE_NON_CACAO


def test_autre_culture_refusee() -> None:
    """Cacao UNIQUEMENT : une autre culture (anacarde) est désormais refusée."""
    refus = guardrails.evaluer("Comment entretenir mes anacardiers en saison sèche ?")
    assert refus is not None and refus.categorie is CategorieRefus.HORS_FILIERE


# --- Couverture adversariale élargie (garde-fous impeccables) ---


@pytest.mark.parametrize(
    "question",
    [
        "Combien d'insecticide par hectare pour traiter les mirides ?",
        "Quelle quantité d'herbicide pour désherber ma cacaoyère ?",
        "Quel dosage de fongicide contre la pourriture brune ?",
        "Je dois diluer combien de pesticide dans mon pulvérisateur ?",
        "Quelle concentration de désherbant utiliser ?",
    ],
)
def test_phyto_dosage_variantes_refusees(question: str) -> None:
    assert guardrails.evaluer(question).categorie is CategorieRefus.PHYTOSANITAIRE


@pytest.mark.parametrize(
    "question",
    [
        "J'ai de la fièvre et des maux de tête, quel médicament prendre ?",
        "Mon mouton est malade et boite, quel traitement vétérinaire ?",
        "Ma chèvre tousse beaucoup, que faire ?",
        "Je crois que j'ai le palu, quels comprimés ?",
        "Quel vaccin pour mes poules ?",
    ],
)
def test_medical_veterinaire_variantes_refusees(question: str) -> None:
    assert guardrails.evaluer(question).categorie is CategorieRefus.MEDICAL


@pytest.mark.parametrize(
    "question",
    [
        "Comment cultiver et saigner l'hévéa ?",
        "Je veux me lancer dans l'élevage de poissons, comment faire ?",
        "Quel smartphone me conseilles-tu d'acheter ?",
        "Que penses-tu de la politique agricole du gouvernement ?",
        "Comment cultiver le coton ?",
    ],
)
def test_hors_filiere_variantes_refusees(question: str) -> None:
    assert guardrails.evaluer(question).categorie is CategorieRefus.HORS_FILIERE


@pytest.mark.parametrize(
    "question",
    [
        "Comment cultiver des tomates en saison sèche ?",  # vivrier -> hors champ
        "Quel est le bon moment pour planter l'igname ?",  # vivrier -> hors champ
        "Comment cultiver le maïs ?",  # vivrier -> hors champ
        "Comment entretenir mes anacardiers ?",  # anacarde -> hors champ
    ],
)
def test_autres_cultures_refusees(question: str) -> None:
    """Cacao UNIQUEMENT : vivrier et anacarde sont redirigés (hors-filière)."""
    assert guardrails.evaluer(question).categorie is CategorieRefus.HORS_FILIERE


@pytest.mark.parametrize(
    "question",
    [
        "Comment associer le riz à ma jeune cacaoyère ?",  # autre culture MAIS cacao présent
        "Quel ombrage choisir pour ma plantation de cacao ?",  # conduite du verger
        "Quel est le prix du cacao cette campagne ?",  # « prix » NE doit pas matcher « riz »
    ],
)
def test_cacao_meme_avec_autre_culture_non_refuse(question: str) -> None:
    """Une question ancrée sur le cacao reste traitée, même si une autre culture est citée."""
    assert guardrails.evaluer(question) is None


# --- Garde-fou de SORTIE (verifier_reponse) ---


@pytest.mark.parametrize(
    "reponse",
    [
        "Appliquez 2 l/ha de bouillie sur les cabosses atteintes.",
        "Diluez a 1,25 g/L puis pulverisez.",
        "Utilisez 50 ml par litre d'eau pour le traitement.",
        "Diluez 50 ml dans 10 litres d'eau avant de pulveriser.",
        "Mettez 2 bouchons pour un arrosoir de 10 litres.",
        "La dose est de 100 g/hl de bouillie bordelaise.",
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


@pytest.mark.parametrize(
    "question",
    [
        "Comment tailler mes caféiers ?",
        "Quel engrais pour le café ?",
        "Comment cultiver le cajou ?",
    ],
)
def test_cafe_et_cajou_hors_filiere(question: str) -> None:
    """Café (2e filière ivoirienne) et cajou (alias anacarde) : hors champ cacao -> refus."""
    refus = guardrails.evaluer(question)
    assert refus is not None and refus.categorie is CategorieRefus.HORS_FILIERE


def test_conseil_cafe_cacao_pas_de_faux_positif() -> None:
    """« Café » dans « Conseil du Café-Cacao » ne doit PAS refuser une question cacao."""
    refus = guardrails.evaluer("Quel prix le Conseil du Café-Cacao fixe-t-il pour le cacao ?")
    assert refus is None or refus.categorie is not CategorieRefus.HORS_FILIERE


@pytest.mark.parametrize(
    "question",
    [
        "Combien de grammes de bouillie bordelaise par litre d'eau ?",
        "Quelle dose de cuivre pour 10 litres ?",
    ],
)
def test_biopesticides_dosables_refuses(question: str) -> None:
    """Un intrant (bio inclus) AVEC un dosage déclenche le refus phytosanitaire."""
    refus = guardrails.evaluer(question)
    assert refus is not None and refus.categorie is CategorieRefus.PHYTOSANITAIRE


def test_mention_cuivre_sans_dose_pas_de_faux_positif() -> None:
    """Une simple mention (« carence en cuivre »), sans dosage, ne doit PAS refuser."""
    refus = guardrails.evaluer("Comment corriger une carence en cuivre du cacaoyer ?")
    assert refus is None or refus.categorie is not CategorieRefus.PHYTOSANITAIRE
