"""Agent Prix : tool use sur les données de marché du cacao."""

from __future__ import annotations

import pytest

from app.domain.agents import AgentRequete
from app.models.domain import Langue
from app.services.agents.agent_prix import AgentPrix
from app.services.outils.prix import OutilPrix


class _InferenceFactice:
    def __init__(self) -> None:
        self.contexte_recu: str | None = None

    async def generer(self, question, *, contexte=None, historique=None, **kw) -> str:
        self.contexte_recu = contexte
        return "Le prix bord-champ garanti est de 1800 FCFA/kg."

    def generer_stream(self, *a, **k): ...
    async def ready(self) -> bool:
        return True


class _PrixFactice:
    async def cours(self) -> dict:
        return {"prix_bord_champ_fcfa_kg": 1800, "campagne": "2025-2026"}


def _requete(q: str) -> AgentRequete:
    return AgentRequete(q, Langue.FR, q, "ip", [])


@pytest.mark.asyncio
async def test_peut_traiter_eleve_sur_question_prix() -> None:
    agent = AgentPrix(_InferenceFactice(), OutilPrix(_PrixFactice()))
    assert await agent.peut_traiter(_requete("à combien se vend le cacao ?")) >= 0.7
    assert await agent.peut_traiter(_requete("comment tailler le cacaoyer ?")) < 0.3


@pytest.mark.asyncio
async def test_traiter_injecte_le_cours_dans_le_contexte() -> None:
    inf = _InferenceFactice()
    agent = AgentPrix(inf, OutilPrix(_PrixFactice()))
    reponse = await agent.traiter(_requete("quel est le prix du cacao ?"))
    assert reponse.agent == "prix"
    assert inf.contexte_recu is not None
    assert "1800" in inf.contexte_recu


@pytest.mark.asyncio
async def test_prix_evite_les_faux_positifs_de_sous_chaine() -> None:
    # « discours » ne déclenche pas « cours » ; « vendredi » ne déclenche pas « vend ».
    agent = AgentPrix(_InferenceFactice(), OutilPrix(_PrixFactice()))
    assert await agent.peut_traiter(_requete("le discours du président vendredi")) == 0.0
