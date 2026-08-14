"""Résolution des demandes contre le catalogue RÉELLEMENT livré.

Les autres tests d'intention travaillent sur un catalogue fabriqué : ils vérifient
l'**algorithme**. Celui-ci vérifie le **vocabulaire**, ce qui ne peut se voir qu'en
confrontant les gabarits livrés les uns aux autres.

La distinction n'est pas académique. Ajouter un gabarit dont un déclencheur recouvre
celui d'un autre ne casse aucun test d'algorithme : le score des deux monte à égalité,
et une demande jusque-là comprise devient silencieusement une question de
clarification. Le seul endroit où cette régression est visible, c'est ici.
"""

from __future__ import annotations

import pytest

from app.application.intention_rapport import resoudre_demande
from app.services.gabarits import Gabarit, charger_gabarit, lister_gabarits, vider_cache


@pytest.fixture(autouse=True)
def cache_neuf():
    """Le catalogue est mis en cache : on ne veut pas hériter d'un autre test."""
    vider_cache()
    yield
    vider_cache()


def catalogue_livre() -> tuple[Gabarit, ...]:
    """Charge tous les gabarits effectivement présents dans l'image."""
    return tuple(charger_gabarit(identifiant) for identifiant in lister_gabarits())


@pytest.mark.parametrize(
    ("demande", "attendu"),
    [
        # Chaque type doit rester joignable par sa formule la plus naturelle.
        ("Fais-moi une étude de la filière cacao", "etude_filiere"),
        ("Je voudrais une étude de marché sur le cacao ivoirien", "etude_marche"),
        ("Prépare un benchmark de la Côte d'Ivoire et du Ghana", "benchmark_filiere"),
        ("Une comparaison de la filière ivoirienne et ghanéenne", "benchmark_filiere"),
        ("Un bulletin pour la région de Daloa", "bulletin_regional"),
        ("Prépare le dossier de la parcelle de Kouadio", "dossier_parcelle"),
    ],
)
def test_chaque_type_reste_joignable_par_sa_formule_naturelle(demande: str, attendu: str) -> None:
    intention = resoudre_demande(demande, catalogue_livre())
    assert intention.gabarit == attendu
    assert intention.certaine is True


def test_l_etude_de_marche_l_emporte_sur_l_etude_de_filiere() -> None:
    """« étude de marché » porte DEUX déclencheurs du gabarit marché, un seul de filière.

    C'est le mécanisme qui départage : le gabarit le plus spécifique accumule, il ne
    revendique pas un mot à lui seul. Sans cela, « étude de marché » serait à égalité
    et partirait en clarification.
    """
    intention = resoudre_demande("une étude de marché sur la campagne 2025-2026", catalogue_livre())
    assert intention.gabarit == "etude_marche"
    assert intention.sujet == "la campagne 2025-2026"


def test_une_etude_sans_qualificatif_demande_laquelle() -> None:
    """« Fais-moi une étude » est AMBIGU depuis qu'il existe deux études. On le dit.

    Comportement voulu, pas une régression : la doctrine du projet est que ce qui
    n'est pas certain n'est pas deviné. Le prix est une question de plus à l'écran ;
    l'alternative — trancher au hasard entre une étude de filière et une étude de
    marché — produirait le mauvais document sans prévenir.
    """
    intention = resoudre_demande("Fais-moi une étude sur la campagne 2025-2026", catalogue_livre())
    assert intention.certaine is False
    assert set(intention.candidats) == {"etude_filiere", "etude_marche"}


def test_aucun_gabarit_ne_revendique_le_vocabulaire_d_un_autre_a_lui_seul() -> None:
    """Deux gabarits ne peuvent pas porter EXACTEMENT le même jeu de déclencheurs.

    Ce cas-là n'est pas rattrapable par l'accumulation : ils resteraient à égalité
    sur toute demande, et l'un des deux deviendrait inatteignable.
    """
    vocabulaires = {
        gabarit.identifiant: frozenset(mot.lower() for mot in gabarit.declencheurs)
        for gabarit in catalogue_livre()
    }
    doublons = [
        (un, autre)
        for un, mots_un in vocabulaires.items()
        for autre, mots_autre in vocabulaires.items()
        if un < autre and mots_un == mots_autre
    ]
    assert doublons == []
