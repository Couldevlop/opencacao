"""Tests des gabarits déclaratifs de livrables."""

from __future__ import annotations

import pytest

from app.services.gabarits import (
    SOURCES_CONNUES,
    GabaritInconnu,
    GabaritInvalide,
    charger_gabarit,
    lire_gabarit,
    lister_gabarits,
)


def test_les_trois_gabarits_de_la_spec_sont_livres():
    assert set(lister_gabarits()) == {"etude_filiere", "dossier_parcelle", "bulletin_regional"}


def test_sans_dossier_de_gabarits_la_liste_est_vide(monkeypatch, tmp_path):
    """Deploiement incomplet : on rend une liste vide, on ne leve pas au demarrage."""
    from app.services import gabarits as module

    monkeypatch.setattr(module, "_DOSSIER", tmp_path / "absent")
    assert module.lister_gabarits() == ()
    with pytest.raises(GabaritInconnu):
        module.charger_gabarit("etude_filiere")


def test_un_gabarit_inconnu_est_refuse():
    with pytest.raises(GabaritInconnu):
        charger_gabarit("gabarit-qui-n-existe-pas")


@pytest.mark.parametrize(
    "identifiant",
    ["../../../etc/passwd", "../sources_agro", "etude_filiere/../../secret", "a\x00b"],
)
def test_un_identifiant_hors_liste_blanche_est_refuse(identifiant):
    """L identifiant vient d une requete HTTP : jamais de chemin assemble."""
    with pytest.raises(GabaritInconnu):
        charger_gabarit(identifiant)


def test_l_etude_de_filiere_a_des_sections_ordonnees():
    gabarit = charger_gabarit("etude_filiere")
    assert gabarit.identifiant == "etude_filiere"
    assert len(gabarit.sections) >= 5
    assert all(section.titre for section in gabarit.sections)


def test_chaque_section_declare_des_sources_connues():
    """Une source inconnue serait silencieusement ignoree a la redaction."""
    for identifiant in lister_gabarits():
        for section in charger_gabarit(identifiant).sections:
            assert set(section.sources) <= SOURCES_CONNUES


def test_le_dossier_de_parcelle_porte_la_mention_preparatoire():
    """D5 : ce dossier n est PAS une declaration de conformite."""
    mention = charger_gabarit("dossier_parcelle").mention.lower()
    assert "préparatoire" in mention
    assert "ne constitue" in mention


def test_les_autres_gabarits_n_ont_pas_de_mention():
    """La mention D5 est propre au dossier de parcelle, pas un ornement global."""
    assert charger_gabarit("etude_filiere").mention == ""
    assert charger_gabarit("bulletin_regional").mention == ""


def test_un_gabarit_sans_section_est_invalide():
    with pytest.raises(GabaritInvalide):
        lire_gabarit({"id": "vide", "titre": "T", "sections": []})


def test_un_gabarit_sans_titre_est_invalide():
    with pytest.raises(GabaritInvalide):
        lire_gabarit({"id": "x", "sections": [{"titre": "S", "sources": ["rag"]}]})


def test_un_gabarit_dont_une_section_n_a_pas_de_titre_est_invalide():
    """Le modele n emet jamais un titre : s il manque, personne ne le fournira."""
    with pytest.raises(GabaritInvalide):
        lire_gabarit({"id": "x", "titre": "T", "sections": [{"sources": ["rag"]}]})


def test_un_gabarit_declarant_une_source_inconnue_est_invalide():
    with pytest.raises(GabaritInvalide):
        lire_gabarit(
            {"id": "x", "titre": "T", "sections": [{"titre": "S", "sources": ["horoscope"]}]}
        )


def test_une_section_sans_source_est_valide():
    """« Limites de l etude » ne mobilise rien : c est licite, et ce sera une lacune."""
    gabarit = lire_gabarit({"id": "x", "titre": "T", "sections": [{"titre": "Limites"}]})
    assert gabarit.sections[0].sources == ()


def test_le_titre_accepte_un_champ_a_substituer():
    """« Étude de filière — {sujet} » : la substitution est faite par le moteur."""
    assert "{sujet}" in charger_gabarit("etude_filiere").titre


def test_les_gabarits_livres_ne_portent_aucun_dosage_ni_nom_de_maladie():
    """Un gabarit est une consigne au modele : il ne doit rien lui souffler d interdit."""
    from app.services import guardrails

    for identifiant in lister_gabarits():
        gabarit = charger_gabarit(identifiant)
        for section in gabarit.sections:
            assert guardrails.contient_diagnostic(section.consigne) is None
