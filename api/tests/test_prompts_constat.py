"""Tests des consignes encadrant le constat visuel."""

from __future__ import annotations

from app.services.prompts_constat import CONSIGNE_DESCRIPTION, consigne_redaction


def test_la_consigne_de_description_interdit_de_nommer_une_maladie():
    minuscules = CONSIGNE_DESCRIPTION.lower()
    assert "ne nomme" in minuscules or "sans nommer" in minuscules
    assert "maladie" in minuscules


def test_la_consigne_de_description_interdit_produit_et_dosage():
    minuscules = CONSIGNE_DESCRIPTION.lower()
    assert "produit" in minuscules
    assert "dose" in minuscules or "dosage" in minuscules


def test_la_consigne_de_redaction_reprend_les_facteurs_de_contexte():
    consigne = consigne_redaction(("Parcelle située à Daloa.", "Période : saison sèche."))
    assert "Daloa" in consigne
    assert "saison sèche" in consigne


def test_la_consigne_de_redaction_impose_l_orientation_anader():
    assert "ANADER" in consigne_redaction(())


def test_la_consigne_de_redaction_tient_sans_facteur():
    """Aucun contexte disponible : la consigne reste valide et ne ment pas."""
    consigne = consigne_redaction(())
    assert consigne
    assert "ANADER" in consigne
    assert "contexte" not in consigne.lower()
