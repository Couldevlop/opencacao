"""Le deck doit avoir une identité — pas le gabarit par défaut de python-pptx.

Audit du 19/08 : thème Office 2007 (Calibri, accents 4F81BD/C0504D), format 4:3,
aucune couleur posée. Un deck en 4:3 aux teintes de 2007 est daté de quinze ans, et
il ne portait aucune trace d'OpenCacao alors que le Word en portait une.
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import UTC, datetime

from app.models.constat import NiveauConfiance
from app.models.rapport import (
    Affirmation,
    Document,
    Graphique,
    Manifeste,
    Section,
    Tableau,
    TypeGraphique,
)
from app.services.rendu.diapositives import rendu_pptx

ORANGE = "EA5B13"


def _document(mention: str = "") -> Document:
    return Document(
        titre="Étude de filière — cacao",
        sous_titre="Zone Sud-Ouest",
        sections=(
            Section(
                "Contexte de la filière",
                "Prose analytique de section.",
                (
                    Affirmation(
                        texte="a",
                        source="ANADER",
                        date="",
                        methode="rag",
                        confiance=NiveauConfiance.ELEVEE,
                    ),
                ),
            ),
        ),
        tableaux=(Tableau("Base", ("Source", "Part"), (("ANADER", "88,2 %"),)),),
        manifeste=Manifeste(
            modele="opencacao-8b",
            version_modele="1.1.0",
            version_app="0.6.91",
            profil_materiel="gpu",
            genere_le=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
            empreinte_demandeur="abc",
        ),
        mention=mention,
        graphiques=(
            Graphique(
                titre="Répartition par source",
                type=TypeGraphique.SECTEURS,
                categories=("ANADER", "CNRA"),
                valeurs=(3.0, 1.0),
            ),
        ),
    )


def _paquet() -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(rendu_pptx(_document())))


def _tout_le_xml(paquet: zipfile.ZipFile) -> str:
    return " ".join(
        paquet.read(n).decode("utf-8", "replace") for n in paquet.namelist() if n.endswith(".xml")
    )


def test_le_deck_est_en_16_9() -> None:
    """Le 4:3 laisse deux bandes noires sur tout vidéoprojecteur d'aujourd'hui."""
    presentation = _paquet().read("ppt/presentation.xml").decode("utf-8")
    cx, cy = re.search(r'sldSz cx="(\d+)" cy="(\d+)"', presentation).groups()
    assert abs(int(cx) / int(cy) - 16 / 9) < 0.01


def test_la_couverture_porte_la_couleur_du_projet() -> None:
    """Sans elle, la première diapositive est une page blanche de traitement de texte."""
    diapo = _paquet().read("ppt/slides/slide1.xml").decode("utf-8")
    assert ORANGE in diapo.upper()


def test_chaque_diapositive_de_section_porte_un_bandeau() -> None:
    """Le bandeau de titre est ce qui fait lire un deck comme une suite structurée."""
    paquet = _paquet()
    diapos = [n for n in paquet.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)]
    coloriees = [n for n in diapos if ORANGE in paquet.read(n).decode("utf-8", "replace").upper()]
    assert len(coloriees) >= len(diapos) - 1, "presque toutes les diapos doivent être habillées"


def test_le_theme_n_est_plus_celui_d_office_2007() -> None:
    """4F81BD est le bleu par défaut de python-pptx : le trouver, c'est n'avoir rien fait."""
    theme = _paquet().read("ppt/theme/theme1.xml").decode("utf-8").upper()
    assert "4F81BD" not in theme
    assert ORANGE in theme


def test_la_police_du_theme_n_est_plus_calibri() -> None:
    theme = _paquet().read("ppt/theme/theme1.xml").decode("utf-8")
    polices = re.findall(r'<a:latin typeface="([^"]*)"', theme)
    assert polices and "Calibri" not in polices


def test_la_mention_reglementaire_est_sur_la_premiere_diapositive() -> None:
    """D5 : elle ne doit pas être reléguée en fin de deck."""
    doc = _document(mention="Document préparatoire, sans valeur d'attestation.")
    paquet = zipfile.ZipFile(io.BytesIO(rendu_pptx(doc)))
    diapo = paquet.read("ppt/slides/slide1.xml").decode("utf-8")
    assert "Document préparatoire" in diapo


def test_le_deck_reste_valide_et_complet() -> None:
    """L'habillage ne doit rien perdre : titre, section, figure, tableau, manifeste."""
    paquet = _paquet()
    textes = "".join(re.findall(r"<a:t>([^<]*)</a:t>", _tout_le_xml(paquet)))
    for attendu in ("Étude de filière", "Contexte de la filière", "Base", "Manifeste"):
        assert attendu in textes
    assert any("charts/chart" in n for n in paquet.namelist())
