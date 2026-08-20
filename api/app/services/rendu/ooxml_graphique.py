"""Construction d'une partie graphique DrawingML — le format que Word attend.

**Pourquoi ce module existe.** ``python-pptx`` sait poser un graphique natif ;
``python-docx`` ne le sait pas, et la spec §2.1 fige les dépendances — on n'ajoute pas
une bibliothèque de tracé pour autant. On écrit donc le XML nous-mêmes.

**Pourquoi pas une image.** Coller un PNG serait plus court et strictement pire : une
image ne se re-style pas, s'écrase à l'impression, ne se lit pas au lecteur d'écran, et
ses chiffres deviennent inaccessibles à qui voudrait les vérifier. Un graphique natif
reste vectoriel, éditable, et porte ses valeurs en clair dans le fichier — ce qui compte
quand le document doit être défendable devant une commission.

``c:chartSpace`` est **le même dialecte** dans Word, PowerPoint et Excel : seul
l'emballage diffère. Ce module produit donc la partie ; c'est l'adaptateur de format qui
l'accroche à son document.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from app.models.rapport import Graphique, TypeGraphique
from app.services.rendu import charte
from app.services.rendu.ooxml import texte_xml_sur

TYPE_CONTENU = "application/vnd.openxmlformats-officedocument.drawingml.chart+xml"

_NS_C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
_NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _texte(valeur: str) -> str:
    """Purge les caractères interdits par XML 1.0, puis échappe le reste.

    Les libellés viennent du corpus et des outils : ce qui casse les autres formats
    casse aussi celui-ci, et un caractère de contrôle isolé rendrait le document
    inouvrable — l'erreur remontant en « format inconnu » (cf. ``ooxml.py``).
    """
    return escape(texte_xml_sur(valeur))


def _pt(valeur: float) -> str:
    """Formate une valeur numérique sans notation scientifique ni zéro parasite."""
    return f"{valeur:g}"


def _references(graphique: Graphique) -> str:
    """Catégories et valeurs, en cache littéral (aucun classeur externe requis).

    Word affiche un graphique à partir de ces caches seuls. On s'en tient là : embarquer
    un classeur Excel n'ajouterait que la possibilité d'éditer les données depuis Word,
    au prix d'une pièce jointe binaire dans un document destiné à être lu et archivé.
    """
    categories = "".join(
        f'<c:pt idx="{rang}"><c:v>{_texte(libelle)}</c:v></c:pt>'
        for rang, libelle in enumerate(graphique.categories)
    )
    valeurs = "".join(
        f'<c:pt idx="{rang}"><c:v>{_pt(valeur)}</c:v></c:pt>'
        for rang, valeur in enumerate(graphique.valeurs)
    )
    return (
        "<c:cat><c:strRef><c:f>Catégories</c:f><c:strCache>"
        f'<c:ptCount val="{len(graphique.categories)}"/>{categories}'
        "</c:strCache></c:strRef></c:cat>"
        "<c:val><c:numRef><c:f>Valeurs</c:f><c:numCache>"
        f'<c:formatCode>General</c:formatCode><c:ptCount val="{len(graphique.valeurs)}"/>{valeurs}'
        "</c:numCache></c:numRef></c:val>"
    )


def _points_colores(nombre: int) -> str:
    """Une couleur par part — sinon Word rend un camembert monochrome illisible."""
    return "".join(
        f'<c:dPt><c:idx val="{rang}"/><c:bubble3D val="0"/><c:spPr>'
        f'<a:solidFill><a:srgbClr val="{charte.couleur_serie(rang)}"/></a:solidFill>'
        "</c:spPr></c:dPt>"
        for rang in range(nombre)
    )


def _serie(graphique: Graphique) -> str:
    """La série unique du graphique, nommée par son unité."""
    nom = _texte(graphique.unite or graphique.titre)
    points = (
        _points_colores(len(graphique.categories))
        if graphique.type is TypeGraphique.SECTEURS
        else (
            f'<c:spPr><a:solidFill><a:srgbClr val="{charte.couleur_serie(0)}"/>'
            "</a:solidFill></c:spPr>"
        )
    )
    return (
        '<c:ser><c:idx val="0"/><c:order val="0"/>'
        f"<c:tx><c:strRef><c:f>Série</c:f><c:strCache>"
        f'<c:ptCount val="1"/><c:pt idx="0"><c:v>{nom}</c:v></c:pt>'
        "</c:strCache></c:strRef></c:tx>"
        f"{points}{_references(graphique)}</c:ser>"
    )


def _corps(graphique: Graphique) -> str:
    """Le tracé proprement dit, selon la forme demandée."""
    serie = _serie(graphique)
    if graphique.type is TypeGraphique.SECTEURS:
        # Étiquettes en pourcentage : c'est ce qu'on lit sur un camembert, pas l'effectif.
        return (
            '<c:pieChart><c:varyColors val="1"/>'
            f"{serie}"
            '<c:dLbls><c:showLegendKey val="0"/><c:showVal val="0"/>'
            '<c:showCatName val="0"/><c:showSerName val="0"/><c:showPercent val="1"/>'
            '<c:showBubbleSize val="0"/></c:dLbls>'
            '<c:firstSliceAng val="0"/></c:pieChart>'
        )
    if graphique.type is TypeGraphique.LIGNES:
        return (
            '<c:lineChart><c:grouping val="standard"/><c:varyColors val="0"/>'
            f'{serie}<c:marker val="1"/>'
            '<c:axId val="111111111"/><c:axId val="222222222"/></c:lineChart>'
            f"{_axes()}"
        )
    return (
        '<c:barChart><c:barDir val="col"/><c:grouping val="clustered"/>'
        f'<c:varyColors val="0"/>{serie}'
        '<c:gapWidth val="80"/>'
        '<c:axId val="111111111"/><c:axId val="222222222"/></c:barChart>'
        f"{_axes()}"
    )


def _axes() -> str:
    """Axes des formes cartésiennes (le camembert n'en a pas)."""
    return (
        '<c:catAx><c:axId val="111111111"/><c:scaling><c:orientation val="minMax"/>'
        '</c:scaling><c:delete val="0"/><c:axPos val="b"/>'
        '<c:crossAx val="222222222"/></c:catAx>'
        '<c:valAx><c:axId val="222222222"/><c:scaling><c:orientation val="minMax"/>'
        '</c:scaling><c:delete val="0"/><c:axPos val="l"/>'
        '<c:majorGridlines/><c:crossAx val="111111111"/></c:valAx>'
    )


def partie_graphique(graphique: Graphique) -> bytes:
    """Rend la partie ``chart`` d'un graphique, prête à être ajoutée à un paquet OOXML.

    Args:
        graphique: La figure à tracer.

    Returns:
        Les octets XML de la partie ``c:chartSpace``.
    """
    legende = (
        '<c:legend><c:legendPos val="r"/><c:overlay val="0"/></c:legend>'
        if graphique.type is TypeGraphique.SECTEURS
        else ""
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<c:chartSpace xmlns:c="{_NS_C}" xmlns:a="{_NS_A}" xmlns:r="{_NS_R}">'
        "<c:chart>"
        "<c:title><c:tx><c:rich>"
        "<a:bodyPr/><a:lstStyle/>"
        f'<a:p><a:r><a:rPr lang="fr-FR" b="1" sz="1200"/>'
        f"<a:t>{_texte(graphique.titre)}</a:t></a:r></a:p>"
        '</c:rich></c:tx><c:overlay val="0"/></c:title>'
        f'<c:autoTitleDeleted val="0"/><c:plotArea><c:layout/>{_corps(graphique)}</c:plotArea>'
        f'{legende}<c:plotVisOnly val="1"/><c:dispBlanksAs val="gap"/>'
        "</c:chart></c:chartSpace>"
    )
    return xml.encode("utf-8")
