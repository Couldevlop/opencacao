"""Le balisage du modèle doit être RENDU, jamais imprimé.

Constat de l'audit du 19/08 : 52 occurrences de ``**`` imprimées telles quelles dans
les documents Word livrés. Le modèle émet du gras Markdown ; les adaptateurs de format
l'écrivaient en toutes lettres, et l'étude avait l'air d'un copier-coller de chat.
"""

from __future__ import annotations

import pytest

from app.services.rendu import enrichi


def test_le_gras_devient_un_segment_gras() -> None:
    """« **1 200 FCFA** » doit produire un segment marqué gras, sans les astérisques."""
    assert enrichi.segments("Le prix est **1 200 FCFA** par kilo.") == (
        ("Le prix est ", False, False),
        ("1 200 FCFA", True, False),
        (" par kilo.", False, False),
    )


def test_l_italique_est_reconnu_sans_confondre_avec_le_gras() -> None:
    """Un seul astérisque encadre de l'italique ; deux, du gras. L'ordre compte."""
    assert enrichi.segments("**gras** et *italique*") == (
        ("gras", True, False),
        (" et ", False, False),
        ("italique", False, True),
    )


def test_un_texte_sans_balisage_reste_un_seul_segment() -> None:
    """Le cas de loin le plus fréquent ne doit rien coûter."""
    assert enrichi.segments("Prose analytique ordinaire.") == (
        ("Prose analytique ordinaire.", False, False),
    )


@pytest.mark.parametrize(
    "texte",
    [
        "un astérisque * isolé",
        "une multiplication 3 * 4 = 12",
        "**non fermé",
        "",
    ],
)
def test_un_balisage_incomplet_ne_mange_pas_le_texte(texte: str) -> None:
    """Mieux vaut un astérisque visible qu'une phrase amputée."""
    assert "".join(s[0] for s in enrichi.segments(texte)) == texte


def test_le_balisage_vide_n_est_pas_du_gras() -> None:
    """« **** » ne doit pas produire un segment gras vide."""
    assert "".join(s[0] for s in enrichi.segments("a ** b")) == "a ** b"


def test_sans_balisage_rend_le_texte_nu() -> None:
    """Pour les formats sans enrichissement (Excel, métadonnées, titres de figure)."""
    assert enrichi.sans_balisage("Le prix est **1 200 FCFA** par *kilo*.") == (
        "Le prix est 1 200 FCFA par kilo."
    )


def test_le_gras_survit_a_la_ponctuation_collee() -> None:
    """Le modèle écrit souvent « **mot**, » ou « **mot**. »."""
    segments = enrichi.segments("La **réforme**, engagée en 2012.")
    assert segments[1] == ("réforme", True, False)
    assert segments[2][0].startswith(",")
