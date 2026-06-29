"""Le routeur d'intention : classement des agents par score."""

from __future__ import annotations

import pytest

from app.application.registre import RegistreAgents
from app.application.routage import RouteurIntention
from app.domain.agents import AgentReponse, AgentRequete
from app.models.domain import Confiance, Langue


class _AgentScore:
    def __init__(self, nom: str, score: float) -> None:
        self.nom = nom
        self.description = nom
        self.mots_cles = (nom,)
        self._score = score

    async def peut_traiter(self, requete: AgentRequete) -> float:
        return self._score

    async def traiter(self, requete: AgentRequete) -> AgentReponse:
        return AgentReponse("", [], Confiance.FAIBLE, self.nom)


def _requete() -> AgentRequete:
    return AgentRequete("q", Langue.FR, "q", "ip", [])


def _routeur(*scores: tuple[str, float]) -> RouteurIntention:
    registre = RegistreAgents()
    for nom, score in scores:
        registre.enregistrer(_AgentScore(nom, score))
    return RouteurIntention(registre, seuil=0.3)


@pytest.mark.asyncio
async def test_classe_par_score_decroissant() -> None:
    routeur = _routeur(("rag", 0.5), ("meteo", 0.9), ("prix", 0.1))
    classement = await routeur.classer(_requete())
    assert [a.nom for a, _ in classement] == ["meteo", "rag"]  # prix sous le seuil


@pytest.mark.asyncio
async def test_meilleur_retourne_le_plus_pertinent() -> None:
    routeur = _routeur(("rag", 0.5), ("meteo", 0.9))
    meilleur = await routeur.meilleur(_requete())
    assert meilleur is not None
    assert meilleur.nom == "meteo"


@pytest.mark.asyncio
async def test_meilleur_none_si_tous_sous_le_seuil() -> None:
    routeur = _routeur(("rag", 0.1), ("meteo", 0.0))
    assert await routeur.meilleur(_requete()) is None
