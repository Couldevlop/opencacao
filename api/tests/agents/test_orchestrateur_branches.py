"""Chemins de l'orchestrateur que rien n'exerçait.

La couverture par BRANCHES a montré que plusieurs ``if`` de l'orchestrateur n'étaient
joués que dans un sens. Deux familles, et aucune n'est théorique :

* **Le tour SANS clarification.** Le dialogue naturel activé, tous les tests posaient
  une question qui déclenche un thème de clarification. Le cas le plus courant — une
  question assez précise pour qu'on y réponde directement — n'était jamais joué.
* **Le tour AVEC historique.** C'est la conversation multi-tours, une fonctionnalité
  livrée en V2. Le cache exact n'y est ni lu ni écrit, parce qu'une même question
  n'appelle pas la même réponse selon ce qui précède. Cette règle n'était vérifiée
  dans aucun sens.
"""

from __future__ import annotations

import pytest

from app.application.orchestrateur import Orchestrateur
from app.application.registre import RegistreAgents
from app.application.routage import RouteurIntention
from app.models.domain import Langue
from tests.agents.test_orchestrateur import (
    _AgentEspion,
    _AgentStream,
    _AgentSynthetiseur,
    _CacheFactice,
    _InferenceFactice,
    _JournalFactice,
)


@pytest.fixture
def orchestrateur_factory():
    """Même fabrique que ``test_orchestrateur``, montée sur ses pièces.

    La fixture d'origine est locale à son module. Plutôt que de la déplacer — ce qui
    toucherait un fichier étranger à ce travail — on la remonte ici depuis les mêmes
    doubles, pour que les deux fichiers décrivent le même orchestrateur.
    """

    def _fabrique(dialogue_naturel: bool = False, reponse_inference: str = "", cache=None):
        registre = RegistreAgents()
        # Agent capable de flux : les deux voies (synchrone et SSE) partagent la
        # même fabrique, donc le même agent doit servir les deux.
        registre.enregistrer(_AgentStream(["un conseil ", "documenté."], contexte="ctx"))
        return Orchestrateur(
            RouteurIntention(registre, seuil=0.3),
            _JournalFactice(),
            cache or _CacheFactice(),
            agent_defaut="rag",
            inference=_InferenceFactice(reponse_inference),
            dialogue_naturel=dialogue_naturel,
        )

    return _fabrique


HISTORIQUE = [
    {"role": "user", "content": "Bonjour"},
    {"role": "assistant", "content": "Bonjour, en quoi puis-je aider ?"},
]

# Question assez précise pour qu'aucun thème de clarification ne se déclenche.
PRECISE = "à quelle période récolter le cacao ?"


@pytest.mark.asyncio
async def test_une_question_precise_ne_declenche_pas_de_clarification(orchestrateur_factory):
    """Dialogue naturel actif, mais rien à clarifier : on répond."""
    orch = orchestrateur_factory(dialogue_naturel=True, reponse_inference="Sur quelle partie ?")
    conseil = await orch.traiter(PRECISE, Langue.FR, "ip")
    # Ce n'est pas la question de clarification qui revient, c'est un conseil.
    assert "Sur quelle partie" not in conseil.reponse


@pytest.mark.asyncio
async def test_en_flux_une_question_precise_ne_declenche_pas_de_clarification(
    orchestrateur_factory,
):
    orch = orchestrateur_factory(dialogue_naturel=True, reponse_inference="Sur quelle partie ?")
    evenements = [e async for e in orch.traiter_stream(PRECISE, Langue.FR, "ip")]
    assert evenements, "le flux doit produire quelque chose"
    assert not any("Sur quelle partie" in str(e) for e in evenements)


class _CacheEspion:
    """Cache qui note ce qu'on lui demande et ce qu'on lui confie."""

    def __init__(self) -> None:
        self.lectures: list[str] = []
        self.ecritures: list[str] = []

    async def hit_rate_limit(self, client_ip: str) -> bool:
        return False

    async def hit_quota(self, cle: str, limite: int, fenetre_s: int) -> bool:
        return False

    async def get_cached(self, question: str, langue: str):
        self.lectures.append(question)
        return None

    async def set_cached(self, question: str, langue: str, valeur: str) -> None:
        self.ecritures.append(question)

    async def get(self, cle: str):
        return None

    async def set(self, cle: str, valeur, ttl_s: int | None = None) -> None:
        return None


