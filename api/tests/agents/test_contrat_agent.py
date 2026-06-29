"""Le contrat d'agent : structures de données et conformité au Protocol."""

from __future__ import annotations

import dataclasses

import pytest

from app.domain.agents import AgentPort, AgentReponse, AgentRequete
from app.models.domain import Confiance, Langue


def _requete(question: str = "Comment tailler un cacaoyer ?") -> AgentRequete:
    return AgentRequete(
        question=question,
        langue=Langue.FR,
        historique=[],
        fil_ancre=question,
        client_ip="test",
    )


class _AgentFactice:
    """Agent minimal conforme au contrat (sert à valider le Protocol)."""

    nom = "factice"
    description = "agent de test"
    mots_cles = ("test",)

    async def peut_traiter(self, requete: AgentRequete) -> float:
        return 1.0 if "test" in requete.question.lower() else 0.0

    async def traiter(self, requete: AgentRequete) -> AgentReponse:
        return AgentReponse(
            texte="ok",
            sources=[],
            confiance=Confiance.MOYENNE,
            agent=self.nom,
        )


def test_agent_factice_est_conforme_au_port() -> None:
    assert isinstance(_AgentFactice(), AgentPort)


def test_requete_est_immuable() -> None:
    requete = _requete()
    with pytest.raises(dataclasses.FrozenInstanceError):
        requete.question = "autre"  # type: ignore[misc]


@pytest.mark.asyncio
async def test_peut_traiter_retourne_un_score() -> None:
    agent = _AgentFactice()
    assert await agent.peut_traiter(_requete("un test")) == 1.0
    assert await agent.peut_traiter(_requete("autre chose")) == 0.0


@pytest.mark.asyncio
async def test_traiter_retourne_une_reponse_attribuee() -> None:
    reponse = await _AgentFactice().traiter(_requete())
    assert reponse.agent == "factice"
    assert reponse.confiance is Confiance.MOYENNE
