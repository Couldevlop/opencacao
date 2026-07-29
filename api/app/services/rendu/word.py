"""Rendu Word — dossier de parcelle et étude de filière.

Les conventions typographiques (tailles, couleurs, hiérarchie de titres) reprennent
``scripts/build_doc_agentique.py`` : le projet a déjà une identité de document écrite,
on ne lui en invente pas une seconde.

**Les caractères de contrôle sont purgés.** ``python-docx`` n'échappe rien : un octet
nul venant du modèle ou d'une source externe produirait un XML invalide, donc un
fichier que le destinataire ne peut pas ouvrir. C'est la garantie propre à ce format,
et elle ne remonte pas dans le moteur.
"""

from __future__ import annotations

import io
import re

from docx import Document as DocxDocument
from docx.shared import Pt, RGBColor

from app.application.provenance import tableau_de_provenance
from app.models.rapport import Document, Tableau

# Palette reprise de scripts/build_doc_agentique.py.
_ORANGE = RGBColor(0xEA, 0x5B, 0x13)
_SOMBRE = RGBColor(0x1F, 0x1F, 0x1F)
_GRIS = RGBColor(0x60, 0x60, 0x60)

# Caractères que la norme XML 1.0 interdit. Tabulation, saut de ligne et retour
# chariot sont légaux et conservés ; le reste casserait le document.
_CONTROLES = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _propre(texte: str) -> str:
    """Retire les caractères de contrôle interdits par XML.

    Args:
        texte: Texte brut, éventuellement issu du modèle.

    Returns:
        Le texte utilisable dans un document Word.
    """
    return _CONTROLES.sub("", texte)


def _paragraphe(
    docx,
    texte: str,
    *,
    taille: int = 11,
    gras: bool = False,
    couleur: RGBColor = _SOMBRE,
    italique: bool = False,
):
    """Ajoute un paragraphe à la mise en forme du projet.

    Args:
        docx: Document python-docx en cours de construction.
        texte: Contenu du paragraphe.
        taille: Corps de la police, en points.
        gras: Met le texte en gras.
        couleur: Couleur du texte.
        italique: Met le texte en italique.

    Returns:
        Le paragraphe ajouté.
    """
    paragraphe = docx.add_paragraph()
    run = paragraphe.add_run(_propre(texte))
    run.font.size = Pt(taille)
    run.font.bold = gras
    run.font.italic = italique
    run.font.color.rgb = couleur
    paragraphe.paragraph_format.space_after = Pt(6)
    return paragraphe


def _tableau_word(docx, tableau: Tableau) -> None:
    """Ajoute un tableau natif Word — et non une image ou du texte aligné.

    Args:
        docx: Document python-docx en cours de construction.
        tableau: Tableau à rendre.
    """
    _paragraphe(docx, tableau.titre, taille=12, gras=True, couleur=_ORANGE)
    table = docx.add_table(rows=1, cols=len(tableau.entetes))
    table.style = "Table Grid"
    for colonne, entete in enumerate(tableau.entetes):
        cellule = table.cell(0, colonne)
        cellule.text = _propre(entete)
        for paragraphe in cellule.paragraphs:
            for run in paragraphe.runs:
                run.font.bold = True
                run.font.size = Pt(10)
    for ligne in tableau.lignes:
        cellules = table.add_row().cells
        for colonne, valeur in enumerate(ligne):
            cellules[colonne].text = _propre(valeur)
            for paragraphe in cellules[colonne].paragraphs:
                for run in paragraphe.runs:
                    run.font.size = Pt(10)


def rendu_word(document: Document) -> bytes:
    """Rend le document au format Word.

    Args:
        document: Document assemblé par le moteur.

    Returns:
        Les octets du fichier ``.docx``.
    """
    docx = DocxDocument()
    _paragraphe(docx, document.titre, taille=20, gras=True, couleur=_ORANGE)
    if document.sous_titre:
        _paragraphe(docx, document.sous_titre, taille=12, couleur=_GRIS, italique=True)
    if document.mention:
        # D5 : en tête, avant tout contenu, et visuellement distincte.
        _paragraphe(docx, document.mention, taille=11, gras=True, couleur=_ORANGE)

    for section in document.sections:
        _paragraphe(docx, section.titre, taille=15, gras=True, couleur=_ORANGE)
        if section.lacune:
            _paragraphe(
                docx,
                "Section en lacune — aucune source mobilisable.",
                taille=10,
                couleur=_GRIS,
                italique=True,
            )
        _paragraphe(docx, section.corps)

    for tableau in document.tableaux:
        _tableau_word(docx, tableau)

    manifeste = document.manifeste
    _paragraphe(docx, "Annexe — manifeste de génération", taille=15, gras=True, couleur=_ORANGE)
    for ligne in (
        f"Modèle : {manifeste.modele} (version {manifeste.version_modele})",
        f"Version applicative : {manifeste.version_app}",
        f"Profil matériel : {manifeste.profil_materiel}",
        f"Généré le : {manifeste.genere_le.isoformat()}",
        f"Empreinte du demandeur : {manifeste.empreinte_demandeur}",
    ):
        _paragraphe(docx, ligne, taille=10, couleur=_GRIS)

    _tableau_word(docx, tableau_de_provenance(document))

    tampon = io.BytesIO()
    docx.save(tampon)
    return tampon.getvalue()
