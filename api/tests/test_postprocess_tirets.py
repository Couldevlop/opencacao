"""Les tirets cadratins trahissent la génération automatique — on les retire.

Demande de Waopron le 19/08/2026, après lecture des réponses en production : le tiret
cadratin (« — ») employé comme ponctuation est une signature de texte généré. Devant
une salle qui juge la crédibilité d'un outil souverain, cette signature travaille
contre nous.

Ce qui NE doit PAS être touché, et c'est tout l'enjeu du nettoyage :

* les traits d'union à l'intérieur des mots : *San-Pédro*, *aujourd'hui*,
  *peut-être*, *Café-Cacao*, *au-dessus* — les supprimer produirait des fautes ;
* les plages de nombres (*2020-2025*), qui restent lisibles avec un trait d'union ;
* les listes à puces, qui aident réellement un producteur à suivre des étapes.

Une consigne dans le prompt ne suffit pas : un modèle l'oublie une fois sur dix, et
c'est cette fois-là qui sera projetée à l'écran. D'où un nettoyage déterministe.
"""

from __future__ import annotations

import pytest

from app.services.postprocess import nettoyer_tirets


@pytest.mark.parametrize(
    ("brut", "attendu"),
    [
        (
            "Récoltez toutes les deux semaines — c'est le rythme conseillé.",
            "Récoltez toutes les deux semaines, c'est le rythme conseillé.",
        ),
        (
            "La taille – surtout la première – demande de la précision.",
            "La taille, surtout la première, demande de la précision.",
        ),
        (
            "Trois points comptent — l'ombrage, l'élagage et la récolte.",
            "Trois points comptent, l'ombrage, l'élagage et la récolte.",
        ),
    ],
)
def test_le_tiret_cadratin_de_ponctuation_devient_une_virgule(brut: str, attendu: str) -> None:
    assert nettoyer_tirets(brut) == attendu


@pytest.mark.parametrize(
    "texte",
    [
        "Rendez-vous à San-Pédro dès aujourd'hui.",
        "Le Conseil du Café-Cacao fixe le prix.",
        "C'est peut-être au-dessus de vos moyens.",
        "La campagne 2020-2025 a été difficile.",
        "- Taillez les gourmands\n- Récoltez tous les quinze jours",
    ],
)
def test_les_traits_d_union_legitimes_sont_preserves(texte: str) -> None:
    """Sans ce test, un nettoyage trop gourmand écrirait « SanPédro » ou « CaféCacao »
    — une faute d'orthographe projetée devant huit cents personnes."""
    assert nettoyer_tirets(texte) == texte


def test_le_tiret_en_debut_de_ligne_est_conserve_comme_puce() -> None:
    """Une liste d'étapes aide un producteur à suivre : on ne la casse pas."""
    texte = "Trois gestes :\n- tailler\n- récolter\n- fermenter"

    assert nettoyer_tirets(texte) == texte


def test_le_cadratin_colle_devient_un_trait_d_union() -> None:
    """Cas résiduel : « 2020—2025 » sans espaces reste une plage lisible."""
    assert nettoyer_tirets("campagne 2020—2025") == "campagne 2020-2025"


def test_un_texte_sans_tiret_est_rendu_intact() -> None:
    texte = "Récoltez vos cabosses toutes les deux semaines."

    assert nettoyer_tirets(texte) == texte


def test_le_texte_vide_ne_casse_pas() -> None:
    assert nettoyer_tirets("") == ""
