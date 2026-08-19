"""Souplesse des déclencheurs météo — sans rouvrir la porte aux faux positifs.

Constat du 19/08/2026 en production : « Quel temps fait-il à Daloa cette semaine pour
mes traitements ? » n'appelait PAS l'agent météo, et la réponse était le repli
souverain (« je n'ai pas accès aux prévisions »). Correct sur le fond — le modèle
n'invente jamais de météo — mais déconcertant pour qui pose une question de météo en
français courant.

La cause était un choix défendable : « temps » seul est ambigu (« temps de séchage »,
« en même temps », « combien de temps »), et l'avoir comme déclencheur détournait des
questions ancrées sur le RAG. La sortie n'est pas de le réintroduire seul, mais
d'ajouter des **tournures** et des termes régionaux non ambigus.

Ces tests tiennent les deux bouts : ce qui doit déclencher, et surtout ce qui ne doit
PAS déclencher — sans la seconde moitié, il suffirait de tout accepter pour être vert.
"""

from __future__ import annotations

import pytest

from app.services.agents.agent_meteo import _MOTS_METEO
from app.services.agents.base import compter_mots_cles


def est_pertinent(question: str) -> bool:
    """Reproduit la décision de routage réelle : un mot climatique dans le texte."""
    return compter_mots_cles(question, _MOTS_METEO) > 0


@pytest.mark.parametrize(
    "question",
    [
        "Quel temps fait-il à Daloa cette semaine pour mes traitements ?",
        "Il fait quel temps à Soubré ?",
        "On aura du beau temps pour la récolte ?",
        "Le mauvais temps va-t-il durer ?",
        "L'harmattan arrive quand cette année ?",
        "Est-ce qu'il y a des orages prévus à Gagnoa ?",
        "Quelle température fait-il à Abengourou ?",
        "L'hivernage a commencé dans ma zone ?",
        "Il fait très chaud, est-ce mauvais pour mes jeunes plants ?",
        "Le vent est fort en ce moment, dois-je m'inquiéter ?",
        # Déjà couverts avant ce chantier : on vérifie qu'on ne les a pas cassés.
        "Va-t-il pleuvoir cette semaine à Daloa ?",
        "Quelles sont les prévisions météo ?",
    ],
)
def test_une_vraie_question_meteo_declenche_l_agent(question: str) -> None:
    assert est_pertinent(question) is True


@pytest.mark.parametrize(
    "question",
    [
        # « temps » au sens durée ou séquence : ne doit RIEN déclencher.
        # (« temps de séchage » n'est PAS ici : « séchage » déclenche déjà la météo,
        #  volontairement — sécher des fèves dépend du temps qu'il fait.)
        "Combien de temps faut-il pour fermenter les fèves ?",
        "Je taille et je récolte en même temps, est-ce une erreur ?",
        # Questions de conseil pur : elles appartiennent au RAG.
        "Quand récolter le cacao ?",
        "Comment reconnaître une cabosse mûre ?",
        "C'est quoi le FIRCA ?",
        "Quel est le prix officiel du cacao ?",
    ],
)
def test_une_question_sans_meteo_ne_detourne_pas_vers_l_agent(question: str) -> None:
    """La moitié qui compte : un déclencheur trop large renvoie vers la météo des
    questions qui attendaient le corpus, et la réponse devient hors sujet."""
    assert est_pertinent(question) is False
