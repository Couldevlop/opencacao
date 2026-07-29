"""Tests de l'adaptateur Excel."""

from __future__ import annotations

import io
from datetime import UTC, datetime

import pytest
from openpyxl import load_workbook

from app.models.constat import NiveauConfiance
from app.models.rapport import Affirmation, Document, Manifeste, Section, Tableau
from app.services.rendu.tableur import rendu_excel


def _document(tableaux: tuple[Tableau, ...] | None = None) -> Document:
    return Document(
        titre="Étude de filière — le cacao",
        sous_titre="Analyse documentée",
        sections=(
            Section(
                titre="Contexte",
                corps="Un paragraphe analytique.",
                affirmations=(
                    Affirmation(
                        texte="La production avoisine 2,2 millions de tonnes.",
                        source="CNRA",
                        date="2025-10-01",
                        methode="rag",
                        confiance=NiveauConfiance.MOYENNE,
                        empreinte="e3b0c44298fc",
                    ),
                ),
            ),
        ),
        tableaux=(
            tableaux
            if tableaux is not None
            else (
                Tableau(
                    titre="Prix",
                    entetes=("Campagne", "Prix"),
                    lignes=(("2025-2026", "1 500"),),
                ),
            )
        ),
        manifeste=Manifeste(
            modele="opencacao-8b",
            version_modele="1.1.0",
            version_app="0.6.75",
            profil_materiel="cpu",
            genere_le=datetime(2026, 7, 29, 10, 0, tzinfo=UTC),
            empreinte_demandeur="a1b2c3d4e5f6",
        ),
    )


def _classeur(octets: bytes):
    return load_workbook(io.BytesIO(octets))


def test_le_rendu_est_un_xlsx_ouvrable():
    assert _classeur(rendu_excel(_document())).sheetnames


def test_la_provenance_a_sa_propre_feuille():
    """Spec §8.4 : c est la que l auditeur voudra trier et filtrer."""
    assert "Provenance" in _classeur(rendu_excel(_document())).sheetnames


def test_le_manifeste_a_sa_propre_feuille():
    assert "Manifeste" in _classeur(rendu_excel(_document())).sheetnames


def test_les_tableaux_de_donnees_ont_leur_feuille():
    classeur = _classeur(rendu_excel(_document()))
    assert "Prix" in classeur.sheetnames
    assert classeur["Prix"].cell(row=1, column=1).value == "Campagne"
    assert classeur["Prix"].cell(row=2, column=2).value == "1 500"


def test_la_feuille_de_provenance_porte_chaque_affirmation():
    feuille = _classeur(rendu_excel(_document()))["Provenance"]
    assert feuille.cell(row=1, column=1).value == "Section"
    assert feuille.cell(row=2, column=3).value == "CNRA"


def test_le_manifeste_est_lisible_en_paires_cle_valeur():
    feuille = _classeur(rendu_excel(_document()))["Manifeste"]
    paires = {
        feuille.cell(row=index, column=1).value: feuille.cell(row=index, column=2).value
        for index in range(1, feuille.max_row + 1)
    }
    assert paires["Modèle"] == "opencacao-8b"
    assert paires["Version applicative"] == "0.6.75"
    assert paires["Empreinte du demandeur"] == "a1b2c3d4e5f6"


def test_un_titre_de_tableau_trop_long_ne_casse_pas_le_classeur():
    """Excel plafonne un nom de feuille a 31 caracteres."""
    long = _document(tableaux=(Tableau(titre="T" * 60, entetes=("A",), lignes=(("x",),)),))
    assert all(len(nom) <= 31 for nom in _classeur(rendu_excel(long)).sheetnames)


def test_un_titre_de_tableau_avec_caracteres_interdits_est_normalise():
    """Excel refuse \\ / * ? : [ ] dans un nom de feuille."""
    sale = _document(tableaux=(Tableau(titre="Prix/2025[A]", entetes=("A",), lignes=(("x",),)),))
    noms = _classeur(rendu_excel(sale)).sheetnames
    assert all(not set(nom) & set("\\/*?:[]") for nom in noms)


def test_deux_tableaux_de_meme_titre_ne_se_percutent_pas():
    doublons = _document(
        tableaux=(
            Tableau(titre="Prix", entetes=("A",), lignes=(("x",),)),
            Tableau(titre="Prix", entetes=("A",), lignes=(("y",),)),
        )
    )
    classeur = _classeur(rendu_excel(doublons))
    feuilles = [nom for nom in classeur.sheetnames if nom.startswith("Prix")]
    assert len(feuilles) == 2


@pytest.mark.parametrize("valeur", ["=1+1", "+1", "-1", "@SUM(A1)", "\t=cmd", "\r=cmd"])
def test_une_valeur_ressemblant_a_une_formule_est_neutralisee(valeur):
    """CWE-1236 : openpyxl n echappe rien, et le contenu vient du modele.

    A l ouverture, une cellule commencant par = + - @ est evaluee comme une formule.
    Un livrable transmis a un bailleur ne doit pas executer quoi que ce soit chez lui.
    """
    piege = _document(tableaux=(Tableau(titre="T", entetes=("A",), lignes=((valeur,),)),))
    cellule = _classeur(rendu_excel(piege))["T"].cell(row=2, column=1)
    assert cellule.data_type == "s"
    assert not str(cellule.value).startswith(("=", "+", "-", "@"))


def test_une_valeur_ordinaire_n_est_pas_alteree():
    """La neutralisation ne doit pas defigurer les donnees legitimes."""
    normal = _document(tableaux=(Tableau(titre="T", entetes=("A",), lignes=(("1 500 FCFA",),)),))
    assert _classeur(rendu_excel(normal))["T"].cell(row=2, column=1).value == "1 500 FCFA"


def test_un_caractere_de_controle_ne_corrompt_pas_le_classeur():
    """openpyxl leve IllegalCharacterError sur un \\x00 non filtre."""
    sale = _document(tableaux=(Tableau(titre="T", entetes=("A",), lignes=(("x\x00y",),)),))
    valeur = _classeur(rendu_excel(sale))["T"].cell(row=2, column=1).value
    assert "\x00" not in str(valeur)


def test_un_document_sans_tableau_garde_provenance_et_manifeste():
    classeur = _classeur(rendu_excel(_document(tableaux=())))
    assert classeur.sheetnames == ["Provenance", "Manifeste"]


def test_au_dela_du_dedoublonnage_le_nom_de_repli_prend_la_main():
    """Cent tableaux homonymes : on ne laisse pas openpyxl renommer en silence."""
    homonymes = _document(
        tableaux=tuple(
            Tableau(titre="Prix", entetes=("A",), lignes=((str(index),),)) for index in range(100)
        )
    )
    noms = _classeur(rendu_excel(homonymes)).sheetnames
    assert len(noms) == len(set(noms))  # aucune collision
    assert len(noms) == 102  # 100 tableaux + Provenance + Manifeste
