"""Agent Reporting : synthèse narrative de contributions multi-agents."""

from __future__ import annotations

import pytest

from app.domain.agents import AgentReponse, AgentRequete
from app.models.domain import Confiance, Langue
from app.services.agents.agent_reporting import AgentReporting


class _InferenceFactice:
    def __init__(self) -> None:
        self.contexte_recu: str | None = None

    async def generer(self, question, *, contexte=None, historique=None, **kw) -> str:
        self.contexte_recu = contexte
        return "Synthèse : conditions favorables et prix porteur."

    def generer_stream(self, *a, **k): ...
    async def ready(self) -> bool:
        return True


def _requete(q: str = "fais-moi un bilan") -> AgentRequete:
    return AgentRequete(q, Langue.FR, q, "ip", [])


@pytest.mark.asyncio
async def test_peut_traiter_eleve_sur_demande_de_rapport() -> None:
    agent = AgentReporting(_InferenceFactice())
    assert await agent.peut_traiter(_requete("fais-moi une synthèse")) >= 0.7
    assert await agent.peut_traiter(_requete("comment tailler ?")) < 0.3


@pytest.mark.asyncio
async def test_synthetiser_fusionne_les_contributions() -> None:
    inf = _InferenceFactice()
    agent = AgentReporting(inf)
    contributions = [
        AgentReponse("Pluie demain.", ["meteo"], Confiance.MOYENNE, "meteo"),
        AgentReponse("Prix 1800 FCFA/kg.", ["CCC"], Confiance.ELEVEE, "prix"),
    ]
    reponse = await agent.synthetiser(_requete(), contributions)
    assert reponse.agent == "reporting"
    # Les contributions sont passées au LLM comme contexte de synthèse.
    assert "Pluie demain." in (inf.contexte_recu or "")
    assert "1800" in (inf.contexte_recu or "")
    # Les sources des contributions sont agrégées.
    assert "meteo" in reponse.sources and "CCC" in reponse.sources
    # Confiance prudente : la plus basse des contributions.
    assert reponse.confiance is Confiance.MOYENNE


@pytest.mark.asyncio
async def test_synthetiser_cadre_les_contributions_comme_analyses() -> None:
    # Souveraineté : les contributions sont cadrées comme des analyses d'agents (pas des
    # sources officielles brutes) et le modèle ne doit pas y ajouter de source non fournie.
    inf = _InferenceFactice()
    agent = AgentReporting(inf)
    contributions = [AgentReponse("Pluie demain.", ["meteo"], Confiance.MOYENNE, "meteo")]
    await agent.synthetiser(_requete(), contributions)
    ctx = (inf.contexte_recu or "").lower()
    assert "analyses" in ctx and "agents" in ctx
    assert "n'ajoute" in ctx and "aucune source" in ctx


@pytest.mark.asyncio
async def test_synthetiser_sans_contribution_degrade_proprement() -> None:
    # Aucune contribution : pas de plantage, confiance faible (rien à synthétiser).
    agent = AgentReporting(_InferenceFactice())
    reponse = await agent.synthetiser(_requete(), [])
    assert reponse.agent == "reporting"
    assert reponse.confiance is Confiance.FAIBLE