@pytest.mark.asyncio
async def test_un_tour_avec_historique_ne_lit_ni_n_ecrit_le_cache_exact(orchestrateur_factory):
    """« Et pour Soubré ? » ne veut pas dire la même chose selon ce qui précède.

    Le cache exact est indexé sur la seule question : le consulter dans une
    conversation rendrait la réponse d'un autre fil, et l'y écrire empoisonnerait le
    cache pour tout le monde.
    """
    espion = _CacheEspion()
    orch = orchestrateur_factory(cache=espion)
    await orch.traiter("et pour Soubré ?", Langue.FR, "ip", historique=HISTORIQUE)
    assert espion.lectures == []
    assert espion.ecritures == []


@pytest.mark.asyncio
async def test_un_tour_unique_utilise_bien_le_cache_exact(orchestrateur_factory):
    """Contre-épreuve : sans elle, le test ci-dessus passerait aussi si le cache avait
    purement disparu du code."""
    espion = _CacheEspion()
    orch = orchestrateur_factory(cache=espion)
    await orch.traiter("à quelle période récolter le cacao ?", Langue.FR, "ip")
    assert espion.lectures, "un tour unique doit consulter le cache exact"
    assert espion.ecritures, "un tour unique doit alimenter le cache exact"


@pytest.mark.asyncio
async def test_en_flux_un_tour_avec_historique_n_utilise_pas_le_cache_exact(
    orchestrateur_factory,
):
    espion = _CacheEspion()
    orch = orchestrateur_factory(cache=espion)
    evenements = [
        e
        async for e in orch.traiter_stream(
            "et pour Soubré ?", Langue.FR, "ip", historique=HISTORIQUE
        )
    ]
    assert evenements
    assert espion.lectures == []
    assert espion.ecritures == []


@pytest.mark.asyncio
async def test_en_flux_un_tour_unique_alimente_le_cache_exact(orchestrateur_factory):
    espion = _CacheEspion()
    orch = orchestrateur_factory(cache=espion)
    [e async for e in orch.traiter_stream("à quelle période récolter ?", Langue.FR, "ip")]
    assert espion.lectures


def _orchestrateur_compose(cache):
    """Orchestrateur en configuration de COMPOSITION : deux agents et un synthétiseur.

    La synthèse a son propre écriture de cache, distincte de celle du chemin mono-agent.
    Elle n'était exercée que sans historique.
    """
    registre = RegistreAgents()
    registre.enregistrer(_AgentEspion("rag", 0.4, "analyse RAG"))
    registre.enregistrer(_AgentEspion("meteo", 0.9, "analyse météo"))
    registre.enregistrer(_AgentSynthetiseur("reporting", 0.8, ["synthèse ", "décisionnelle."]))
    return Orchestrateur(
        RouteurIntention(registre, seuil=0.3),
        _JournalFactice(),
        cache,
        agent_defaut="rag",
    )


@pytest.mark.asyncio
async def test_une_synthese_avec_historique_n_empoisonne_pas_le_cache():
    """Même règle sur le chemin de composition : en conversation, on n'écrit pas.

    Ce chemin a sa propre écriture de cache. La règle y était donc à vérifier
    séparément — et elle ne l'était pas.
    """
    espion = _CacheEspion()
    orch = _orchestrateur_compose(espion)
    evenements = [
        e
        async for e in orch.traiter_stream(
            "comment tailler le cacaoyer ?", Langue.FR, "ip", historique=HISTORIQUE
        )
    ]
    assert any(e["type"] == "done" for e in evenements), "la synthèse doit aboutir"
    assert espion.ecritures == []


@pytest.mark.asyncio
async def test_une_synthese_en_tour_unique_alimente_le_cache():
    """Contre-épreuve du test ci-dessus."""
    espion = _CacheEspion()
    orch = _orchestrateur_compose(espion)
    [e async for e in orch.traiter_stream("comment tailler le cacaoyer ?", Langue.FR, "ip")]
    assert espion.ecritures
