"""Gabarit des emails d'alerte — un seul, pour les deux canaux d'envoi.

Le corps était jusqu'ici un ``<pre>`` : du texte à chasse fixe, sans en-tête ni
identité, qui donnait à une alerte d'exploitation l'air d'une sortie de terminal
égarée dans une boîte aux lettres.

**Les contraintes d'un email ne sont pas celles d'une page web**, et ce sont elles qui
dictent la forme :

* **mise en forme en ligne** — la plupart des clients suppriment ou ignorent les
  feuilles de style, y compris celles embarquées dans un ``<style>`` ;
* **structure en tableaux** — Outlook rend via Word, qui ne connaît ni ``flex`` ni
  ``grid`` ; un tableau reste le seul moyen fiable de centrer un bloc ;
* **largeur bornée à 600 px** — au-delà, les volets de lecture coupent ;
* **aucune image externe** — elles sont bloquées par défaut, et une identité qui repose
  sur une image bloquée n'existe pas. La nôtre tient dans du texte et une couleur ;
* **texte d'aperçu** — la ligne que les boîtes affichent après l'objet. Sans elle, elles
  y mettent le premier bout de HTML venu.

**Le corps est échappé, jamais interprété.** Une alerte peut contenir une adresse de
tunnel, un libellé de source, un message d'erreur — c'est-à-dire des données. Les rendre
en HTML brut ouvrirait une injection dans la boîte de qui reçoit l'alerte.
"""

from __future__ import annotations

import re

# Reprise de la charte des livrables (``services/rendu/charte.py``). Dupliquée ici
# volontairement : ``core`` ne doit pas dépendre de ``services``, et trois constantes
# de couleur ne justifient pas d'inverser cette dépendance.
_ORANGE = "#EA5B13"
_SOMBRE = "#1F1F1F"
_GRIS = "#606060"
_FOND = "#F4F4F5"
_BLANC = "#FFFFFF"

_PRODUIT = "OpenCacao"
_EDITEUR = "OpenLab Consulting"
_POLICE = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"

# Seuls http(s) deviennent cliquables. « javascript: » et « data: » resteraient du texte
# inerte : un lien actif dans un email d'alerte est une surface d'attaque gratuite.
_URL = re.compile(r"(https?://[^\s<>\"']+)")

_LONGUEUR_APERCU = 140


def echapper(texte: str) -> str:
    """Neutralise tout ce qui pourrait être interprété comme du HTML."""
    return (
        texte.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _lier(fragment_echappe: str) -> str:
    """Rend cliquables les URL http(s) d'un fragment DÉJÀ échappé."""
    return _URL.sub(
        lambda m: f'<a href="{m.group(1)}" style="color:{_ORANGE};">{m.group(1)}</a>',
        fragment_echappe,
    )


def _paragraphes(texte: str) -> str:
    """Convertit le texte en paragraphes, les lignes vides faisant la séparation."""
    style = (
        f"margin:0 0 14px;font-family:{_POLICE};font-size:15px;" f"line-height:1.6;color:{_SOMBRE};"
    )
    blocs = [bloc.strip() for bloc in re.split(r"\n\s*\n", texte.strip()) if bloc.strip()]
    return "".join(
        f'<p style="{style}">{_lier(echapper(bloc)).replace(chr(10), "<br>")}</p>' for bloc in blocs
    )


def _apercu(texte: str) -> str:
    """Première ligne utile, pour la ligne d'aperçu des boîtes aux lettres."""
    plat = " ".join(texte.split())
    return plat[:_LONGUEUR_APERCU] + ("…" if len(plat) > _LONGUEUR_APERCU else "")


def html(sujet: str, texte: str) -> str:
    """Rend le corps HTML d'une alerte.

    Args:
        sujet: Objet du message, repris en titre — un email dont le corps ne rappelle
            pas son objet se lit mal une fois ouvert depuis une liste.
        texte: Corps en texte brut. Les lignes vides séparent les paragraphes.

    Returns:
        Un document HTML complet, autonome, sans ressource externe.
    """
    return (
        '<!doctype html><html lang="fr"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
        f'<body style="margin:0;padding:0;background:{_FOND};">'
        # Aperçu : lu par la boîte, invisible dans le corps.
        f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;">'
        f"{echapper(_apercu(texte))}</div>"
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="background:{_FOND};padding:24px 12px;">'
        '<tr><td align="center">'
        f'<table role="presentation" width="600" cellpadding="0" cellspacing="0" '
        f'style="max-width:600px;width:100%;background:{_BLANC};border-radius:10px;'
        'overflow:hidden;border:1px solid #E4E4E7;">'
        # Bandeau d'identité. Du texte et une couleur : une image serait bloquée.
        f'<tr><td style="background:{_ORANGE};padding:18px 28px;">'
        f'<span style="font-family:{_POLICE};font-size:17px;font-weight:700;'
        f'color:{_BLANC};letter-spacing:.3px;">{_PRODUIT}</span>'
        f'<span style="font-family:{_POLICE};font-size:12px;color:#FFE3D0;'
        "float:right;padding-top:5px;\">Alerte d'exploitation</span>"
        "</td></tr>"
        f'<tr><td style="padding:26px 28px 6px;">'
        f'<h1 style="margin:0 0 16px;font-family:{_POLICE};font-size:19px;'
        f'line-height:1.35;color:{_SOMBRE};font-weight:600;">{echapper(sujet)}</h1>'
        f"{_paragraphes(texte)}"
        "</td></tr>"
        f'<tr><td style="padding:8px 28px 24px;">'
        f'<div style="border-top:1px solid #E4E4E7;padding-top:14px;'
        f'font-family:{_POLICE};font-size:12px;line-height:1.5;color:{_GRIS};">'
        f"Message <strong>automatique</strong> émis par {_PRODUIT} — inutile d'y "
        f"répondre.<br>{_EDITEUR}"
        "</div></td></tr>"
        "</table></td></tr></table></body></html>"
    )


def texte(sujet: str, corps: str) -> str:
    """Rend la version texte brut, lisible seule.

    Certains clients n'affichent que celle-ci. Elle ne doit donc pas être un HTML
    dégradé, mais un message complet.

    Args:
        sujet: Objet du message.
        corps: Corps en texte brut.

    Returns:
        Le message en texte seul, objet et pied compris.
    """
    return (
        f"{_PRODUIT} — alerte d'exploitation\n"
        f"{'=' * 40}\n\n"
        f"{sujet}\n\n"
        f"{corps.strip()}\n\n"
        f"{'-' * 40}\n"
        f"Message automatique émis par {_PRODUIT} — inutile d'y répondre.\n"
        f"{_EDITEUR}\n"
    )
