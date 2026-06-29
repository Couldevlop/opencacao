"""Agent RAG : conseil agronomique ancré, sert aussi d'agent par défaut."""

from __future__ import annotations

import pytest

from app.domain.agents import AgentRequete
from app.models.domain import Langue
from app.services.agents.agent_rag import AgentRag


class _InferenceFactice:
    def __init__(self, texte: str) -> None:
        self._texte = texte
        self.contexte_recu: str | None = None

    async def generer(self, question, *, contexte=None, historique=None, **kw) -> str:
        self.contexte_recu = contexte
        return self._texte

    def generer_stream(self, *a, **k): ...
    async def ready(self) -> bool:
        return True


class _RagFactice:
    def __init__(self, contexte: str | None) -> None:
        self._contexte = contexte

    async def contexte_pour(self, question: str) -> str | None:
        return self._contexte


def _requete(q: str = "comment tailler le cacaoyer ?") -> AgentRequete:
    return AgentRequete(q, Langue.FR, q, "ip", [])


@pytest.mark.asyncio
async def test_agent_rag_repond_avec_contexte() -> None:
    inf = _InferenceFactice("Taillez en saison sèche (source : CNRA).")
    agent = AgentRag(inf, rag=_RagFactice("Extrait CNRA sur la taille."))
    reponse = await agent.traiter(_requete())
    assert reponse.agent == "rag"
    assert "CNRA" in reponse.sources
    assert inf.contexte_recu == "Extrait CNRA sur la taille."


@pytest.mark.asyncio
async def test_agent_rag_sans_rag_fonctionne() -> None:
    agent = AgentRag(_InferenceFactice("Conseil générique."), rag=None)
    reponse = await agent.traiter(_requete())
    assert reponse.texte == "Conseil générique."


@pytest.mark.asyncio
async def test_peut_traiter_score_eleve_par_defaut() -> None:
    # Agent généraliste : score non nul sur toute question cacao (sert de repli).
    agent = AgentRag(_InferenceFactice("x"), rag=None)
    assert await agent.peut_traiter(_requete()) >= 0.3
