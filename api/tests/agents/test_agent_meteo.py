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
    def __init__(self) -> None:
        self.appelee_avec: str | None = None

    async def previsions(self, localite: str) -> dict:
        self.appelee_avec = localite
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


@pytest.mark.asyncio
async def test_peut_traiter_sur_vocabulaire_meteo_courant() -> None:
    # Les questions usuelles sur prévisions/précipitations/averses doivent partir à
    # l'agent Météo (et non tomber au RAG faute de mot-clé).
    agent = AgentMeteo(_InferenceFactice(), OutilMeteo(_MeteoFactice()))
    assert await agent.peut_traiter(_requete("quelles précipitations à Soubré ?")) >= 0.7
    assert await agent.peut_traiter(_requete("quelles sont les prévisions à Daloa ?")) >= 0.7
    assert await agent.peut_traiter(_requete("y aura-t-il des averses demain ?")) >= 0.7


@pytest.mark.asyncio
async def test_question_agronomie_ne_route_pas_meteo() -> None:
    # Sans mot climatique, une question d'agronomie reste au RAG (score météo nul).
    agent = AgentMeteo(_InferenceFactice(), OutilMeteo(_MeteoFactice()))
    assert await agent.peut_traiter(_requete("comment traiter les mirides du cacaoyer ?")) == 0.0
    # Faux positif de sous-chaîne évité : « printemps » ne déclenche pas « temps ».
    assert await agent.peut_traiter(_requete("au printemps le cacaoyer fleurit")) == 0.0


@pytest.mark.asyncio
async def test_outil_meteo_fail_soft() -> None:
    # Un outil dont la source échoue renvoie {} (l'agent dégrade proprement).
    class _SourceKO:
        async def previsions(self, localite: str) -> dict:
            raise RuntimeError("API météo indisponible")

    assert await OutilMeteo(_SourceKO()).invoquer(localite="Daloa") == {}


@pytest.mark.asyncio
async def test_localite_dans_historique_est_detectee() -> None:
    # La ville est citée dans un tour précédent, pas dans le dernier message.
    inf = _InferenceFactice()
    meteo = _MeteoFactice()
    agent = AgentMeteo(inf, OutilMeteo(meteo))
    requete = AgentRequete(
        "et la pluie demain ?",
        Langue.FR,
        "et la pluie demain ?",
        "ip",
        [{"role": "user", "content": "je suis planteur à Daloa"}],
    )
    await agent.traiter(requete)
    assert meteo.appelee_avec == "Daloa"


@pytest.mark.asyncio
async def test_zone_nord_consigne_sans_prevision() -> None:
    # Une ville de savane du Nord : on n'interroge PAS la météo, on redirige.
    inf = _InferenceFactice()
    meteo = _MeteoFactice()
    agent = AgentMeteo(inf, OutilMeteo(meteo))
    await agent.traiter(_requete("quel temps à Korhogo ?"))
    assert meteo.appelee_avec is None
    assert inf.contexte_recu is not None
    assert "Korhogo" in inf.contexte_recu
    assert "savane" in inf.contexte_recu.lower()


@pytest.mark.asyncio
async def test_sans_localite_demande_la_commune() -> None:
    # Aucune ville : on demande la commune, sans interroger la météo ni inventer.
    inf = _InferenceFactice()
    meteo = _MeteoFactice()
    agent = AgentMeteo(inf, OutilMeteo(meteo))
    await agent.traiter(_requete("y aura-t-il des averses demain ?"))
    assert meteo.appelee_avec is None
    assert inf.contexte_recu is not None
    assert "commune" in inf.contexte_recu.lower()


@pytest.mark.asyncio
async def test_previsions_vides_loggue_un_warning() -> None:
    # Localité cacaoyère mais prévisions indisponibles (API tombée) : on journalise
    # pour l'observabilité, et on dégrade en contexte None (jamais de météo inventée).
    import structlog

    class _MeteoVide:
        async def previsions(self, localite: str) -> dict:
            return {}

    inf = _InferenceFactice()
    agent = AgentMeteo(inf, OutilMeteo(_MeteoVide()))
    with structlog.testing.capture_logs() as logs:
        await agent.traiter(_requete("quel temps à Daloa ?"))
    assert inf.contexte_recu is None
    assert any(
        e["event"] == "meteo_previsions_vides" and e.get("localite") == "Daloa" for e in logs
    )
