"""Agent Normes : routage certifications/qualité + cadrage référentiels injecté."""

from __future__ import annotations

import pytest

from app.domain.agents import AgentRequete
from app.models.domain import Langue
from app.services.agents.agent_normes import AgentNormes
from app.services.agents.agent_reglementation import AgentReglementation


class _InferenceFactice:
    def __init__(self) -> None:
        self.contexte_recu: str | None = None

    async def generer(self, question, *, contexte=None, historique=None, **kw) -> str:
        self.contexte_recu = contexte
        return "La certification Rainforest Alliance valorise le cacao durable."

    def generer_stream(self, *a, **k): ...
    async def ready(self) -> bool:
        return True


class _RagFactice:
    def __init__(self, contexte: str | None) -> None:
        self._contexte = contexte

    async def contexte_pour(self, question: str) -> str | None:
        return self._contexte


def _requete(q: str) -> AgentRequete:
    return AgentRequete(q, Langue.FR, q, "ip", [])


@pytest.mark.asyncio
async def test_peut_traiter_eleve_sur_question_certification() -> None:
    agent = AgentNormes(_InferenceFactice())
    assert await agent.peut_traiter(_requete("quelle certification Rainforest Alliance ?")) >= 0.7
    assert await agent.peut_traiter(_requete("comment tailler le cacaoyer ?")) < 0.3


@pytest.mark.asyncio
async def test_peut_traiter_reconnait_ars_1000_et_labels() -> None:
    agent = AgentNormes(_InferenceFactice())
    assert await agent.peut_traiter(_requete("c'est quoi la norme ARS 1000 ?")) >= 0.7
    assert await agent.peut_traiter(_requete("le label commerce équitable pour mon cacao")) >= 0.7


@pytest.mark.asyncio
async def test_traiter_injecte_le_cadre_normes_avec_le_rag() -> None:
    inf = _InferenceFactice()
    agent = AgentNormes(inf, rag=_RagFactice("ARS 1000 : lignes directrices cacao durable."))
    reponse = await agent.traiter(_requete("certification Fairtrade pour mon cacao ?"))
    assert reponse.agent == "normes"
    ctx = (inf.contexte_recu or "").lower()
    assert "certification" in ctx or "référentiel" in ctx or "referentiel" in ctx
    assert "ars 1000" in ctx


@pytest.mark.asyncio
async def test_traiter_sans_rag_garde_le_cadre() -> None:
    inf = _InferenceFactice()
    agent = AgentNormes(inf, rag=None)
    await agent.traiter(_requete("comment obtenir un label bio ?"))
    ctx = (inf.contexte_recu or "").lower()
    assert "certification" in ctx or "référentiel" in ctx or "referentiel" in ctx


@pytest.mark.asyncio
async def test_sans_rag_consigne_anti_fabrication() -> None:
    # Sans doc RAG : ne pas laisser inventer critères/seuils/primes/dates (ils varient).
    inf = _InferenceFactice()
    agent = AgentNormes(inf)  # pas de RAG
    await agent.traiter(_requete("quels sont les critères et la prime Rainforest Alliance ?"))
    ctx = (inf.contexte_recu or "").lower()
    assert "aucun" in ctx or "n'avance" in ctx or "n'invente" in ctx
    assert "certificateur" in ctx or "coopérative" in ctx or "anader" in ctx


@pytest.mark.asyncio
async def test_reglementation_cede_la_certification_a_normes() -> None:
    # Non-régression de la séparation : « certification » seul ne route plus vers EUDR.
    regl = AgentReglementation(_InferenceFactice())
    assert await regl.peut_traiter(_requete("quelle certification pour mon cacao ?")) < 0.3
