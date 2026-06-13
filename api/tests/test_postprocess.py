"""Tests du post-traitement : extraction des sources et estimation de confiance."""

from __future__ import annotations

from app.models.domain import Confiance
from app.services.postprocess import estimer_confiance, extraire_sources


def test_extraire_sources_reconnait_les_sources_citees() -> None:
    """Les sources du référentiel citées dans le texte sont extraites."""
    sources = extraire_sources("D'après le CNRA et l'ANADER, séchez au soleil.")
    assert "CNRA" in sources
    assert "ANADER" in sources


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
