"""Agent Météo : tool use (récupère des prévisions, raisonne dessus)."""

from __future__ import annotations

import pytest

from app.domain.agents import AgentRequete
from app.models.domain import Langue
from app.services.agents.agent_meteo import AgentMeteo
from app.services.outils.meteo import OutilMeteo


class _InferenceFactice:
    def __init__(self) -> None:
        self.contexte_recu: str | None = None

    async def generer(self, question, *, contexte=None, historique=None, **kw) -> str:
        self.contexte_recu = contexte
        return "Évitez de traiter avant la pluie prévue demain."

    def generer_stream(self, *a, **k): ...
    async def ready(self) -> bool:
        return True


class _MeteoFactice:
    async def previsions(self, localite: str) -> dict:
        return {"localite": localite, "pluie_mm_24h": 12, "resume": "pluie demain"}


def _requete(q: str) -> AgentRequete:
    return AgentRequete(q, Langue.FR, q, "ip", [])


@pytest.mark.asyncio
async def test_peut_traiter_eleve_sur_question_meteo() -> None:
    agent = AgentMeteo(_InferenceFactice(), OutilMeteo(_MeteoFactice()))
    assert await agent.peut_traiter(_requete("dois-je traiter avant la pluie ?")) >= 0.7
    assert await agent.peut_traiter(_requete("quel prix du cacao ?")) < 0.3


@pytest.mark.asyncio
async def test_traiter_injecte_les_previsions_dans_le_contexte() -> None:
    inf = _InferenceFactice()
    agent = AgentMeteo(inf, OutilMeteo(_MeteoFactice()))
    reponse = await agent.traiter(_requete("quand traiter à Daloa ?"))
    assert reponse.agent == "meteo"
    assert inf.contexte_recu is not None
    assert "pluie" in inf.contexte_recu.lower()
