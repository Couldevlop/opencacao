"""Intégration : le POST /v1/chat route via l'orchestrateur quand le flag est ON.

Verrouille la non-régression du contrat HTTP : le chemin agentique (V3) doit
produire le même schéma de réponse que le chemin V2, en passant par la gestion de
sessions (DialogueSessionService) et le router inchangés.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api_deps import (
    _construire_orchestrateur,
    get_cache_client,
    get_conseil_service,
    get_inference_client,
    get_journal,
)
from app.application.conseil_agentique import ConseilAgentique
from app.core.config import get_settings
from app.main import create_app
from tests.conftest import FakeCache, FakeInference, FakeJournal, FakeRag


@pytest.fixture
def agents_client(
    fake_cache: FakeCache,
    fake_inference: FakeInference,
    fake_journal: FakeJournal,
    fake_rag: FakeRag,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[TestClient]:
    """Client de test avec la plateforme agentique ACTIVÉE (orchestrateur + agents)."""
    monkeypatch.setenv("PREWARM_ENABLED", "false")
    monkeypatch.setenv("SESSIONS_DB_PATH", str(tmp_path / "sessions.db"))
    monkeypatch.setenv("AGENTS_ENABLED", "true")
    get_settings.cache_clear()
    app = create_app()
    orchestrateur = _construire_orchestrateur(fake_inference, fake_cache, fake_journal, fake_rag)
    adaptateur = ConseilAgentique(orchestrateur)
    app.dependency_overrides[get_cache_client] = lambda: fake_cache
    app.dependency_overrides[get_inference_client] = lambda: fake_inference
    app.dependency_overrides[get_journal] = lambda: fake_journal
    app.dependency_overrides[get_conseil_service] = lambda: adaptateur
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def test_chat_via_orchestrateur_repond_200(agents_client: TestClient) -> None:
    reponse = agents_client.post("/v1/chat", json={"question": "comment tailler le cacaoyer ?"})
    assert reponse.status_code == 200
    body = reponse.json()
    assert body["reponse"]
    assert "interaction_id" in body
    assert "confiance" in body


def test_chat_via_orchestrateur_refuse_hors_filiere(agents_client: TestClient) -> None:
    # Garde-fou centralisé : une question hors cacao est redirigée vers l'ANADER.
    reponse = agents_client.post("/v1/chat", json={"question": "comment cultiver le maïs ?"})
    assert reponse.status_code == 200
    assert reponse.json()["redirection_anader"] is True
