"""Le registre : enregistrement, lookup, refus des doublons."""

from __future__ import annotations

import pytest

from app.application.registre import RegistreAgents
from app.domain.agents import AgentReponse, AgentRequete
from app.models.domain import Confiance


class _Agent:
    def __init__(self, nom: str) -> None:
        self.nom = nom
        self.description = f"agent {nom}"
        self.mots_cles = (nom,)

    async def peut_traiter(self, requete: AgentRequete) -> float:
        return 0.0

    async def traiter(self, requete: AgentRequete) -> AgentReponse:
        return AgentReponse("", [], Confiance.FAIBLE, self.nom)


def test_enregistrer_puis_obtenir() -> None:
    registre = RegistreAgents()
    agent = _Agent("rag")
    registre.enregistrer(agent)
    assert registre.obtenir("rag") is agent


def test_obtenir_inconnu_retourne_none() -> None:
    assert RegistreAgents().obtenir("absent") is None


def test_doublon_de_nom_rejete() -> None:
    registre = RegistreAgents()
    registre.enregistrer(_Agent("rag"))
    with pytest.raises(ValueError, match="déjà enregistré"):
        registre.enregistrer(_Agent("rag"))


def test_tous_et_noms() -> None:
    registre = RegistreAgents()
    registre.enregistrer(_Agent("rag"))
    registre.enregistrer(_Agent("meteo"))
    assert set(registre.noms()) == {"rag", "meteo"}
    assert len(registre.tous()) == 2
