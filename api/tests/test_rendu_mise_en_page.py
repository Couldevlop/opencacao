"""Mise en page du Word — ce qui distingue une étude d'une suite de paragraphes.

Constats de l'audit du 19/08 corrigés ici : horodatage machine en couverture, message
de lacune écrit deux fois, aucun pied de page ni numérotation, et une mise en forme
sans le moindre aplat de couleur.
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import UTC, datetime

from app.models.constat import NiveauConfiance
from app.models.rapport import Affirmation, Document, Manifeste, Section, Tableau
from app.services.rendu.word import rendu_word


def _document(mention: str = "", lacune: bool = False) -> Document:
    return Document(
        titre="Étude de filière — cacao",
        sous_titre="Sud-Ouest",
        sections=(
            Section(
                "Contexte",
                "Prose analytique." if not lacune else "Aucune source mobilisable.",
                (
                    Affirmation(
                        texte="a",
                        source="ANADER",
                        date="",
                        methode="rag",
                        confiance=NiveauConfiance.ELEVEE,
                    ),
                ),
                lacune=lacune,
            ),
        ),
        tableaux=(Tableau("Base", ("Source", "Part"), (("ANADER", "88,2 %"), ("CNRA", "11,8 %"))),),
        manifeste=Manifeste(
            modele="opencacao-8b",
            version_modele="1.1.0",
            version_app="0.6.91",
            profil_materiel="gpu",
            genere_le=datetime(2026, 8, 20, 9, 4, 29, 675716, tzinfo=UTC),
            empreinte_demandeur="abc",
        ),
        mention=mention,
    )


def _paquet(octets: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(octets))


def _document_xml(doc: Document) -> str:
    return _paquet(rendu_word(doc)).read("word/document.xml").decode("utf-8")


def _visible(xml: str) -> str:
    return "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", xml))


def test_la_date_de_couverture_est_lisible_par_un_humain() -> None:
    """« 2026-08-20T09:04:29.675716+00:00 » n'a rien à faire sur une page de garde."""
    visible = _visible(_document_xml(_document()))
    assert "Généré le 20 août 2026" in visible
    # La couverture ne porte AUCUN horodatage machine. L'annexe du manifeste, elle,
    # garde l'ISO (« Généré le : 2026-… ») : c'est ce qui rend le document rejouable.
    assert "Généré le 2026-" not in visible
    assert "675716" not in visible


def test_l_horodatage_precis_reste_au_manifeste() -> None:
    """Il sert la rejouabilité : on le déplace, on ne le supprime pas."""
    assert "2026-08-20T09:04:29" in _visible(_document_xml(_document()))


def test_la_lacune_n_est_annoncee_qu_une_fois() -> None:
    """Deux fois le même aveu, c'est bafouiller à l'endroit le plus délicat."""
    # Insensible à la casse : le doublon constaté mêlait « Section en lacune — aucune
    # source mobilisable. » et « Aucune source mobilisable n'a été trouvée… ».
    visible = _visible(_document_xml(_document(lacune=True))).lower()
    assert visible.count("aucune source mobilisable") == 1


def test_l_entete_de_tableau_est_sur_un_aplat_de_couleur() -> None:
    """Un tableau sans en-tête coloré se lit comme une grille de tableur."""
    xml = _document_xml(_document())
    assert "<w:shd" in xml, "aucun aplat de couleur dans le document"
    assert 'w:fill="EA5B13"' in xml, "l'orange du projet doit habiller l'en-tête"


def test_les_lignes_du_tableau_sont_alternees() -> None:
    """Le zébrage guide l'œil sur les tableaux longs."""
    xml = _document_xml(_document())
    assert xml.count("<w:shd") >= 3, "en-tête + au moins une ligne zébrée"


def test_la_mention_reglementaire_est_encadree() -> None:
    """D5 : la mention doit sauter aux yeux, pas se fondre dans le corps."""
    doc = _document(mention="Document préparatoire, sans valeur d'attestation.")
    xml = _document_xml(doc)
    assert "Document préparatoire" in _visible(xml)
    assert xml.count("<w:shd") >= 4, "la mention doit porter son propre aplat"


def test_le_document_a_un_pied_de_page_numerote() -> None:
    """Impossible de dire « page 4 » en réunion sans numérotation."""
    paquet = _paquet(rendu_word(_document()))
    pieds = [n for n in paquet.namelist() if "footer" in n]
    assert pieds, "aucun pied de page"
    contenu = paquet.read(pieds[0]).decode("utf-8")
    assert "PAGE" in contenu


def test_le_pied_de_page_rappelle_le_titre() -> None:
    """Une page photocopiée seule doit encore dire d'où elle vient."""
    paquet = _paquet(rendu_word(_document()))
    pieds = [n for n in paquet.namelist() if "footer" in n]
    assert "cacao" in paquet.read(pieds[0]).decode("utf-8")
