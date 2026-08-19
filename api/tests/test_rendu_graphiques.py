"""Les figures doivent arriver DANS les fichiers, en OOXML natif.

Un graphique collé en image est mort : illisible à l'agrandissement, non re-stylable,
inexploitable par un lecteur d'écran. Ces tests vérifient donc la présence d'une
**partie graphique** dans le paquet, et que ses valeurs sont bien celles du document —
pas qu'une fonction a été appelée.
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import UTC, datetime

import pytest

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
from app.services.rendu.word import rendu_word


def _manifeste() -> Manifeste:
    return Manifeste(
        modele="opencacao-8b",
        version_modele="1.1.0",
        version_app="0.6.88",
        profil_materiel="gpu",
        genere_le=datetime(2026, 8, 19, 22, 0, tzinfo=UTC),
        empreinte_demandeur="abc",
    )


def _document(avec_figures: bool = True) -> Document:
    figures = (
        Graphique(
            titre="Répartition des affirmations par source",
            type=TypeGraphique.SECTEURS,
            categories=("ANADER", "CNRA"),
            valeurs=(3.0, 1.0),
            unite="affirmations",
            note="Comptage, aucune estimation.",
        ),
        Graphique(
            titre="Niveaux de confiance",
            type=TypeGraphique.BATONS,
            categories=("Élevée", "Moyenne", "Faible"),
            valeurs=(3.0, 1.0, 0.0),
        ),
    )
    return Document(
        titre="Étude de filière — cacao",
        sous_titre="Test",
        sections=(
            Section(
                "Contexte",
                "Prose analytique.",
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
        tableaux=(Tableau("Base documentaire", ("Source", "Part"), (("ANADER", "75,0 %"),)),),
        manifeste=_manifeste(),
        graphiques=figures if avec_figures else (),
    )


def _paquet(octets: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(octets))


def _parties_graphiques(paquet: zipfile.ZipFile) -> list[str]:
    return [n for n in paquet.namelist() if re.search(r"charts?/chart\d*\.xml$", n)]


# --- Word ---


def test_le_word_porte_des_parties_graphiques_natives() -> None:
    """Deux figures au document, deux parties graphiques dans le .docx."""
    paquet = _paquet(rendu_word(_document()))
    assert len(_parties_graphiques(paquet)) == 2


def test_le_graphique_word_contient_les_valeurs_du_document() -> None:
    """Les valeurs doivent être DANS le fichier, pas seulement passées à une fonction."""
    paquet = _paquet(rendu_word(_document()))
    xml = paquet.read(_parties_graphiques(paquet)[0]).decode("utf-8")
    assert "ANADER" in xml
    assert "<c:v>3" in xml


def test_le_camembert_et_l_histogramme_ont_des_formes_distinctes() -> None:
    """Un camembert rendu en barres trahirait la lecture attendue."""
    paquet = _paquet(rendu_word(_document()))
    parties = sorted(_parties_graphiques(paquet))
    xmls = [paquet.read(p).decode("utf-8") for p in parties]
    assert any("<c:pieChart>" in x for x in xmls)
    assert any("<c:barChart>" in x for x in xmls)


def test_le_word_declare_le_type_de_contenu_des_graphiques() -> None:
    """Sans l'override de type MIME, Word refuse d'ouvrir le fichier."""
    paquet = _paquet(rendu_word(_document()))
    types = paquet.read("[Content_Types].xml").decode("utf-8")
    assert "drawingml.chart+xml" in types


def test_un_document_sans_figure_reste_un_word_valide() -> None:
    """Le socle peut être vide (document en lacune) : aucun graphique, aucune casse."""
    paquet = _paquet(rendu_word(_document(avec_figures=False)))
    assert _parties_graphiques(paquet) == []
    assert "word/document.xml" in paquet.namelist()


# --- PowerPoint ---


def test_le_pptx_porte_des_parties_graphiques_natives() -> None:
    """Le deck doit montrer les figures, pas les décrire."""
    paquet = _paquet(rendu_pptx(_document()))
    assert len(_parties_graphiques(paquet)) == 2


def test_le_pptx_contient_les_categories_du_document() -> None:
    paquet = _paquet(rendu_pptx(_document()))
    xmls = [paquet.read(p).decode("utf-8") for p in _parties_graphiques(paquet)]
    assert any("ANADER" in x for x in xmls)


def test_le_pptx_rend_aussi_les_tableaux() -> None:
    """Ils étaient purement absents du deck : une étude sans chiffres à l'écran."""
    paquet = _paquet(rendu_pptx(_document()))
    diapos = [n for n in paquet.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)]
    assert any("<a:tbl>" in paquet.read(d).decode("utf-8") for d in diapos)


def test_un_document_sans_figure_reste_un_pptx_valide() -> None:
    paquet = _paquet(rendu_pptx(_document(avec_figures=False)))
    assert _parties_graphiques(paquet) == []


@pytest.mark.parametrize("rendu", [rendu_word, rendu_pptx])
def test_le_titre_de_chaque_figure_apparait_dans_le_document(rendu) -> None:
    """Une figure sans légende n'est pas défendable devant une commission."""
    contenu = rendu(_document())
    paquet = _paquet(contenu)
    tout = " ".join(
        paquet.read(n).decode("utf-8", "replace") for n in paquet.namelist() if n.endswith(".xml")
    )
    assert "Répartition des affirmations par source" in tout


def test_un_nom_de_source_empoisonne_ne_casse_pas_le_fichier() -> None:
    """Les noms de source viennent du corpus : un caractère interdit par XML 1.0 y suffit.

    Sans purge, le fichier devient illisible par Word — et l'erreur remonterait en
    « format inconnu », désignant le mauvais coupable (cf. services/rendu/ooxml.py).
    """
    document = _document()
    empoisonne = Graphique(
        titre="Sources\x00 mobilisées",
        type=TypeGraphique.SECTEURS,
        categories=("ANA\x08DER", "CNRA"),
        valeurs=(2.0, 1.0),
    )
    octets = rendu_word(
        Document(
            titre=document.titre,
            sous_titre=document.sous_titre,
            sections=document.sections,
            tableaux=(),
            manifeste=document.manifeste,
            graphiques=(empoisonne,),
        )
    )
    paquet = _paquet(octets)
    xml = paquet.read(_parties_graphiques(paquet)[0]).decode("utf-8")
    assert "\x00" not in xml
    assert "ANADER" in xml


def test_la_courbe_est_disponible_pour_les_series_temporelles() -> None:
    """Prévu pour les séries chiffrées à venir (prix de campagne, alertes mensuelles).

    Gardé plutôt que supprimé : c'est la forme qu'appelle une évolution dans le temps,
    et l'ajouter au moment où les données arriveront exigerait de rouvrir le format.
    """
    courbe = Graphique(
        titre="Évolution du prix bord-champ",
        type=TypeGraphique.LIGNES,
        categories=("2023", "2024", "2025"),
        valeurs=(900.0, 1500.0, 1200.0),
        unite="FCFA/kg",
    )
    document = _document()
    octets = rendu_word(
        Document(
            titre=document.titre,
            sous_titre=document.sous_titre,
            sections=document.sections,
            tableaux=(),
            manifeste=document.manifeste,
            graphiques=(courbe,),
        )
    )
    paquet = _paquet(octets)
    xml = paquet.read(_parties_graphiques(paquet)[0]).decode("utf-8")
    assert "<c:lineChart>" in xml
    assert "1500" in xml
