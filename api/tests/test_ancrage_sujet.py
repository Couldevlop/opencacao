"""Ancrage d'une réponse courte sur le sujet déjà engagé.

Écart vécu en production le 11/09/2026, sur un dialogue de cinq tours :

    « Mon cacaoyer est malade »   -> clarifie
    « les feuilles jaunissent »   -> clarifie
    « depuis deux semaines »      -> répond
    « sur toute la parcelle »     -> **conseils sur l'emplacement d'une plantation**

Le fil ancré ne retient que le DERNIER tour utilisateur : la requête documentaire
valait « depuis deux semaines sur toute la parcelle ». Ni « cacaoyer », ni « malade »,
ni « feuilles » — et le mot « parcelle » ramenait des passages sur le choix du terrain.

On enrichit donc l'ancrage du SUJET connu, mais **seulement** quand le tour courant ne
porte aucun thème de lui-même : une question neuve doit continuer d'être prise pour ce
qu'elle est. Et l'enrichissement reste court — allonger une requête éteint le canal
lexical de la recherche hybride (mesuré en juillet : 0,75 -> 0,27, 93 documents -> 0).
"""

from __future__ import annotations

from app.application.contexte import fil_ancre, fil_ancre_sujet
from app.services import fiche


def _fil_symptome() -> list[dict[str, str]]:
    """Un dialogue engagé sur un jaunissement des feuilles."""
    return [
        {"role": "user", "content": "Mon cacaoyer est malade"},
        {"role": "assistant", "content": "Sur quelle partie ?"},
        {"role": "user", "content": "les feuilles jaunissent"},
        {"role": "assistant", "content": "Depuis quand ?"},
        {"role": "user", "content": "depuis deux semaines"},
        {"role": "assistant", "content": "Voici le conseil."},
    ]


def test_une_reponse_courte_est_ancree_sur_le_sujet() -> None:
    historique = _fil_symptome()
    question = "sur toute la parcelle"
    connue = fiche.extraire(question, historique)

    ancre = fil_ancre_sujet(question, historique, connue)

    assert "feuille" in ancre.lower()
    assert question in ancre


def test_une_question_neuve_n_est_pas_detournee() -> None:
    """Le producteur change de sujet : on le suit, on ne le ramène pas de force."""
    historique = _fil_symptome()
    question = "quel est le prix officiel du cacao ?"
    connue = fiche.extraire(question, historique)

    ancre = fil_ancre_sujet(question, historique, connue)

    assert "feuille" not in ancre.lower()
    assert question in ancre


def test_sans_sujet_connu_l_ancrage_ne_change_pas() -> None:
    """Aucun sujet engagé : le comportement historique, à l'octet près."""
    question = "bonjour"
    connue = fiche.extraire(question, [])

    assert fil_ancre_sujet(question, [], connue) == fil_ancre(question, [])


def test_l_ancrage_reste_court() -> None:
    """Une requête longue éteint le canal lexical : l'ajout tient en quelques mots."""
    historique = _fil_symptome()
    question = "sur toute la parcelle"
    connue = fiche.extraire(question, historique)

    enrichi = fil_ancre_sujet(question, historique, connue)

    assert len(enrichi) - len(fil_ancre(question, historique)) <= 60


def test_une_fiche_absente_ne_fait_pas_tomber_l_ancrage() -> None:
    """Certains chemins n'ont pas construit de fiche : le défaut est de ne rien ajouter."""
    question = "sur toute la parcelle"

    assert fil_ancre_sujet(question, _fil_symptome(), None) == fil_ancre(question, _fil_symptome())
