"""Tests de l'étage 0 — recevabilité d'une image de plantation."""

from __future__ import annotations

import struct

from app.models.parcelle import ImageRequest, MotifRecevabilite
from app.services.vision.recevabilite import (
    COTE_MIN_PX,
    SEUIL_NETTETE,
    dimensions_depuis_entete,
    evaluer,
)


def _png(largeur: int, hauteur: int) -> bytes:
    """Fabrique un en-tête PNG minimal mais valide (signature + IHDR)."""
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", largeur, hauteur)
    return signature + ihdr + b"\x08\x02\x00\x00\x00" + b"\x00" * 4


def _jpeg(largeur: int, hauteur: int) -> bytes:
    """Fabrique un en-tête JPEG minimal mais valide (SOI + APP0 + SOF0)."""
    soi = b"\xff\xd8"
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
    sof0 = (
        b"\xff\xc0"
        + struct.pack(">H", 17)
        + b"\x08"
        + struct.pack(">HH", hauteur, largeur)
        + b"\x03"
        + b"\x00" * 9
    )
    return soi + app0 + sof0


def _image(**surcharges) -> ImageRequest:
    defauts = {
        "contenu_base64": "AAAA",
        "largeur": 1024,
        "hauteur": 768,
        "score_nettete": 400.0,
        "luminance_moyenne": 128.0,
    }
    return ImageRequest(**{**defauts, **surcharges})


def test_dimensions_lues_dans_un_entete_png():
    assert dimensions_depuis_entete(_png(1024, 768)) == (1024, 768)


def test_dimensions_lues_dans_un_entete_jpeg():
    assert dimensions_depuis_entete(_jpeg(1600, 1200)) == (1600, 1200)


def test_dimensions_none_si_le_format_est_inconnu():
    assert dimensions_depuis_entete(b"ceci n'est pas une image") is None


def test_image_nette_et_bien_exposee_est_recevable():
    verdict = evaluer(_image(), _jpeg(1024, 768))
    assert verdict.recevable is True
    assert verdict.motif is MotifRecevabilite.OK


def test_format_non_image_est_refuse():
    verdict = evaluer(_image(), b"MZ\x90\x00 un executable")
    assert verdict.recevable is False
    assert verdict.motif is MotifRecevabilite.FORMAT_REFUSE


def test_image_floue_est_refusee_avec_conseil():
    verdict = evaluer(_image(score_nettete=SEUIL_NETTETE - 1.0), _jpeg(1024, 768))
    assert verdict.recevable is False
    assert verdict.motif is MotifRecevabilite.FLOU
    assert "approchez" in verdict.conseil.lower()


def test_image_sous_exposee_est_refusee():
    verdict = evaluer(_image(luminance_moyenne=10.0), _jpeg(1024, 768))
    assert verdict.recevable is False
    assert verdict.motif is MotifRecevabilite.SOUS_EXPOSE


def test_image_sur_exposee_est_refusee_avec_conseil_de_contre_jour():
    verdict = evaluer(_image(luminance_moyenne=250.0), _jpeg(1024, 768))
    assert verdict.recevable is False
    assert verdict.motif is MotifRecevabilite.SUR_EXPOSE
    assert "soleil" in verdict.conseil.lower()


def test_image_trop_petite_est_refusee():
    petite = COTE_MIN_PX - 1
    verdict = evaluer(_image(largeur=petite, hauteur=petite), _jpeg(petite, petite))
    assert verdict.recevable is False
    assert verdict.motif is MotifRecevabilite.TROP_PETITE


def test_les_dimensions_de_l_entete_priment_sur_celles_declarees():
    """Le client annonce 1024x768, l'en-tête dit 100x100 : on croit l'en-tête."""
    verdict = evaluer(_image(largeur=1024, hauteur=768), _jpeg(100, 100))
    assert verdict.recevable is False
    assert verdict.motif is MotifRecevabilite.TROP_PETITE


# --------------------------------------------------------- branches defensives
#
# Les cas ci-dessous couvrent les chemins d'erreur du parseur d'en-tetes. Ce n'est
# pas de la couverture de complaisance : ces octets seront ecrits sur disque, et un
# parseur qui plante ou qui accepte n'importe quoi est une faille, pas un detail.


def test_png_tronque_avant_les_dimensions_est_rejete():
    assert dimensions_depuis_entete(b"\x89PNG\r\n\x1a\n" + b"\x00" * 4) is None


def test_png_sans_bloc_ihdr_est_rejete():
    faux = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"XXXX" + b"\x00" * 12
    assert dimensions_depuis_entete(faux) is None


def test_png_annoncant_une_dimension_nulle_est_rejete():
    assert dimensions_depuis_entete(_png(0, 768)) is None


def test_jpeg_avec_octets_parasites_avant_le_marqueur_est_lu_quand_meme():
    """Le parseur avance octet par octet jusqu'au prochain 0xFF (ligne de saut)."""
    brut = _jpeg(800, 600)
    bruite = brut[:2] + b"\x00\x00\x00" + brut[2:]
    assert dimensions_depuis_entete(bruite) == (800, 600)


def test_jpeg_dont_le_marqueur_sof_est_tronque_est_rejete():
    """SOF0 commence a l'octet 20 : il faut 29 octets pour lire ses dimensions."""
    tronque = _jpeg(800, 600)[:26]
    assert dimensions_depuis_entete(tronque) is None


def test_jpeg_avec_longueur_de_segment_absurde_est_rejete():
    """Une longueur declaree inferieure a 2 rendrait la progression infinie."""
    faux = b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", 0) + b"\x00" * 12
    assert dimensions_depuis_entete(faux) is None


def test_jpeg_sans_aucun_marqueur_sof_est_rejete():
    sans_sof = b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
    assert dimensions_depuis_entete(sans_sof) is None


def test_jpeg_annoncant_une_dimension_nulle_est_rejete():
    assert dimensions_depuis_entete(_jpeg(0, 600)) is None
