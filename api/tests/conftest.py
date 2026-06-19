"""Fixtures de test : application avec inférence et cache mockés."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api_deps import (
    get_cache_client,
    get_conseil_service,
    get_inference_client,
    get_journal,
)
from app.application.conseil_service import ConseilService
from app.core.config import get_settings
from app.main import create_app


class FakeCache:
    """Cache en mémoire, sans Redis, pour les tests."""

    def __init__(self, rate_limit: int = 20) -> None:
        self._store: dict[str, str] = {}
        self._counts: dict[str, int] = {}
        self._rate_limit = rate_limit

    async def get_cached(self, question: str, langue: str) -> str | None:
        return self._store.get(f"{langue}:{question}")

    async def set_cached(self, question: str, langue: str, payload: str) -> None:
        self._store[f"{langue}:{question}"] = payload

    async def hit_rate_limit(self, client_ip: str) -> bool:
        self._counts[client_ip] = self._counts.get(client_ip, 0) + 1
        return self._counts[client_ip] > self._rate_limit

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        return None


class FakeInference:
    """Inférence simulée : réponse fixe, configurable pour échouer."""

    def __init__(self, reponse: str | None = None, disponible: bool = True) -> None:
        self.reponse = reponse or (
            "Pour bien sécher vos fèves, étalez-les en couche fine au soleil. "
            "Sources : CNRA, ANADER."
        )
        self.disponible = disponible
        self.appels: list[str] = []

    async def generer(self, question: str, **_: object) -> str:
        from app.services.inference import InferenceUnavailable

        if not self.disponible:
            raise InferenceUnavailable("indisponible")
        self.appels.append(question)
        return self.reponse

    async def generer_stream(self, question: str, **_: object):
        """Émet la réponse en plusieurs fragments, comme un flux SSE."""
        from app.services.inference import InferenceUnavailable

        if not self.disponible:
            raise InferenceUnavailable("indisponible")
        self.appels.append(question)
        pas = max(1, len(self.reponse) // 3)
        for debut in range(0, len(self.reponse), pas):
            yield self.reponse[debut : debut + pas]

    async def ready(self) -> bool:
        return self.disponible

    async def close(self) -> None:
        return None


class FakeJournal:
    """Journal en mémoire : enregistre interactions et retours, sans fichier."""

    def __init__(self) -> None:
        self.interactions: list[dict] = []
        self.feedbacks: list[dict] = []
        self.visites: list[dict] = []

    async def enregistrer_interaction(
        self,
        question: str,
        langue: str,
        reponse: str,
        confiance: str,
        sources: list[str],
        redirection_anader: bool,
    ) -> str:
        identifiant = f"test{len(self.interactions):08d}"
        self.interactions.append(
            {"id": identifiant, "question": question, "reponse": reponse, "vote": None}
        )
        return identifiant

    async def enregistrer_feedback(self, interaction_id: str, vote: str) -> None:
        self.feedbacks.append({"id": interaction_id, "vote": vote})

    async def enregistrer_visite(self, pays: str, continent: str, canal: str) -> None:
        self.visites.append({"pays": pays, "continent": continent, "canal": canal})


@pytest.fixture
def fake_cache() -> FakeCache:
    return FakeCache()


@pytest.fixture
def fake_inference() -> FakeInference:
    return FakeInference()


@pytest.fixture
def fake_journal() -> FakeJournal:
    return FakeJournal()


@pytest.fixture
def client(
    fake_cache: FakeCache,
    fake_inference: FakeInference,
    fake_journal: FakeJournal,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    """Client de test avec inférence, cache et journal surchargés."""
    # Pas de pré-chauffage en test (éviterait de vrais appels d'inférence au démarrage).
    monkeypatch.setenv("PREWARM_ENABLED", "false")
    get_settings.cache_clear()
    app = create_app()
    service = ConseilService(inference=fake_inference, cache=fake_cache, journal=fake_journal)
    app.dependency_overrides[get_cache_client] = lambda: fake_cache
    app.dependency_overrides[get_inference_client] = lambda: fake_inference
    app.dependency_overrides[get_journal] = lambda: fake_journal
    app.dependency_overrides[get_conseil_service] = lambda: service
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    get_settings.cache_clear()
