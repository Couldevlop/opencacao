"""Rendu Markdown — affichage web et diffusion en direct.

Aucune dépendance. C'est aussi le format de référence des tests : ce qui manque ici
manquera partout ailleurs.
"""

from __future__ import annotations

import re

from app.application.provenance import tableau_de_provenance
from app.models.rapport import Document, Tableau
from app.services.rendu.ooxml import texte_xml_sur

# Une clôture de bloc de code émise par le modèle avalerait TOUT le reste du
# document — annexe de provenance et manifeste compris, c'est-à-dire précisément ce
# qui rend le livrable défendable devant un auditeur. On désamorce par un caractère
# invisible plutôt que de retirer le texte du modèle.
_CLOTURE = re.compile(r"(?m)^(\s{0,3})(`{3,}|~{3,})")


def _corps(texte: str) -> str:
    """Désamorce une clôture de bloc de code, qui avalerait la suite du document.

    Args:
        texte: Corps de section, issu du modèle.

    Returns:
        Le texte, ses clôtures rendues inoffensives.
    """
    return _CLOTURE.sub(lambda trouve: f"{trouve.group(1)}​{trouve.group(2)}", texte)


def _cellule(valeur: str) -> str:
    """Neutralise ce qui disloquerait un tableau Markdown.

    Le contenu peut venir du modèle ou d'une source externe : un ``|`` ou un saut de
    ligne dans une cellule casserait la table.

    Args:
        valeur: Contenu brut de la cellule.

    Returns:
        Le contenu rendu inoffensif pour une table Markdown.
    """
    return valeur.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _tableau(tableau: Tableau) -> list[str]:
    """Rend un tableau en Markdown.

    Args:
        tableau: Tableau à rendre.

    Returns:
        Les lignes Markdown, titre compris.
    """
    lignes = [f"**{tableau.titre}**", ""]
    lignes.append("| " + " | ".join(_cellule(entete) for entete in tableau.entetes) + " |")
    lignes.append("| " + " | ".join("---" for _ in tableau.entetes) + " |")
    lignes.extend(
        "| " + " | ".join(_cellule(valeur) for valeur in ligne) + " |" for ligne in tableau.lignes
    )
    lignes.append("")
    return lignes


def rendu_markdown(document: Document) -> str:
    """Rend le document en Markdown.

    Args:
        document: Document assemblé par le moteur.

    Returns:
        Le document complet, mention et manifeste compris.
    """
    lignes = [f"# {texte_xml_sur(document.titre)}", ""]
    if document.sous_titre:
        lignes += [f"*{document.sous_titre}*", ""]
    if document.mention:
        # D5 : en tête, avant tout contenu, et visuellement distincte.
        lignes += [f"> **{document.mention}**", ""]

    for section in document.sections:
        lignes += [f"## {section.titre}", ""]
        if section.lacune:
            lignes += ["*Section en lacune — aucune source mobilisable.*", ""]
        lignes += [_corps(texte_xml_sur(section.corps)), ""]

    for tableau in document.tableaux:
        lignes += _tableau(tableau)

    lignes += ["## Annexe — provenance", ""]
    lignes += _tableau(tableau_de_provenance(document))

    manifeste = document.manifeste
    lignes += [
        "## Annexe — manifeste de génération",
        "",
        f"- Modèle : {manifeste.modele} (version {manifeste.version_modele})",
        f"- Version applicative : {manifeste.version_app}",
        f"- Profil matériel : {manifeste.profil_materiel}",
        f"- Généré le : {manifeste.genere_le.isoformat()}",
        f"- Empreinte du demandeur : {manifeste.empreinte_demandeur}",
        f"- Sources mobilisées : {len(manifeste.documents_rag)}",
        "",
    ]
    return "\n".join(lignes)
