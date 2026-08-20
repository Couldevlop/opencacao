"""Le gabarit HTML des alertes — ce que le destinataire voit vraiment.

Jusqu'ici le corps était un ``<pre>`` : du texte à chasse fixe, sans en-tête ni
identité. Ces tests portent sur les propriétés qui font qu'un email arrive lisible
partout — pas sur l'esthétique, qui se juge à l'œil.
"""

from __future__ import annotations

import re

import pytest

from app.core import email_gabarit

SUJET = "Le pod GPU tourne encore et vous est facturé"
TEXTE = (
    "Le service tourne sur CPU, mais le pod RunPod répond toujours.\n\n"
    "Arrêtez-le ici : https://console.runpod.io/pods\n"
    "« Stop », pas « Terminate »."
)


def test_le_sujet_apparait_dans_le_corps() -> None:
    """Un email dont le corps ne rappelle pas son objet se lit mal en liste."""
    assert SUJET in email_gabarit.html(SUJET, TEXTE)


def test_le_texte_est_rendu_en_paragraphes_et_non_en_bloc_brut() -> None:
    """Les lignes vides séparent des paragraphes ; sans cela tout se colle."""
    rendu = email_gabarit.html(SUJET, TEXTE)
    assert "<pre" not in rendu
    assert rendu.count("<p ") >= 2


def test_la_mise_en_forme_est_en_ligne() -> None:
    """La plupart des clients de messagerie suppriment les feuilles de style."""
    rendu = email_gabarit.html(SUJET, TEXTE)
    assert "<style" not in rendu
    assert "style=" in rendu


def test_la_structure_est_en_tableaux() -> None:
    """Outlook ne sait pas centrer un bloc autrement ; le flex y est ignoré."""
    rendu = email_gabarit.html(SUJET, TEXTE)
    assert "<table" in rendu
    assert "display:flex" not in rendu


def test_l_identite_du_projet_est_presente() -> None:
    rendu = email_gabarit.html(SUJET, TEXTE)
    assert "OpenCacao" in rendu
    assert "EA5B13" in rendu.upper()


def test_le_pied_dit_que_le_message_est_automatique() -> None:
    """Un destinataire doit savoir qu'il ne sert à rien de répondre."""
    assert "automatique" in email_gabarit.html(SUJET, TEXTE).lower()


@pytest.mark.parametrize(
    "poison",
    [
        "<script>alert(1)</script>",
        '" onload="alert(1)',
        "<img src=x onerror=alert(1)>",
    ],
)
def test_aucune_injection_html_ne_passe(poison: str) -> None:
    """Le corps vient d'un message d'alerte, parfois construit depuis des données.

    Un libellé de source ou une adresse de tunnel peut porter n'importe quoi : le
    gabarit doit échapper, jamais interpréter.
    """
    rendu = email_gabarit.html("Sujet", poison)
    # Ce qui compte n'est pas l'absence des MOTS — « onload » échappé reste du texte
    # inerte, et l'exiger absent ferait échouer une sortie pourtant correcte. Ce qui
    # compte est qu'aucune BALISE ni aucun ATTRIBUT ne se forme.
    assert poison not in rendu, "le poison ne doit jamais apparaître tel quel"
    assert "<script" not in rendu
    assert "<img" not in rendu
    # Les caractères structurants du HTML ont bien été neutralisés.
    assert "&lt;" in rendu or "&quot;" in rendu


def test_les_liens_deviennent_cliquables() -> None:
    """Une URL en texte brut n'est pas cliquable dans tous les clients."""
    rendu = email_gabarit.html(SUJET, TEXTE)
    assert 'href="https://console.runpod.io/pods"' in rendu


def test_un_lien_ne_peut_pas_porter_de_javascript() -> None:
    """Seuls http(s) sont rendus cliquables : `javascript:` reste du texte inerte."""
    rendu = email_gabarit.html("Sujet", "Voir javascript:alert(1) et ftp://x/y")
    assert 'href="javascript:' not in rendu
    assert 'href="ftp:' not in rendu


def test_le_preheader_resume_sans_polluer_l_affichage() -> None:
    """Le texte d'aperçu des boîtes doit exister mais rester invisible dans le corps."""
    rendu = email_gabarit.html(SUJET, TEXTE)
    apercu = re.search(r"display:\s*none[^>]*>([^<]{10,})", rendu)
    assert apercu, "aucun texte d'aperçu masqué"


def test_le_texte_brut_reste_lisible_seul() -> None:
    """Certains clients n'affichent que lui ; il ne doit pas être un HTML dégradé."""
    brut = email_gabarit.texte(SUJET, TEXTE)
    assert SUJET in brut
    assert "https://console.runpod.io/pods" in brut
    assert "<" not in brut
