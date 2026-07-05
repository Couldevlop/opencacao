"""Agent Satellite : alertes de déforestation GFW autour de la position (tool use)."""

from __future__ import annotations

import pytest

from app.domain.agents import AgentRequete
from app.models.domain import Langue
from app.services.agents.agent_reglementation import AgentReglementation
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
async def test_mot_faible_seul_ne_route_pas_vers_satellite() -> None:
    """« parcelle » seul (sans mot fort) ne doit pas détourner une question
    phytosanitaire vers un appel GFW hors sujet — revue finale 05/07."""
    agent, _ = _agent()
    assert (
        await agent.peut_traiter(_requete("Comment traiter les chenilles sur ma parcelle ?")) == 0.0
    )


@pytest.mark.asyncio
async def test_mot_fort_et_faible_cumulent_le_score() -> None:
    agent, _ = _agent()
    assert (
        await agent.peut_traiter(_requete("Y a-t-il de la déforestation sur ma parcelle ?")) >= 0.8
    )


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


@pytest.mark.asyncio
async def test_gps_virgule_decimale_francaise_reconnue() -> None:
    agent, inf = _agent({"alertes_depuis_2021": 0, "dates_recentes": [], "tampon_km": 1})
    await agent.traiter(_requete("déforestation autour de 5,72, -6,68 près de Soubré ?"))
    source = agent._outil._source  # type: ignore[attr-defined]
    assert source.appels[0]["lat"] == pytest.approx(5.72)
    assert source.appels[0]["lon"] == pytest.approx(-6.68)


@pytest.mark.asyncio
async def test_alertes_anciennes_sans_date_recente_ne_laisse_pas_de_trou() -> None:
    """Garde : alertes anciennes (dates_recentes vide) ne doit pas produire une
    phrase tronquée « Dernières dates d'alerte : . » qui inviterait le LLM à
    inventer une date — revue finale 05/07."""
    agent, inf = _agent({"alertes_depuis_2021": 5, "dates_recentes": [], "tampon_km": 1})
    await agent.traiter(_requete("déforestation à Soubré ?"))
    contexte = inf.contexte_recu or ""
    assert "Dernières dates d'alerte : ." not in contexte
    assert "5" in contexte
    assert "aucune alerte récente" in contexte.lower()
    assert "avant l'an dernier" in contexte.lower()
    assert "n'invente" in contexte.lower()


# ===== FRONTIÈRE AVEC L'AGENT RÉGLEMENTATION =====


@pytest.mark.asyncio
async def test_frontiere_deforestation_va_au_satellite() -> None:
    satellite, _ = _agent()
    reglementation = AgentReglementation(_InferenceFactice())
    question = _requete("y a-t-il des alertes de déforestation sur ma parcelle ?")
    assert await satellite.peut_traiter(question) > await reglementation.peut_traiter(question)


@pytest.mark.asyncio
async def test_frontiere_eudr_reste_reglementaire() -> None:
    satellite, _ = _agent()
    reglementation = AgentReglementation(_InferenceFactice())
    question = _requete("que demande la réglementation EUDR pour exporter ?")
    assert await reglementation.peut_traiter(question) > await satellite.peut_traiter(question)


@pytest.mark.asyncio
async def test_deforestation_seule_ne_score_plus_reglementation() -> None:
    """Verrouille le retrait : « déforestation » seul ne route plus vers Réglementation."""
    reglementation = AgentReglementation(_InferenceFactice())
    question = _requete("il y a de la déforestation près de chez moi, c'est grave ?")
    assert await reglementation.peut_traiter(question) == 0.0
    satellite, _ = _agent()
    assert await satellite.peut_traiter(question) >= 0.7
