"""Tests du registre analytique imposé aux sections de livrable."""

from __future__ import annotations

from app.services.gabarits import SectionGabarit
from app.services.prompts_redaction import SYSTEM_PROMPT_REDACTION, consigne_section


def test_le_registre_interdit_l_adresse_au_lecteur():
    """Le corpus est en registre « conseil au producteur » : on redresse."""
    minuscules = SYSTEM_PROMPT_REDACTION.lower()
    assert "troisième personne" in minuscules
    assert "ne t'adresse" in minuscules or "sans t'adresser" in minuscules


def test_le_registre_interdit_le_renvoi_anader_dans_une_etude():
    """Renvoyer un bailleur vers l ANADER serait faux."""
    assert "anader" in SYSTEM_PROMPT_REDACTION.lower()


def test_le_registre_interdit_d_inventer_un_chiffre():
    minuscules = SYSTEM_PROMPT_REDACTION.lower()
    assert "n'invente" in minuscules or "jamais un chiffre" in minuscules


def test_le_registre_interdit_les_titres():
    """La structure vient du gabarit ; le modele n emet jamais un titre."""
    assert "titre" in SYSTEM_PROMPT_REDACTION.lower()


def test_le_registre_interdit_produit_et_dosage():
    """Un document d etude ne prescrit pas davantage qu un conseil au producteur."""
    minuscules = SYSTEM_PROMPT_REDACTION.lower()
    assert "produit" in minuscules
    assert "dose" in minuscules


def test_le_registre_ne_souffle_aucun_dosage():
    """CLAUDE.md : jamais de dosage, meme en exemple — a fortiori dans un prompt.

    On n applique PAS ``contient_diagnostic`` ici : ce garde-fou vise les SORTIES du
    modele, et une consigne qui interdit de nommer un produit doit forcement le
    nommer pour l interdire. Le controle qui a du sens sur une entree est celui du
    dosage chiffre, que rien ne justifie d ecrire.
    """
    from app.services import guardrails

    assert guardrails.verifier_reponse(SYSTEM_PROMPT_REDACTION) is None


def test_la_consigne_de_section_reprend_le_titre_et_le_sujet():
    consigne = consigne_section(
        SectionGabarit(titre="Marché et prix", sources=("prix",), consigne="Rapporter le prix."),
        sujet="la campagne 2025-2026",
    )
    assert "Marché et prix" in consigne
    assert "la campagne 2025-2026" in consigne
    assert "Rapporter le prix." in consigne


def test_la_consigne_tient_sans_consigne_propre_a_la_section():
    consigne = consigne_section(
        SectionGabarit(titre="Contexte", sources=(), consigne=""), sujet="le cacao"
    )
    assert "Contexte" in consigne
    assert consigne.strip()
