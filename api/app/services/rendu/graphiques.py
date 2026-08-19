"""Socle analytique commun à toutes les études — graphiques et tableaux dérivés.

**Un graphique est le rendu d'éléments déjà établis, jamais un fait nouveau.** C'est la
règle qui permet d'en placer systématiquement dans les documents sans trahir l'interdit
central du projet : ne jamais avancer un chiffre qu'on ne peut pas justifier. Rien ici
n'est demandé au modèle, rien n'est estimé — on compte ce que le moteur a déjà collecté,
et on le compte de façon déterministe.

Les deux premiers graphiques ne portent pas sur le cacao mais sur **le document
lui-même** : d'où viennent ses affirmations, et avec quelle confiance. C'est ce qu'un
lecteur institutionnel regarde avant le fond — la base probante avant les conclusions —
et c'est aussi la seule chose qu'on puisse chiffrer honnêtement tant que le corpus ne
fournit pas de séries. Quand des tableaux chiffrés arriveront (prix, alertes, météo),
ils emprunteront la même mécanique.

Une étude dont aucune section n'est sourcée ne reçoit **aucun** graphique : un cadre
vide qui fait savant est pire qu'une absence assumée.
"""

from __future__ import annotations

from collections import Counter

from app.models.constat import NiveauConfiance
from app.models.rapport import Graphique, Section, Tableau, TypeGraphique

# Ordre d'affichage imposé : un histogramme de confiance dont les barreaux changent de
# place d'un document à l'autre ne se compare pas d'une étude à la suivante.
_ORDRE_CONFIANCE: tuple[tuple[NiveauConfiance, str], ...] = (
    (NiveauConfiance.ELEVEE, "Élevée"),
    (NiveauConfiance.MOYENNE, "Moyenne"),
    (NiveauConfiance.FAIBLE, "Faible"),
)


def _affirmations(sections: tuple[Section, ...]) -> tuple:
    """Rassemble les affirmations de toutes les sections, dans l'ordre du document."""
    return tuple(a for section in sections for a in section.affirmations)


def repartition_par_source(sections: tuple[Section, ...]) -> Graphique | None:
    """Camembert de la répartition des affirmations par source.

    Args:
        sections: Sections rédigées du document.

    Returns:
        La figure, ou ``None`` si le document ne porte aucune affirmation sourcée.
    """
    affirmations = _affirmations(sections)
    if not affirmations:
        return None
    # Tri décroissant : un camembert dont les parts sautent au hasard se lit mal.
    comptes = Counter(a.source for a in affirmations).most_common()
    return Graphique(
        titre="Répartition des affirmations par source",
        type=TypeGraphique.SECTEURS,
        categories=tuple(source for source, _ in comptes),
        valeurs=tuple(float(nombre) for _, nombre in comptes),
        unite="affirmations",
        note=(
            f"Établi en comptant les {len(affirmations)} affirmations sourcées du "
            "document. Aucune estimation."
        ),
    )


def niveaux_de_confiance(sections: tuple[Section, ...]) -> Graphique | None:
    """Histogramme des niveaux de confiance déclarés.

    Args:
        sections: Sections rédigées du document.

    Returns:
        La figure, ou ``None`` si le document ne porte aucune affirmation sourcée.
    """
    affirmations = _affirmations(sections)
    if not affirmations:
        return None
    comptes = Counter(a.confiance for a in affirmations)
    return Graphique(
        titre="Niveaux de confiance des affirmations",
        type=TypeGraphique.BATONS,
        categories=tuple(libelle for _, libelle in _ORDRE_CONFIANCE),
        # Un niveau absent vaut zéro et RESTE affiché : voir qu'aucune affirmation n'est
        # de confiance faible est une information, l'omettre laisserait croire à un oubli.
        valeurs=tuple(float(comptes.get(niveau, 0)) for niveau, _ in _ORDRE_CONFIANCE),
        unite="affirmations",
        note="Niveaux déclarés par le moteur à la collecte, non réévalués à la rédaction.",
    )


def tableau_des_sources(sections: tuple[Section, ...]) -> Tableau | None:
    """Tableau reprenant le camembert en chiffres.

    Le graphique ne suffit pas : imprimé en noir et blanc, lu par une personne
    daltonienne ou par un lecteur d'écran, il ne dit plus rien. Le tableau porte la
    même information sous une forme qui survit à tout cela.

    Args:
        sections: Sections rédigées du document.

    Returns:
        Le tableau, ou ``None`` si le document ne porte aucune affirmation sourcée.
    """
    affirmations = _affirmations(sections)
    if not affirmations:
        return None
    total = len(affirmations)
    lignes = tuple(
        (source, str(nombre), f"{nombre * 100 / total:.1f} %".replace(".", ","))
        for source, nombre in Counter(a.source for a in affirmations).most_common()
    )
    return Tableau(
        titre="Base documentaire mobilisée",
        entetes=("Source", "Affirmations", "Part"),
        lignes=lignes,
    )


def socle_analytique(sections: tuple[Section, ...]) -> tuple[Graphique, ...]:
    """Graphiques présents dans TOUTE étude, dans un ordre fixe.

    Systématique, jamais optionnel : une étude se juge d'abord sur sa base probante,
    et celle-ci doit se voir sans qu'on ait à la demander.

    Args:
        sections: Sections rédigées du document.

    Returns:
        Les figures du socle, ou un tuple vide si rien n'est sourcé.
    """
    figures = (repartition_par_source(sections), niveaux_de_confiance(sections))
    return tuple(figure for figure in figures if figure is not None)
