"""Tests du post-traitement : extraction des sources et estimation de confiance."""

from __future__ import annotations

from app.models.domain import Confiance
from app.services.postprocess import estimer_confiance, extraire_sources


def test_extraire_sources_reconnait_les_sources_citees() -> None:
    """Les sources du référentiel citées dans le texte sont extraites."""
    sources = extraire_sources("D'après le CNRA et l'ANADER, séchez au soleil.")
    assert "CNRA" in sources
    assert "ANADER" in sources


def test_extraire_sources_reconnait_firca_fao_icco() -> None:
    """FIRCA, FAO et ICCO (présents dans le corpus RAG) sont reconnus comme sources."""
    assert extraire_sources("Sources : FIRCA.") == ["FIRCA"]
    assert extraire_sources("Sources : FAO.") == ["FAO"]
    assert "ICCO" in extraire_sources("D'après l'ICCO et le CNRA...")


def test_extraire_sources_sans_doublon() -> None:
    """Une source citée plusieurs fois n'apparaît qu'une fois."""
    sources = extraire_sources("CNRA recommande... le CNRA confirme...")
    assert sources.count("CNRA") == 1


def test_confiance_elevee_si_deux_sources() -> None:
    assert estimer_confiance(["CNRA", "ANADER"]) is Confiance.ELEVEE


def test_confiance_moyenne_si_une_source() -> None:
    assert estimer_confiance(["CNRA"]) is Confiance.MOYENNE


def test_confiance_faible_si_aucune_source() -> None:
    assert estimer_confiance([]) is Confiance.FAIBLE


def test_extraire_sources_ancrees_seulement_avec_contexte() -> None:
    """Avec contexte : seules les sources présentes AUSSI dans le contexte sont retenues."""
    reponse = "D'après le CNRA et l'ANADER, séchez au soleil."
    contexte = "[1] (source : CNRA) Séchez les fèves au soleil."
    assert extraire_sources(reponse, contexte) == ["CNRA"]  # ANADER cité mais non ancré


def test_extraire_sources_sans_contexte_reste_textuel() -> None:
    """Sans contexte (None) : comportement legacy (extraction textuelle seule)."""
    assert "CNRA" in extraire_sources("selon le CNRA", None)


def test_extraire_sources_contexte_vide_aucune_ancree() -> None:
    """Réponse non ancrée (contexte vide) : aucune source ancrée -> confiance faible."""
    assert extraire_sources("selon le CNRA et l'ANADER", "") == []


# --- Troncature : une section ne doit jamais finir en plein mot ---


def test_un_texte_coupe_en_plein_mot_est_ramene_a_la_derniere_phrase() -> None:
    """Vécu le 20/08 : « …renforcer la position de la Côte d'I » sur un document livré.

    Le plafond de tokens coupe la génération là où elle en est. On ne peut pas la
    reprendre, mais on peut refuser de livrer une phrase amputée.
    """
    from app.services.postprocess import terminer_proprement

    coupe = "Le prix est fixé chaque année. Les acteurs de la filière sont **organis"
    assert terminer_proprement(coupe) == "Le prix est fixé chaque année."


def test_un_texte_deja_termine_n_est_pas_touche() -> None:
    """Le cas de loin le plus fréquent ne doit rien coûter ni rien perdre."""
    from app.services.postprocess import terminer_proprement

    entier = "Le prix officiel s'élève à 1 200 FCFA le kilogramme."
    assert terminer_proprement(entier) == entier


def test_un_texte_sans_aucune_fin_de_phrase_est_laisse_intact() -> None:
    """Mieux vaut une phrase inachevée qu'une section vidée de sa substance."""
    from app.services.postprocess import terminer_proprement

    presque_tout = "Une seule très longue phrase sans aucune ponctuation terminale qui court"
    assert presque_tout in terminer_proprement(presque_tout)


def test_les_autres_ponctuations_de_fin_sont_reconnues() -> None:
    """Une section peut se clore sur une interrogation ou des points de suspension."""
    from app.services.postprocess import terminer_proprement

    assert terminer_proprement("Fini ? Et la suite qui ne fini") == "Fini ?"
