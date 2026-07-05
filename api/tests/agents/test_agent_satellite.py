"""Agent Satellite : alertes de déforestation GFW autour de la position (tool use)."""

from __future__ import annotations

import pytest

from app.domain.agents import AgentRequete
from app.models.domain import Langue
from app.services.agents.agent_satellite import AgentSatellite
from app.services.outils.indisponible import SatelliteIndisponible
from app.services.outils.satellite import OutilSatellite


class _SourceFactice:
    def __init__(self, resultat: dict | None = None, erreur: bool = False) -> None:
        self._resultat = resultat or {}
        self._erreur = erreur
        self.appels: list[dict] = []

    async def alertes(self, localite="", lat=None, lon=None) -> dict:
        self.appels.append({"localite": localite, "lat": lat, "lon": lon})
        if self._erreur:
            raise RuntimeError("api en panne")
        return self._resultat


@pytest.mark.asyncio
async def test_outil_satellite_transmet_les_arguments() -> None:
    source = _SourceFactice({"alertes_depuis_2021": 3})
    outil = OutilSatellite(source)
    resultat = await outil.invoquer(lat=5.78, lon=-6.59)
    assert resultat == {"alertes_depuis_2021": 3}
    assert source.appels == [{"localite": "", "lat": 5.78, "lon": -6.59}]


@pytest.mark.asyncio
async def test_outil_satellite_fail_soft_sur_exception() -> None:
    outil = OutilSatellite(_SourceFactice(erreur=True))
    assert await outil.invoquer(localite="Soubré") == {}


@pytest.mark.asyncio
async def test_source_indisponible_renvoie_vide() -> None:
    assert await SatelliteIndisponible().alertes(localite="Soubré") == {}


# ===== TESTS AGENT SATELLITE =====


class _InferenceFactice:
    def __init__(self) -> None:
        self.contexte_recu: str | None = None

    async def generer(self, question, *, contexte=None, historique=None, **kw) -> str:
        self.contexte_recu = contexte
        return "Réponse satellite."

    def generer_stream(self, *a, **k): ...

    async def ready(self) -> bool:
        return True


def _requete(q: str, historique: list[dict[str, str]] | None = None) -> AgentRequete:
    return AgentRequete(q, Langue.FR, q, "ip", historique or [])


def _agent(resultat: dict | None = None) -> tuple[AgentSatellite, _InferenceFactice]:
    inf = _InferenceFactice()
    return AgentSatellite(inf, OutilSatellite(_SourceFactice(resultat))), inf


@pytest.mark.asyncio
async def test_score_eleve_sur_question_deforestation() -> None:
    agent, _ = _agent()
    assert (
        await agent.peut_traiter(_requete("y a-t-il de la déforestation près de ma parcelle ?"))
        >= 0.7
    )
    assert await agent.peut_traiter(_requete("quel est le prix du cacao ?")) == 0.0


@pytest.mark.asyncio
async def test_gps_dans_le_fil_prioritaire_sur_la_localite() -> None:
    agent, inf = _agent({"alertes_depuis_2021": 0, "dates_recentes": [], "tampon_km": 1})
    await agent.traiter(_requete("déforestation autour de 5.72, -6.68 près de Soubré ?"))
    source = agent._outil._source  # type: ignore[attr-defined]
    assert source.appels[0]["lat"] == pytest.approx(5.72)
    assert source.appels[0]["lon"] == pytest.approx(-6.68)


@pytest.mark.asyncio
async def test_localite_seule_transmise_a_l_outil() -> None:
    agent, inf = _agent({"alertes_depuis_2021": 0, "dates_recentes": [], "tampon_km": 1})
    await agent.traiter(_requete("des alertes de déforestation à Soubré ?"))
    source = agent._outil._source  # type: ignore[attr-defined]
    assert source.appels[0]["localite"] == "Soubré"
    assert source.appels[0]["lat"] is None


@pytest.mark.asyncio
async def test_sans_localisation_demande_la_position() -> None:
    agent, inf = _agent()
    await agent.traiter(_requete("y a-t-il de la déforestation vers chez moi ?"))
    assert "position" in (inf.contexte_recu or "").lower()
    assert "n'avance aucun" in (inf.contexte_recu or "").lower()


@pytest.mark.asyncio
async def test_zero_alerte_reste_prudent() -> None:
    agent, inf = _agent({"alertes_depuis_2021": 0, "dates_recentes": [], "tampon_km": 1})
    await agent.traiter(_requete("déforestation à Soubré ?"))
    contexte = inf.contexte_recu or ""
    assert "0 alerte" in contexte or "aucune alerte" in contexte.lower()
    assert "jamais" in contexte.lower()  # interdiction de certifier la conformité


@pytest.mark.asyncio
async def test_alertes_detectees_injectees_avec_dates() -> None:
    agent, inf = _agent(
        {"alertes_depuis_2021": 216, "dates_recentes": ["2026-06-18"], "tampon_km": 1}
    )
    await agent.traiter(_requete("déforestation à Soubré ?"))
    contexte = inf.contexte_recu or ""
    assert "216" in contexte and "2026-06-18" in contexte


@pytest.mark.asyncio
async def test_source_indisponible_consigne_explicite() -> None:
    agent, inf = _agent({})  # outil renvoie {}
    await agent.traiter(_requete("déforestation à Soubré ?"))
    assert "indisponible" in (inf.contexte_recu or "").lower()
