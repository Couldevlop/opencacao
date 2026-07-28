"""Étage 0 de la cascade de vision — recevabilité d'une image de plantation.

C'est le composant le moins spectaculaire et le plus rentable de la chaîne : sans lui,
tout l'aval analyse du bruit. Une image refusée renvoie un **conseil de reprise en
français simple**, jamais un code d'erreur.

Répartition des responsabilités (voir le plan C1, « écart assumé ») :

* Le **navigateur** calcule la netteté (variance du laplacien) et la luminance moyenne
  sur les pixels qu'il possède déjà, et refuse localement avant tout téléversement —
  ce qui économise la bande passante sur un réseau mobile faible.
* Le **serveur** valide le format et les dimensions en lisant les **en-têtes** de
  fichier, en Python pur. Ce n'est pas une redondance : on écrit ces fichiers sur
  disque, et rien ne garantit qu'un client envoie bien une image.

Les métriques déclarées par le client sont bornées, jamais crues sur parole ; les
dimensions lues dans l'en-tête **priment** sur celles annoncées.
"""

from __future__ import annotations

import struct

from app.core.logging import get_logger
from app.models.parcelle import ImageRequest, MotifRecevabilite, Recevabilite

logger = get_logger(__name__)

# Variance du laplacien en dessous de laquelle une photo de cabosse est trop floue
# pour un constat. Valeur empirique sur images de téléphone en 1024 px de large.
SEUIL_NETTETE = 60.0

# Luminance moyenne (0-255) hors de laquelle l'exposition compromet le constat.
# Le contre-jour est le défaut dominant en plantation : soleil zénithal, sous-bois.
LUMINANCE_MIN = 30.0
LUMINANCE_MAX = 225.0

# Côté minimal en pixels : en dessous, une lésion de cabosse n'est plus discernable.
COTE_MIN_PX = 320

# Plafond de taille par image, après décodage base64.
TAILLE_MAX_OCTETS = 3_000_000

_CONSEILS: dict[MotifRecevabilite, str] = {
    MotifRecevabilite.OK: "Image exploitable.",
    MotifRecevabilite.FLOU: (
        "La photo est floue. Approchez-vous de la cabosse, tenez le téléphone bien "
        "immobile, et refaites la photo."
    ),
    MotifRecevabilite.SOUS_EXPOSE: (
        "La photo est trop sombre. Sortez de l'ombre ou attendez un moment plus "
        "clair, puis refaites la photo."
    ),
    MotifRecevabilite.SUR_EXPOSE: (
        "La photo est éblouie. Tournez-vous dos au soleil pour éviter le contre-jour, "
        "puis refaites la photo."
    ),
    MotifRecevabilite.TROP_PETITE: (
        "L'image est trop petite pour être examinée. Utilisez l'appareil photo du "
        "téléphone plutôt qu'une capture d'écran."
    ),
    MotifRecevabilite.FORMAT_REFUSE: (
        "Ce fichier n'est pas une photo reconnue. Envoyez une image JPEG ou PNG."
    ),
}

_SIGNATURE_PNG = b"\x89PNG\r\n\x1a\n"

# Marqueurs JPEG « Start Of Frame » portant les dimensions. On saute SOF4 (0xC4,
# tables de Huffman) et les marqueurs de redémarrage, qui ne sont pas des SOF.
_MARQUEURS_SOF = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)


def _dimensions_png(donnees: bytes) -> tuple[int, int] | None:
    """Lit largeur et hauteur dans le bloc IHDR d'un PNG."""
    if len(donnees) < 24 or donnees[12:16] != b"IHDR":
        return None
    largeur, hauteur = struct.unpack(">II", donnees[16:24])
    return (largeur, hauteur) if largeur and hauteur else None


def _dimensions_jpeg(donnees: bytes) -> tuple[int, int] | None:
    """Parcourt les segments JPEG jusqu'au marqueur SOF portant les dimensions."""
    position = 2
    taille = len(donnees)
    while position + 3 < taille:
        if donnees[position] != 0xFF:
            position += 1
            continue
        marqueur = donnees[position + 1]
        if marqueur in _MARQUEURS_SOF:
            if position + 9 > taille:
                return None
            hauteur, largeur = struct.unpack(">HH", donnees[position + 5 : position + 9])
            return (largeur, hauteur) if largeur and hauteur else None
        longueur = struct.unpack(">H", donnees[position + 2 : position + 4])[0]
        if longueur < 2:
            return None
        position += 2 + longueur
    return None


def dimensions_depuis_entete(donnees: bytes) -> tuple[int, int] | None:
    """Extrait les dimensions d'une image depuis son en-tête, sans la décoder.

    Sert aussi de validation de format : un fichier dont on ne peut lire l'en-tête
    n'est pas une image PNG ou JPEG, et n'a rien à faire sur le disque.

    Args:
        donnees: Contenu binaire du fichier.

    Returns:
        ``(largeur, hauteur)`` en pixels, ou ``None`` si le format est inconnu.
    """
    if donnees.startswith(_SIGNATURE_PNG):
        return _dimensions_png(donnees)
    if donnees.startswith(b"\xff\xd8"):
        return _dimensions_jpeg(donnees)
    return None


def _verdict(motif: MotifRecevabilite, score_nettete: float) -> Recevabilite:
    """Assemble un verdict avec le conseil de reprise associé au motif."""
    return Recevabilite(
        recevable=motif is MotifRecevabilite.OK,
        motif=motif,
        conseil=_CONSEILS[motif],
        score_nettete=score_nettete,
    )


def evaluer(image: ImageRequest, donnees: bytes) -> Recevabilite:
    """Rend le verdict de recevabilité d'une image.

    Ordre de priorité : format, dimensions, netteté, exposition. Le format passe en
    premier parce qu'un fichier non reconnu ne doit jamais être écrit sur disque.

    Args:
        image: Métadonnées déclarées par le client (dimensions, métriques).
        donnees: Contenu binaire décodé de l'image.

    Returns:
        Le verdict, avec son motif et un conseil de reprise en français simple.
    """
    dimensions = dimensions_depuis_entete(donnees)
    if dimensions is None:
        logger.info("recevabilite_format_refuse", octets=len(donnees))
        return _verdict(MotifRecevabilite.FORMAT_REFUSE, image.score_nettete)

    largeur, hauteur = dimensions
    if min(largeur, hauteur) < COTE_MIN_PX:
        return _verdict(MotifRecevabilite.TROP_PETITE, image.score_nettete)
    if image.score_nettete < SEUIL_NETTETE:
        return _verdict(MotifRecevabilite.FLOU, image.score_nettete)
    if image.luminance_moyenne < LUMINANCE_MIN:
        return _verdict(MotifRecevabilite.SOUS_EXPOSE, image.score_nettete)
    if image.luminance_moyenne > LUMINANCE_MAX:
        return _verdict(MotifRecevabilite.SUR_EXPOSE, image.score_nettete)
    return _verdict(MotifRecevabilite.OK, image.score_nettete)
