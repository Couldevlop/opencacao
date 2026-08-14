"""Provenance des livrables — au centre, pas en annexe (spec §8.4).

Deux services rendus au reste du chantier :

* **Construire le manifeste** qui rend un document rejouable.
* **Vérifier** qu'aucune affirmation n'a échappé au sourçage. Ce n'est pas une
  politesse : c'est un critère d'acceptation, et un test échoue si une affirmation
  sort sans source.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from app.models.rapport import Affirmation, Document, Manifeste, Tableau

ENTETES_PROVENANCE = ("Section", "Affirmation", "Source", "Date", "Méthode", "Confiance")

# Longueur de l'empreinte du demandeur : assez pour corréler deux documents d'un même
# producteur, trop courte pour servir de jeton si elle était devinée.
_LONGUEUR_EMPREINTE = 12

# Mentions qui ressemblent à une source sans en être une. ``strip()`` seul ne les
# attrape pas : elles produiraient une ligne de provenance vide de sens devant un
# auditeur, ce qui est précisément ce que la règle interdit.
_SOURCES_VIDES = frozenset({"", "-", "--", "n/a", "na", "nc", "inconnu", "source inconnue"})


def source_absente(source: str | None) -> bool:
    """Indique si une source est absente, blanche, ou vide de sens.

    Args:
        source: Valeur du champ ``source`` d'une affirmation.

    Returns:
        ``True`` si elle ne désigne aucune source vérifiable.
    """
    lisible = "".join(
        caractere
        for caractere in (source or "")
        if caractere.isprintable() and not caractere.isspace()
    )
    return lisible.casefold() in _SOURCES_VIDES


def empreinte_demandeur(demandeur: str) -> str:
    """Réduit l'identifiant du demandeur à une empreinte non rejouable.

    ``X-Device-Id`` est la **clé d'autorisation** des ressources d'un producteur, pas
    un pseudonyme. Un manifeste part dans un fichier téléchargeable, potentiellement
    remis à un bailleur ou à un auditeur : ce qu'on n'écrit pas ne peut pas fuir.

    Args:
        demandeur: Identifiant d'appareil ou de compte.

    Returns:
        Une empreinte courte et stable, ou une chaîne vide si aucun demandeur.
    """
    if not demandeur:
        return ""
    return hashlib.sha256(demandeur.encode("utf-8")).hexdigest()[:_LONGUEUR_EMPREINTE]


def construire_manifeste(
    modele: str,
    version_modele: str,
    version_app: str,
    profil_materiel: str,
    demandeur: str,
    documents_rag: tuple[tuple[str, str], ...] = (),
    outils: tuple[tuple[str, str], ...] = (),
) -> Manifeste:
    """Assemble le manifeste de génération d'un document.

    Args:
        modele: Nom du modèle de langage.
        version_modele: Version du modèle.
        version_app: Version applicative.
        profil_materiel: « cpu » ou « gpu ».
        demandeur: Identifiant d'appareil ou de compte. **Il n'est pas stocké tel
            quel** : seule son empreinte entre dans le manifeste.
        documents_rag: Couples ``(source, empreinte)`` des passages mobilisés.
        outils: Couples ``(nom, horodatage ISO)`` des outils appelés.

    Returns:
        Le manifeste, horodaté en UTC au moment de l'appel.
    """
    return Manifeste(
        modele=modele,
        version_modele=version_modele,
        version_app=version_app,
        profil_materiel=profil_materiel,
        genere_le=datetime.now(UTC),
        empreinte_demandeur=empreinte_demandeur(demandeur),
        documents_rag=documents_rag,
        outils=outils,
    )


def affirmations_sans_source(document: Document) -> tuple[Affirmation, ...]:
    """Retourne les affirmations dépourvues de source.

    Un blanc ne compte pas comme une source : ce serait un contournement trivial de
    la règle, et une ligne de tableau de provenance vide devant un auditeur.

    Args:
        document: Document à vérifier.

    Returns:
        Les affirmations fautives, vide si le document est sain.
    """
    return tuple(
        affirmation
        for section in document.sections
        for affirmation in section.affirmations
        if source_absente(affirmation.source)
    )


def tableau_de_provenance(document: Document) -> Tableau:
    """Construit le tableau « d'où vient chaque chiffre ».

    Figure en annexe de tout livrable, et en feuille dédiée dans l'export Excel.

    Args:
        document: Document assemblé.

    Returns:
        Le tableau, dont les lignes suivent l'ordre des sections.
    """
    lignes = tuple(
        (
            section.titre,
            affirmation.texte,
            affirmation.source,
            affirmation.date,
            affirmation.methode,
            affirmation.confiance.value,
        )
        for section in document.sections
        for affirmation in section.affirmations
    )
    return Tableau(titre="Provenance des affirmations", entetes=ENTETES_PROVENANCE, lignes=lignes)
