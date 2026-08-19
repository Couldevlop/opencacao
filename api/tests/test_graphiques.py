"""Socle analytique commun : les graphiques dérivés des affirmations réelles.

Un graphique est le RENDU d'éléments déjà établis, jamais un fait nouveau. Il ne peut
donc pas être inventé : sans affirmations, pas de graphique. C'est ce qui permet d'en
mettre dans toutes les études sans trahir la règle qui interdit d'estimer un chiffre.
"""

from __future__ import annotations

from app.models.constat import NiveauConfiance
from app.models.rapport import Affirmation, Section, TypeGraphique
from app.services.rendu import graphiques


def _aff(texte: str, source: str, confiance: NiveauConfiance) -> Affirmation:
    return Affirmation(texte=texte, source=source, date="", methode="rag", confiance=confiance)


def _sections() -> tuple[Section, ...]:
    return (
        Section(
            "Contexte",
            "corps",
            (
                _aff("a", "ANADER", NiveauConfiance.ELEVEE),
                _aff("b", "ANADER", NiveauConfiance.MOYENNE),
                _aff("c", "CNRA", NiveauConfiance.ELEVEE),
            ),
        ),
        Section("Marché", "corps", (_aff("d", "Conseil du Café-Cacao", NiveauConfiance.ELEVEE),)),
    )


def test_la_repartition_par_source_compte_les_affirmations_reelles() -> None:
    """Le camembert montre sur quoi le document repose — c'est ce qu'une commission ouvre."""
    graphique = graphiques.repartition_par_source(_sections())
    assert graphique is not None
    assert graphique.type is TypeGraphique.SECTEURS
    assert dict(zip(graphique.categories, graphique.valeurs, strict=True)) == {
        "ANADER": 2.0,
        "CNRA": 1.0,
        "Conseil du Café-Cacao": 1.0,
    }


def test_les_niveaux_de_confiance_sont_en_batons() -> None:
    """Trois barreaux, toujours dans le même ordre : élevée, moyenne, faible."""
    graphique = graphiques.niveaux_de_confiance(_sections())
    assert graphique is not None
    assert graphique.type is TypeGraphique.BATONS
    assert graphique.categories == ("Élevée", "Moyenne", "Faible")
    assert graphique.valeurs == (3.0, 1.0, 0.0)


def test_un_document_sans_affirmation_ne_produit_aucun_graphique() -> None:
    """Pas de données, pas de graphique — jamais un cadre vide pour faire savant."""
    vides = (Section("Limites", "Aucune source mobilisable.", (), lacune=True),)
    assert graphiques.repartition_par_source(vides) is None
    assert graphiques.niveaux_de_confiance(vides) is None


def test_le_socle_commun_rassemble_les_graphiques_de_toute_etude() -> None:
    """Toutes les études portent le même appareil : ce n'est pas une option demandée."""
    socle = graphiques.socle_analytique(_sections())
    assert [g.type for g in socle] == [
        TypeGraphique.SECTEURS,
        TypeGraphique.BATONS,
    ]
    assert all(g.titre for g in socle)


def test_le_socle_est_vide_quand_rien_n_est_source() -> None:
    """Un document en lacune totale ne s'invente pas un appareil statistique."""
    assert graphiques.socle_analytique((Section("X", "y", (), lacune=True),)) == ()


def test_le_tableau_de_synthese_des_sources_accompagne_le_camembert() -> None:
    """Le chiffre doit être lisible aussi hors du graphique (accessibilité, impression)."""
    tableau = graphiques.tableau_des_sources(_sections())
    assert tableau is not None
    assert tableau.entetes == ("Source", "Affirmations", "Part")
    assert ("ANADER", "2", "50,0 %") in tableau.lignes


def test_les_sources_sont_classees_de_la_plus_mobilisee_a_la_moins() -> None:
    """Un camembert dont les parts sautent au hasard se lit mal."""
    graphique = graphiques.repartition_par_source(_sections())
    assert graphique is not None
    assert graphique.categories[0] == "ANADER"
    assert list(graphique.valeurs) == sorted(graphique.valeurs, reverse=True)


def test_le_tableau_des_sources_est_absent_sans_affirmation() -> None:
    """Symétrique du graphique : rien de sourcé, rien de chiffré."""
    assert graphiques.tableau_des_sources((Section("X", "y", (), lacune=True),)) is None
