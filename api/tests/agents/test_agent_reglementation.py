"""Agent Réglementation EUDR : routage réglementaire + cadrage EUDR injecté."""

from __future__ import annotations

import pytest

from app.domain.agents import AgentRequete
from app.models.domain import Langue
from app.services.agents.agent_reglementation import AgentReglementation


class _InferenceFactice:
    def __init__(self) -> None:
        self.contexte_recu: str | None = None

    async def generer(self, question, *, contexte=None, historique=None, **kw) -> str:
        self.contexte_recu = contexte
        return "Pour exporter vers l'UE, tracez vos parcelles (géolocalisation)."

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
async def test_peut_traiter_eleve_sur_question_eudr() -> None:
    agent = AgentReglementation(_InferenceFactice())
    assert await agent.peut_traiter(_requete("que dit l'EUDR sur la traçabilité du cacao ?")) >= 0.7
    assert await agent.peut_traiter(_requete("comment tailler le cacaoyer ?")) < 0.3


@pytest.mark.asyncio
async def test_traiter_injecte_le_cadre_eudr_avec_le_rag() -> None:
    inf = _InferenceFactice()
    agent = AgentReglementation(
        inf, rag=_RagFactice("Extrait sur la géolocalisation des parcelles.")
    )
    reponse = await agent.traiter(_requete("conformité EUDR pour exporter mon cacao ?"))
    assert reponse.agent == "reglementation"
    assert "EUDR" in (inf.contexte_recu or "")
    assert "géolocalisation" in (inf.contexte_recu or "").lower()


@pytest.mark.asyncio
async def test_traiter_sans_rag_garde_le_cadre() -> None:
    inf = _InferenceFactice()
    agent = AgentReglementation(inf, rag=None)
    await agent.traiter(_requete("règlement européen déforestation ?"))
    assert "EUDR" in (inf.contexte_recu or "")


@pytest.mark.asyncio
async def test_sans_rag_consigne_anti_fabrication_eudr() -> None:
    # Sans doc RAG, ne pas laisser inventer une date/seuil EUDR (reports successifs).
    inf = _InferenceFactice()
    agent = AgentReglementation(inf)  # pas de RAG
    await agent.traiter(_requete("Quand l'EUDR entre-t-il en vigueur ?"))
    ctx = inf.contexte_recu.lower()
    assert "eudr" in ctx
    assert "aucune date" in ctx or "n'avance" in ctx
    assert "report" in ctx
