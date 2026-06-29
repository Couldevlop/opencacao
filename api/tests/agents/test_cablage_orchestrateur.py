"""La composition racine assemble un orchestrateur fonctionnel + le flag de bascule."""

from __future__ import annotations

import pytest

from app.api_deps import _construire_orchestrateur, get_conseil_service
from app.application.conseil_agentique import ConseilAgentique
from app.application.orchestrateur import Orchestrateur
from app.core.config import get_settings
from app.services.outils.indisponible import MeteoIndisponible, PrixIndisponible


@pytest.mark.asyncio
async def test_outils_indisponibles_renvoient_vide() -> None:
    assert await MeteoIndisponible().previsions("Daloa") == {}
    assert await PrixIndisponible().cours() == {}


def test_construction_orchestrateur_enregistre_les_quatre_agents() -> None:
    # Ports factices : on ne teste que le câblage (aucun agent n'est appelé).
    orch = _construire_orchestrateur(inference=object(), cache=object(), journal=object(), rag=None)
    assert isinstance(orch, Orchestrateur)
    noms = orch._routeur._registre.noms()  # noqa: SLF001
    assert set(noms) == {"rag", "meteo", "prix", "reporting"}


class _State:
    inference = object()
    cache = object()
    journal = object()
    rag = None


class _App:
    state = _State()


class _Request:
    app = _App()


def test_get_conseil_service_renvoie_adaptateur_si_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTS_ENABLED", "true")
    get_settings.cache_clear()
    try:
        service = get_conseil_service(_Request())  # type: ignore[arg-type]
        assert isinstance(service, ConseilAgentique)
    finally:
        get_settings.cache_clear()
