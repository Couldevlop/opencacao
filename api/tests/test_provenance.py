"""Tests de la provenance — aucune affirmation ne sort sans source (D4)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.application.provenance import (
    affirmations_sans_source,
    construire_manifeste,
    empreinte_demandeur,
    tableau_de_provenance,
)
from app.models.constat import NiveauConfiance
from app.models.rapport import Affirmation, Document, Section


def _affirmation(source: str = "CNRA") -> Affirmation:
    return Affirmation(
        texte="La production a atteint 2,2 millions de tonnes.",
        source=source,
        date="2025-10-01",
        methode="rag",
        confiance=NiveauConfiance.MOYENNE,
    )


def _document(*affirmations: Affirmation, sections: tuple[Section, ...] | None = None) -> Document:
    return Document(
        titre="Étude de filière",
        sous_titre="Cacao ivoirien",
        sections=sections
        or (Section(titre="Contexte", corps="Un paragraphe.", affirmations=affirmations),),
        tableaux=(),
        manifeste=construire_manifeste(
            modele="opencacao-8b",
            version_modele="1.1.0",
            version_app="0.6.75",
            profil_materiel="cpu",
            demandeur="appareil-a",
            documents_rag=(("CNRA", "a" * 16),),
            outils=(("prix", "2026-07-29T10:00:00+00:00"),),
        ),
    )


def test_une_affirmation_sans_source_est_signalee():
    """Critere d acceptation : ce test echoue si une affirmation sort sans source."""
    orphelines = affirmations_sans_source(_document(_affirmation(source="")))
    assert len(orphelines) == 1


def test_une_affirmation_sourcee_ne_declenche_rien():
    assert affirmations_sans_source(_document(_affirmation())) == ()


def test_l_affirmation_remontee_est_bien_la_fautive():
    orphelines = affirmations_sans_source(
        _document(_affirmation(), _affirmation(source=""), _affirmation("ICCO"))
    )
    assert [a.source for a in orphelines] == [""]


def test_une_source_faite_d_espaces_ne_compte_pas():
    """« Sourcer » avec un blanc serait un contournement trivial."""
    assert len(affirmations_sans_source(_document(_affirmation(source="   ")))) == 1


@pytest.mark.parametrize("source", ["​", "-", "n/a", "N/A", "inconnu", "\x00"])
def test_une_source_vide_de_sens_ne_compte_pas(source):
    """strip() ne voit ni l espace de largeur nulle ni « n/a » : le filet doit."""
    assert len(affirmations_sans_source(_document(_affirmation(source=source)))) == 1


def test_une_source_absente_ne_fait_pas_tomber_la_verification():
    """Un None arrivant du moteur doit etre signale, pas lever une AttributeError."""
    affirmation = Affirmation(
        texte="Sans source.",
        source=None,  # type: ignore[arg-type]
        date="",
        methode="rag",
        confiance=NiveauConfiance.FAIBLE,
    )
    assert len(affirmations_sans_source(_document(affirmation))) == 1


def test_les_sections_au_dela_de_la_premiere_sont_verifiees_aussi():
    """Sans ce test, une verification limitee a sections[0] passerait au vert."""
    document = _document(
        sections=(
            Section(titre="Contexte", corps="…", affirmations=(_affirmation(),)),
            Section(titre="Marché", corps="…", affirmations=(_affirmation(source=""),)),
        )
    )
    orphelines = affirmations_sans_source(document)
    assert len(orphelines) == 1


def test_le_manifeste_porte_de_quoi_rejouer_le_document():
    manifeste = _document(_affirmation()).manifeste
    assert manifeste.modele == "opencacao-8b"
    assert manifeste.version_app == "0.6.75"
    assert manifeste.profil_materiel == "cpu"
    assert manifeste.documents_rag == (("CNRA", "a" * 16),)
    assert manifeste.outils == (("prix", "2026-07-29T10:00:00+00:00"),)


def test_le_manifeste_ne_contient_jamais_l_identifiant_du_demandeur():
    """X-Device-Id est une CLE D AUTORISATION : un .docx remis a un bailleur la livrerait."""
    manifeste = construire_manifeste(
        modele="opencacao-8b",
        version_modele="1.1.0",
        version_app="0.6.75",
        profil_materiel="cpu",
        demandeur="acct_secret-du-producteur",
    )
    assert "acct_secret-du-producteur" not in manifeste.empreinte_demandeur
    assert manifeste.empreinte_demandeur


def test_deux_documents_du_meme_demandeur_restent_correlables():
    """La tracabilite survit a l empreinte : c est tout l interet de hacher plutot qu effacer."""
    assert empreinte_demandeur("appareil-a") == empreinte_demandeur("appareil-a")
    assert empreinte_demandeur("appareil-a") != empreinte_demandeur("appareil-b")


def test_sans_demandeur_l_empreinte_reste_vide():
    assert empreinte_demandeur("") == ""


def test_le_manifeste_est_horodate_en_utc():
    manifeste = _document(_affirmation()).manifeste
    assert manifeste.genere_le.tzinfo is UTC
    assert manifeste.genere_le <= datetime.now(UTC)


def test_le_tableau_de_provenance_reprend_chaque_affirmation():
    tableau = tableau_de_provenance(_document(_affirmation(), _affirmation("ICCO")))
    assert tableau.entetes == ("Section", "Affirmation", "Source", "Date", "Méthode", "Confiance")
    assert len(tableau.lignes) == 2
    assert tableau.lignes[0][2] == "CNRA"
    assert tableau.lignes[1][2] == "ICCO"


def test_le_tableau_de_provenance_nomme_la_section_d_origine():
    """Un auditeur doit pouvoir remonter du chiffre a l endroit ou il est affirme."""
    tableau = tableau_de_provenance(_document(_affirmation()))
    assert tableau.lignes[0][0] == "Contexte"


def test_un_document_sans_affirmation_donne_un_tableau_vide():
    tableau = tableau_de_provenance(_document())
    assert tableau.lignes == ()
