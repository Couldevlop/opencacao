"""Chemins du service de conseil que rien n'exerçait.

Mêmes trous que dans l'orchestrateur, et pour la même raison : les tests posaient
toujours des questions qui déclenchent une clarification, et toujours en tour unique.

Deux règles n'étaient donc vérifiées dans aucun sens :

* une question assez précise ne doit PAS déclencher de clarification — c'est pourtant
  le cas le plus courant ;
* en conversation multi-tours, le cache exact n'est ni lu ni écrit, parce que « et pour
  Soubré ? » ne veut pas dire la même chose selon ce qui précède. L'y écrire
  empoisonnerait le cache pour tous les autres.
"""

from __future__ import annotations

import pytest

from app.application.conseil_service import ConseilService
from app.models.domain import Langue

from .conftest import FakeCache, FakeInference, FakeJournal

HISTORIQUE = [
    {"role": "user", "content": "Bonjour"},
    {"role": "assistant", "content": "Bonjour, en quoi puis-je aider ?"},
]

# Assez précise pour qu'aucun thème de clarification ne se déclenche.
PRECISE = "à quelle période récolter le cacao ?"


class CacheEspion(FakeCache):
    """Cache réel des tests, doublé d'un journal de ses accès."""

    def __init__(self) -> None:
        super().__init__()
        self.lectures: list[str] = []
        self.ecritures: list[str] = []

    async def get_cached(self, question: str, langue: str):
        self.lectures.append(question)
        return await super().get_cached(question, langue)

    async def set_cached(self, question: str, langue: str, payload: str) -> None:
        self.ecritures.append(question)
        await super().set_cached(question, langue, payload)


def _service(cache=None, dialogue_naturel=False, reponse_inference=None) -> ConseilService:
    return ConseilService(
        inference=FakeInference(reponse=reponse_inference),
        cache=cache or FakeCache(),
        journal=FakeJournal(),
        dialogue_naturel=dialogue_naturel,
    )


@pytest.mark.asyncio
async def test_une_question_precise_ne_declenche_pas_de_clarification():
    """Le discriminant est le CACHE, pas le texte rendu.

    La même inférence factice sert la clarification et la réponse normale : son texte
    revient donc dans les deux cas, et le comparer ne prouverait rien. En revanche la
    clarification rend la main AVANT la consultation du cache exact. Si le cache a été
    consulté, c'est qu'on est passé outre — ce qui est exactement la branche visée.
    """
    espion = CacheEspion()
    service = _service(cache=espion, dialogue_naturel=True, reponse_inference="Un conseil.")
    await service.conseiller(PRECISE, Langue.FR, "ip")
    assert (
        espion.lectures
    ), "la clarification s'est déclenchée alors qu'il n'y avait rien à clarifier"


@pytest.mark.asyncio
async def test_en_flux_une_question_precise_ne_declenche_pas_de_clarification():
    espion = CacheEspion()
    service = _service(cache=espion, dialogue_naturel=True, reponse_inference="Un conseil.")
    evenements = [e async for e in service.conseiller_stream(PRECISE, Langue.FR, "ip")]
    assert evenements
    assert espion.lectures


@pytest.mark.asyncio
async def test_un_tour_avec_historique_ne_touche_pas_au_cache_exact():
    espion = CacheEspion()
    service = _service(cache=espion)
    await service.conseiller("et pour Soubré ?", Langue.FR, "ip", historique=HISTORIQUE)
    assert espion.lectures == []
    assert espion.ecritures == []


@pytest.mark.asyncio
async def test_un_tour_unique_utilise_bien_le_cache_exact():
    """Contre-épreuve : sans elle, le test ci-dessus resterait vert si le cache avait
    simplement disparu du code."""
    espion = CacheEspion()
    service = _service(cache=espion)
    await service.conseiller(PRECISE, Langue.FR, "ip")
    assert espion.lectures
    assert espion.ecritures


@pytest.mark.asyncio
async def test_en_flux_un_tour_avec_historique_n_ecrit_pas_le_cache_exact():
    espion = CacheEspion()
    service = _service(cache=espion)
    evenements = [
        e
        async for e in service.conseiller_stream(
            "et pour Soubré ?", Langue.FR, "ip", historique=HISTORIQUE
        )
    ]
    assert evenements
    assert espion.ecritures == []


@pytest.mark.asyncio
async def test_en_flux_un_tour_unique_alimente_le_cache_exact():
    espion = CacheEspion()
    service = _service(cache=espion)
    [e async for e in service.conseiller_stream(PRECISE, Langue.FR, "ip")]
    assert espion.ecritures
