"""Rendu Excel — annexes de données et tableau de provenance.

La provenance a sa **feuille dédiée** (spec §8.4) : c'est le format dans lequel un
auditeur voudra la trier et la filtrer.

**Les formules sont neutralisées.** ``openpyxl`` n'échappe rien : une cellule dont la
valeur commence par ``=``, ``+``, ``-`` ou ``@`` est évaluée comme une formule à
l'ouverture du fichier (CWE-1236, « CSV injection »). Le contenu vient du modèle et de
sources externes, et le livrable est transmis à un bailleur ou à un auditeur : il ne
doit rien exécuter chez lui. La garantie vit **ici** et non dans le domaine — préfixer
par une apostrophe est correct pour un tableur et corromprait le Markdown, le Word et
le PowerPoint.
"""

from __future__ import annotations

import io
import re

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.worksheet.worksheet import Worksheet

from app.application.provenance import tableau_de_provenance
from app.models.rapport import Document, Tableau

# Excel refuse un nom de feuille de plus de 31 caractères, et les caractères \/*?:[]
_LONGUEUR_MAX_FEUILLE = 31
_INTERDITS_FEUILLE = str.maketrans({caractere: "-" for caractere in "\\/*?:[]"})

# Amorces qui font interpréter une cellule comme une formule. La tabulation et le
# retour chariot comptent aussi : certains tableurs les ignorent avant d'évaluer.
_AMORCES_FORMULE = ("=", "+", "-", "@", "\t", "\r")

# Caractères que le format OOXML interdit ; openpyxl lève IllegalCharacterError.
_CONTROLES = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _valeur(brut: str) -> str:
    """Rend une valeur inoffensive pour un tableur.

    Args:
        brut: Contenu de la cellule, éventuellement issu du modèle.

    Returns:
        La valeur purgée de ses caractères de contrôle et, si elle ressemblait à une
        formule, précédée d'une apostrophe qui force son interprétation en texte.
    """
    propre = _CONTROLES.sub("", str(brut))
    if propre.startswith(_AMORCES_FORMULE):
        return f"'{propre}"
    return propre


def _nom_de_feuille(titre: str, defaut: str, pris: set[str]) -> str:
    """Rend un titre utilisable et unique comme nom de feuille Excel.

    Args:
        titre: Titre du tableau.
        defaut: Nom de repli si le titre ne donne rien d'utilisable.
        pris: Noms déjà attribués, pour éviter qu'openpyxl ne renomme en silence.

    Returns:
        Un nom de feuille valide, unique, d'au plus 31 caractères.
    """
    propre = (titre or defaut).translate(_INTERDITS_FEUILLE).strip()
    nom = (propre or defaut)[:_LONGUEUR_MAX_FEUILLE]
    if nom not in pris:
        return nom
    for suffixe in range(2, 100):
        candidat = f"{nom[: _LONGUEUR_MAX_FEUILLE - len(str(suffixe)) - 1]} {suffixe}"
        if candidat not in pris:
            return candidat
    return defaut[:_LONGUEUR_MAX_FEUILLE]


def _ecrire_tableau(feuille: Worksheet, tableau: Tableau) -> None:
    """Écrit un tableau (en-têtes en gras) dans une feuille.

    Args:
        feuille: Feuille de destination.
        tableau: Tableau à écrire.
    """
    feuille.append([_valeur(entete) for entete in tableau.entetes])
    for cellule in feuille[1]:
        cellule.font = Font(bold=True)
    for ligne in tableau.lignes:
        feuille.append([_valeur(valeur) for valeur in ligne])
    # Toute cellule reste du TEXTE : sans cela, une valeur neutralisée pourrait être
    # réinterprétée à la lecture.
    for ligne_cellules in feuille.iter_rows():
        for cellule in ligne_cellules:
            if cellule.value is not None:
                cellule.data_type = "s"


def rendu_excel(document: Document) -> bytes:
    """Rend le document au format Excel.

    Args:
        document: Document assemblé par le moteur.

    Returns:
        Les octets du fichier ``.xlsx``.
    """
    classeur = Workbook()
    classeur.remove(classeur.active)
    pris: set[str] = set()

    for index, tableau in enumerate(document.tableaux, start=1):
        nom = _nom_de_feuille(tableau.titre, f"Tableau {index}", pris)
        pris.add(nom)
        _ecrire_tableau(classeur.create_sheet(nom), tableau)

    pris.add("Provenance")
    _ecrire_tableau(classeur.create_sheet("Provenance"), tableau_de_provenance(document))

    manifeste = document.manifeste
    feuille = classeur.create_sheet("Manifeste")
    for cle, valeur in (
        ("Modèle", manifeste.modele),
        ("Version du modèle", manifeste.version_modele),
        ("Version applicative", manifeste.version_app),
        ("Profil matériel", manifeste.profil_materiel),
        ("Généré le", manifeste.genere_le.isoformat()),
        ("Empreinte du demandeur", manifeste.empreinte_demandeur),
    ):
        feuille.append([cle, _valeur(valeur)])
    for ligne in feuille.iter_rows(min_col=1, max_col=1):
        ligne[0].font = Font(bold=True)

    tampon = io.BytesIO()
    classeur.save(tampon)
    return tampon.getvalue()
