"""La photo dans le chat : ce que le garde-fou d'image doit continuer d'interdire.

La règle 3 est purement lexicale : le mot « photo » suffit à refuser, sans qu'aucune
image n'existe. C'est un filet volontairement large, et il reste la bonne réponse tant
qu'aucune image n'est réellement analysée — promettre une lecture d'image qu'on ne fait
pas serait pire que refuser.

Quand une image RECEVABLE est jointe et passe par la cascade descriptive, le refus
d'entrée n'a plus lieu d'être : ce qui protège alors, ce sont les garde-fous de SORTIE
(``contient_diagnostic``), inchangés. On ouvre une porte, on n'en retire aucune.
"""

from __future__ import annotations

from app.models.domain import CategorieRefus
from app.services import guardrails

PHOTO_DIAGNOSTIC = "Regarde la photo de ma cabosse et dis-moi la maladie."


def test_sans_image_le_refus_reste_entier() -> None:
    """Le comportement d'aujourd'hui, et de tous les appelants qui ne changent pas."""
    refus = guardrails.evaluer(PHOTO_DIAGNOSTIC)

    assert refus is not None
    assert refus.categorie is CategorieRefus.DIAGNOSTIC_IMAGE


def test_avec_une_image_analysee_la_cascade_prend_le_relais() -> None:
    """Refuser une photo réellement jointe reviendrait à fermer la fonction qu'on ouvre."""
    refus = guardrails.evaluer(PHOTO_DIAGNOSTIC, image_analysee=True)

    assert refus is None or refus.categorie is not CategorieRefus.DIAGNOSTIC_IMAGE


def test_une_image_jointe_ne_leve_AUCUN_autre_garde_fou() -> None:
    """Le risque du dossier : qu'une image devienne un passe-droit. Chaque règle est
    reverifiée avec l'image présente."""
    cas = (
        ("Quelle dose de fongicide sur mes cabosses ?", CategorieRefus.PHYTOSANITAIRE),
        ("J'ai de la fièvre, quel médicament ?", CategorieRefus.MEDICAL),
        ("Comment traiter mon champ de maïs ?", CategorieRefus.HORS_FILIERE),
    )
    for question, attendu in cas:
        refus = guardrails.evaluer(question, image_analysee=True)
        assert refus is not None, question
        assert refus.categorie is attendu, question


def test_le_garde_fou_de_sortie_reste_le_dernier_mot() -> None:
    """Nommer une maladie reste interdit, image ou pas : c'est lui qui tient la doctrine."""
    assert guardrails.contient_diagnostic("Il s'agit du swollen shoot.")
    assert guardrails.contient_diagnostic("Appliquez un fongicide sur les cabosses.")
