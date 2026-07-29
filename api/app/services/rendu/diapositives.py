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
from pptx.util import Pt

from app.models.rapport import Document
from app.services.rendu.ooxml import texte_xml_sur

# Auteur inscrit dans les métadonnées du fichier livré.
_AUTEUR = "OpenCacao — OpenLab Consulting"

# Dispositions du thème par défaut de python-pptx.
_DISPOSITION_TITRE = 0
_DISPOSITION_TITRE_CONTENU = 1

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
    if len(ouverture.placeholders) > 1:
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
