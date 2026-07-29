"""Tests de la purge des caractères refusés par XML 1.0.

Contrainte commune aux quatre formats : ce n'est pas une garantie propre à l'un
d'eux qui fuiterait dans les autres, c'est la même norme.
"""

from __future__ import annotations

import json

import pytest

from app.services.rendu.ooxml import texte_xml_sur


@pytest.mark.parametrize(
    "hostile",
    ["\x00", "\x0b", "\x1f", "\ud800", "\udfff", "\ufffe", "\uffff"],
)
def test_les_caracteres_refuses_par_xml_sont_retires(hostile):
    assert texte_xml_sur(f"avant{hostile}apres") == "avantapres"


@pytest.mark.parametrize("legitime", ["\n", "\t", "\r", "é", "🌱", "\u200b", "منطقة"])
def test_les_caracteres_legitimes_sont_conserves(legitime):
    """Purger trop defigurerait le document : sauts de ligne, accents, hors-BMP."""
    assert legitime in texte_xml_sur(f"avant{legitime}apres")


def test_un_surrogate_isole_peut_venir_du_modele():
    """C est la voie reelle : json.loads l accepte, et la sortie modele passe par la."""
    assert json.loads('"\ud800"') == "\ud800"
    assert texte_xml_sur(json.loads('"\ud800"')) == ""


def test_le_texte_purge_est_toujours_encodable():
    """Sans cela, l encodage echoue en ValueError et le routeur rend « format inconnu »."""
    for hostile in ("\ud800", "\ufffe", "\x00"):
        texte_xml_sur(f"x{hostile}y").encode("utf-8")
