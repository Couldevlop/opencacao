"""Rendu Markdown — affichage web et diffusion en direct.

Aucune dépendance. C'est aussi le format de référence des tests : ce qui manque ici
manquera partout ailleurs.
"""

from __future__ import annotations

from app.application.provenance import tableau_de_provenance
from app.models.rapport import Document, Tableau


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
    lignes = [f"# {document.titre}", ""]
    if document.sous_titre:
        lignes += [f"*{document.sous_titre}*", ""]
    if document.mention:
        # D5 : en tête, avant tout contenu, et visuellement distincte.
        lignes += [f"> **{document.mention}**", ""]

    for section in document.sections:
        lignes += [f"## {section.titre}", ""]
        if section.lacune:
            lignes += ["*Section en lacune — aucune source mobilisable.*", ""]
        lignes += [section.corps, ""]

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
