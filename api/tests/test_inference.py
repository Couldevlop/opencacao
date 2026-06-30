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
    return InferenceClient("http://inference:8000", "opencacao-8b", 10.0, client=http)


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


async def test_max_tokens_configurable_envoye_dans_le_payload() -> None:
    """Le plafond max_tokens du client est transmis à l'inférence."""
    vu: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        vu["max_tokens"] = json.loads(request.content)["max_tokens"]
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url="http://inference:8000")
    client = InferenceClient(
        "http://inference:8000", "opencacao-8b", 10.0, max_tokens=256, client=http
    )
    await client.generer("Comment sécher mes fèves ?")
    assert vu["max_tokens"] == 256  # valeur du client par défaut
    await client.close()


async def test_params_decodage_envoyes_dans_le_payload() -> None:
    """Température, top_p et frequency_penalty configurés sont transmis à l'inférence."""
    vu: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        vu.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url="http://inference:8000")
    client = InferenceClient(
        "http://inference:8000",
        "opencacao-8b",
        10.0,
        temperature=0.15,
        top_p=0.85,
        frequency_penalty=0.4,
        client=http,
    )
    await client.generer("Comment sécher mes fèves ?")
    assert vu["temperature"] == 0.15
    assert vu["top_p"] == 0.85
    assert vu["frequency_penalty"] == 0.4
    await client.close()


# --- Streaming (generer_stream) ---

_SSE = (
    'data: {"choices":[{"delta":{"content":"Étalez"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":" les fèves"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":" au soleil."}}]}\n\n'
    "data: [DONE]\n\n"
)


async def test_generer_stream_assemble_les_deltas() -> None:
    """Les fragments SSE sont restitués dans l'ordre, [DONE] arrête le flux."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=_SSE.encode(), headers={"content-type": "text/event-stream"}
        )

    client = _client(handler)
    morceaux = [m async for m in client.generer_stream("Comment sécher mes fèves ?")]
    assert "".join(morceaux) == "Étalez les fèves au soleil."
    await client.close()


async def test_generer_stream_erreur_http_leve_indisponible() -> None:
    """Une erreur HTTP pendant le flux lève InferenceUnavailable."""
    client = _client(lambda req: httpx.Response(500, json={"error": "boom"}))
    with pytest.raises(InferenceUnavailable):
        _ = [m async for m in client.generer_stream("Question ?")]
    await client.close()


def test_inference_max_tokens_defaut_400() -> None:
    # Plafond de sécurité aligné sur la cible du chantier latence (réponses concises).
    assert Settings().inference_max_tokens == 400


async def test_cache_prompt_envoye_dans_le_payload() -> None:
    """Le flag cache_prompt est transmis à l'inférence (réutilisation du préfixe système)."""
    vu: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        vu.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url="http://inference:8000")
    client = InferenceClient("http://inference:8000", "opencacao-8b", 10.0, client=http)
    await client.generer("Question ?")
    assert vu.get("cache_prompt") is True
    await client.close()


async def test_cache_prompt_envoye_dans_le_payload_stream() -> None:
    """cache_prompt est aussi transmis sur le chemin streaming."""
    vu: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        vu.update(json.loads(request.content))
        return httpx.Response(200, text=_SSE, headers={"content-type": "text/event-stream"})

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url="http://inference:8000")
    client = InferenceClient("http://inference:8000", "opencacao-8b", 10.0, client=http)
    _ = [m async for m in client.generer_stream("Question ?")]
    assert vu.get("cache_prompt") is True
    await client.close()
