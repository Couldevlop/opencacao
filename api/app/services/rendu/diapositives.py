"""Rendu PowerPoint — restitution institutionnelle.

**C'est le livrable du moment de scène** (spec §8.7) : faire générer en direct, devant
l'assemblée, la présentation qu'elle est en train de regarder. Il doit donc s'ouvrir
sans réparation et se lire de loin — d'où le corps de texte volontairement grand et le
découpage strict d'une section par diapositive.

Comme pour Word, les caractères de contrôle sont purgés : ``python-pptx`` n'échappe
rien, et un fichier illisible se découvre au pire moment.
"""

from __future__ import annotations

import io

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.util import Inches, Pt

from app.models.rapport import Document, Graphique, Tableau, TypeGraphique
from app.services.rendu.ooxml import texte_xml_sur

# Auteur inscrit dans les métadonnées du fichier livré.
_AUTEUR = "OpenCacao — OpenLab Consulting"

# Dispositions du thème par défaut de python-pptx.
_DISPOSITION_TITRE = 0
_DISPOSITION_TITRE_CONTENU = 1
# Disposition « titre seul » : une figure ou un tableau occupe la place du texte.
_DISPOSITION_TITRE_SEUL = 5

# Une diapositive lue de loin ne tient pas les 800 caractères d'une section : on
# tronque proprement plutôt que de laisser le texte déborder hors du cadre.
CORPS_MAX = 600

# Corps de texte lisible depuis le fond d'une salle.
_TAILLE_CORPS = 16


def _propre(texte: str) -> str:
    """Retire ce que XML 1.0 refuse (contrôles, surrogates isolés, non-caractères)."""
    return texte_xml_sur(texte)


def _tronquer(texte: str) -> str:
    """Tronque un corps de section à la longueur lisible sur écran.

    Args:
        texte: Corps de la section.

    Returns:
        Le texte, suivi d'une ellipse s'il a été coupé.
    """
    propre = _propre(texte)
    if len(propre) <= CORPS_MAX:
        return propre
    return propre[: CORPS_MAX - 1].rstrip() + "…"


# Une figure occupe la diapositive sous son titre, marges comprises.
_FIGURE = (Inches(0.9), Inches(1.6), Inches(7.6), Inches(4.6))

_FORMES = {
    TypeGraphique.SECTEURS: XL_CHART_TYPE.PIE,
    TypeGraphique.BATONS: XL_CHART_TYPE.COLUMN_CLUSTERED,
    TypeGraphique.LIGNES: XL_CHART_TYPE.LINE_MARKERS,
}


def _diapositive_graphique(presentation, graphique: Graphique) -> None:
    """Ajoute une diapositive portant une figure NATIVE (pas une image).

    Args:
        presentation: Présentation en construction.
        graphique: Figure à tracer.
    """
    diapositive = presentation.slides.add_slide(presentation.slide_layouts[_DISPOSITION_TITRE_SEUL])
    diapositive.shapes.title.text = _propre(graphique.titre)
    donnees = CategoryChartData()
    donnees.categories = [_propre(c) for c in graphique.categories]
    donnees.add_series(_propre(graphique.unite or graphique.titre), graphique.valeurs)
    cadre = diapositive.shapes.add_chart(_FORMES[graphique.type], *_FIGURE, donnees)
    graphe = cadre.chart
    if graphique.type is TypeGraphique.SECTEURS:
        graphe.has_legend = True
        graphe.legend.position = XL_LEGEND_POSITION.RIGHT
        graphe.legend.include_in_layout = False
    else:
        graphe.has_legend = False


def _diapositive_tableau(presentation, tableau: Tableau) -> None:
    """Ajoute une diapositive portant un tableau.

    Le deck n'en rendait aucun : une étude défilait à l'écran sans jamais montrer un
    chiffre, alors que le Word les portait.

    Args:
        presentation: Présentation en construction.
        tableau: Tableau à rendre.
    """
    diapositive = presentation.slides.add_slide(presentation.slide_layouts[_DISPOSITION_TITRE_SEUL])
    diapositive.shapes.title.text = _propre(tableau.titre)
    lignes, colonnes = len(tableau.lignes) + 1, len(tableau.entetes)
    forme = diapositive.shapes.add_table(lignes, colonnes, *_FIGURE)
    grille = forme.table
    for rang, entete in enumerate(tableau.entetes):
        grille.cell(0, rang).text = _propre(entete)
    for numero, ligne in enumerate(tableau.lignes, start=1):
        for rang, valeur in enumerate(ligne):
            grille.cell(numero, rang).text = _tronquer(_propre(valeur))


def rendu_pptx(document: Document) -> bytes:
    """Rend le document au format PowerPoint.

    Args:
        document: Document assemblé par le moteur.

    Returns:
        Les octets du fichier ``.pptx``.
    """
    presentation = Presentation()
    proprietes = presentation.core_properties
    proprietes.author = _AUTEUR
    # python-pptx laisse « Steve Canny » — le nom de son auteur — dans ce champ.
    proprietes.last_modified_by = _AUTEUR
    proprietes.title = _propre(document.titre)
    proprietes.created = document.manifeste.genere_le
    proprietes.modified = document.manifeste.genere_le

    ouverture = presentation.slides.add_slide(presentation.slide_layouts[_DISPOSITION_TITRE])
    ouverture.shapes.title.text = _propre(document.titre)
    sous_titre = document.sous_titre
    if document.mention:
        # D5 : la mention est sur la PREMIÈRE diapositive, pas reléguée en fin de deck.
        sous_titre = f"{sous_titre}\n{document.mention}" if sous_titre else document.mention
    # pragma: no branch — la disposition « titre » du gabarit python-pptx livré porte
    # toujours deux emplacements. Ce garde-fou ne sert que si le gabarit était remplacé
    # un jour ; l'exercer exigerait de simuler les entrailles de python-pptx, donc de
    # tester le simulacre plutôt que le code. Branche assumée, pas oubliée.
    if len(ouverture.placeholders) > 1:  # pragma: no branch
        ouverture.placeholders[1].text = _propre(sous_titre)

    for section in document.sections:
        diapositive = presentation.slides.add_slide(
            presentation.slide_layouts[_DISPOSITION_TITRE_CONTENU]
        )
        diapositive.shapes.title.text = _propre(section.titre)
        cadre = diapositive.placeholders[1].text_frame
        cadre.text = _tronquer(section.corps)
        cadre.word_wrap = True
        for paragraphe in cadre.paragraphs:
            for run in paragraphe.runs:
                run.font.size = Pt(_TAILLE_CORPS)

    for graphique in document.graphiques:
        _diapositive_graphique(presentation, graphique)

    for tableau in document.tableaux:
        _diapositive_tableau(presentation, tableau)

    manifeste = document.manifeste
    finale = presentation.slides.add_slide(presentation.slide_layouts[_DISPOSITION_TITRE_CONTENU])
    finale.shapes.title.text = "Manifeste de génération"
    finale.placeholders[1].text_frame.text = "\n".join(
        (
            f"Modèle : {manifeste.modele} (version {manifeste.version_modele})",
            f"Version applicative : {manifeste.version_app}",
            f"Profil matériel : {manifeste.profil_materiel}",
            f"Généré le : {manifeste.genere_le.isoformat()}",
            f"Empreinte du demandeur : {manifeste.empreinte_demandeur}",
            f"Sources mobilisées : {len(manifeste.documents_rag)}",
        )
    )

    tampon = io.BytesIO()
    presentation.save(tampon)
    return tampon.getvalue()
