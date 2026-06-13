"""Tests unitaires du client d'inférence (InferenceClient).

Le service d'inférence est simulé via httpx.MockTransport — aucun appel réseau.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.config import Settings
from app.domain.exceptions import InferenceUnavailable
from app.services.inference import InferenceClient


def _client(handler) -> InferenceClient:
    """Construit un InferenceClient dont le transport HTTP est simulé."""
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url="http://inference:8000")
    return InferenceClient("http://inference:8000", "opencacao-7b", 10.0, client=http)


async def test_generer_reponse_nominale() -> None:
    """Une réponse OpenAI bien formée est renvoyée nettoyée."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "  Étalez les fèves au soleil.  "}}]},
        )

    client = _client(handler)
    texte = await client.generer("Comment sécher mes fèves ?")
    assert texte == "Étalez les fèves au soleil."
    await client.close()


async def test_generer_erreur_http_leve_indisponible() -> None:
    """Une erreur HTTP 500 lève InferenceUnavailable."""
    client = _client(lambda req: httpx.Response(500, json={"error": "boom"}))
    with pytest.raises(InferenceUnavailable):
        await client.generer("Question ?")
    await client.close()


async def test_generer_reponse_malformee_leve_indisponible() -> None:
    """Une réponse sans la structure attendue lève InferenceUnavailable."""
    client = _client(lambda req: httpx.Response(200, json={"unexpected": True}))
    with pytest.raises(InferenceUnavailable):
        await client.generer("Question ?")
    await client.close()


async def test_ready_vrai_si_200() -> None:
    """ready renvoie True si /health répond 200."""
    client = _client(lambda req: httpx.Response(200, json={"status": "ok"}))
    assert await client.ready() is True
    await client.close()


async def test_ready_faux_si_statut_non_200() -> None:
    """ready renvoie False si /health répond autre chose que 200."""
    client = _client(lambda req: httpx.Response(503))
    assert await client.ready() is False
    await client.close()


async def test_ready_faux_si_erreur_reseau() -> None:
    """ready renvoie False si la connexion échoue."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = _client(handler)
    assert await client.ready() is False
    await client.close()


def test_from_settings_construit_un_client() -> None:
    """La fabrique construit un InferenceClient depuis les paramètres."""
    client = InferenceClient.from_settings(Settings())
    assert isinstance(client, InferenceClient)
