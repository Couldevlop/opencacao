"""Tests du client d'embeddings (EmbeddingsClient, transport simulé)."""

from __future__ import annotations

import httpx

from app.core.config import Settings
from app.services.embeddings import EmbeddingsClient


def _client(handler) -> EmbeddingsClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url="http://embeddings:8001")
    return EmbeddingsClient("http://embeddings:8001", 10.0, client=http)


async def test_embed_nominal() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/embeddings"
        return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2, 0.3]}]})

    client = _client(handler)
    vecteurs = await client.embed(["Quand récolter ?"])
    assert vecteurs == [[0.1, 0.2, 0.3]]
    await client.close()


async def test_embed_liste_vide() -> None:
    client = _client(lambda req: httpx.Response(200, json={"data": []}))
    assert await client.embed([]) == []
    await client.close()


async def test_embed_tolere_la_panne() -> None:
    client = _client(lambda req: httpx.Response(500, json={"error": "boom"}))
    assert await client.embed(["q"]) is None  # tolérant : pas d'exception
    await client.close()


def test_from_settings() -> None:
    client = EmbeddingsClient.from_settings(Settings())
    assert isinstance(client, EmbeddingsClient)
