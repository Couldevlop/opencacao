"""Fixtures de test : application avec inférence et cache mockés."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api_deps import get_cache_client, get_conseil_service, get_inference_client
from app.application.conseil_service import ConseilService
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

    async def ready(self) -> bool:
        return self.disponible

    async def close(self) -> None:
        return None


@pytest.fixture
def fake_cache() -> FakeCache:
    return FakeCache()


@pytest.fixture
def fake_inference() -> FakeInference:
    return FakeInference()


@pytest.fixture
def client(fake_cache: FakeCache, fake_inference: FakeInference) -> Iterator[TestClient]:
    """Client de test avec inférence et cache surchargés."""
    app = create_app()
    service = ConseilService(inference=fake_inference, cache=fake_cache)
    app.dependency_overrides[get_cache_client] = lambda: fake_cache
    app.dependency_overrides[get_inference_client] = lambda: fake_inference
    app.dependency_overrides[get_conseil_service] = lambda: service
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
