"""Tests des types de domaine du constat visuel."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest

from app.models.constat import (
    Constat,
    EtatRevue,
    NiveauConfiance,
    Observation,
    Organe,
)


def _observation(**surcharges) -> Observation:
    defauts = {
        "organe": Organe.CABOSSE,
        "description": "Cabosse mûre, surface régulière, aucune lésion visible.",
        "confiance": NiveauConfiance.MOYENNE,
        "empreinte_image": "a" * 64,
    }
    return Observation(**{**defauts, **surcharges})


def test_observation_est_immuable():
    obs = _observation()
    with pytest.raises(dataclasses.FrozenInstanceError):
        obs.description = "autre"  # type: ignore[misc]


def test_constat_naît_en_attente_de_revue():
    constat = Constat(
        identifiant="c1",
        capture="cap1",
        parcelle="p1",
        proprietaire="appareil-a",
        observations=(_observation(),),
        texte="Constat visuel…",
        confiance=NiveauConfiance.MOYENNE,
        cree_le=datetime.now(UTC),
    )
    assert constat.etat_revue is EtatRevue.EN_ATTENTE
    assert constat.revu_par == ""


def test_organe_couvre_les_quatre_cas_plus_indetermine():
    assert {o.value for o in Organe} == {
        "cabosse",
        "feuille",
        "tronc",
        "vue_ensemble",
        "indetermine",
    }


def test_niveau_confiance_ordonne():
    """La fusion contextuelle doit pouvoir dégrader : l'ordre doit être comparable."""
    assert NiveauConfiance.FAIBLE.rang < NiveauConfiance.MOYENNE.rang
    assert NiveauConfiance.MOYENNE.rang < NiveauConfiance.ELEVEE.rang


def test_degrader_descend_d_un_cran_et_rend_un_membre_de_l_enum():
    """La fusion contextuelle compare le résultat par identité : jamais une chaîne nue."""
    assert NiveauConfiance.ELEVEE.degrader() is NiveauConfiance.MOYENNE
    assert NiveauConfiance.MOYENNE.degrader() is NiveauConfiance.FAIBLE


def test_degrader_a_un_plancher():
    assert NiveauConfiance.FAIBLE.degrader() is NiveauConfiance.FAIBLE


def test_etat_revue_couvre_le_cycle_anader():
    assert {e.value for e in EtatRevue} == {"en_attente", "confirme", "corrige", "rejete"}
