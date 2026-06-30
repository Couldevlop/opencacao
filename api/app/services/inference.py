"""Client vers le service d'inférence (API OpenAI-compatible)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.exceptions import InferenceUnavailable
from app.services.prompts import build_messages

logger = get_logger(__name__)

__all__ = ["InferenceClient", "InferenceUnavailable"]


class InferenceClient:
    """Appelle le service d'inférence via /v1/chat/completions.

    vLLM comme llama-cpp-python exposent une API compatible OpenAI ; ce client est
    donc indépendant du backend choisi.
    """

    def __init__(
        self,
        base_url: str,
        model_name: str,
        timeout_s: float,
        max_tokens: int = 512,
        temperature: float = 0.2,
        top_p: float = 0.9,
        frequency_penalty: float = 0.3,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Initialise le client d'inférence.

        Args:
            base_url: URL de base du service d'inférence.
            model_name: Nom du modèle à demander.
            timeout_s: Timeout des requêtes, en secondes.
            max_tokens: Plafond de génération par défaut (réglable).
            temperature: Température d'échantillonnage par défaut (basse = ancré).
            top_p: Noyau de probabilité (nucleus sampling).
            frequency_penalty: Pénalité de répétition (réduit le remplissage).
            client: Client httpx injectable (pour les tests).
        """
        self._base_url = base_url.rstrip("/")
        self._model_name = model_name
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._top_p = top_p
        self._frequency_penalty = frequency_penalty
        self._client = client or httpx.AsyncClient(timeout=timeout_s)

    @classmethod
    def from_settings(cls, settings: Settings) -> InferenceClient:
        """Construit un client à partir des paramètres applicatifs."""
        return cls(
            base_url=settings.inference_url,
            model_name=settings.model_name,
            timeout_s=settings.request_timeout_s,
            max_tokens=settings.inference_max_tokens,
            temperature=settings.inference_temperature,
            top_p=settings.inference_top_p,
            frequency_penalty=settings.inference_frequency_penalty,
        )

    def _params_decodage(self, temperature: float | None) -> dict:
        """Paramètres de décodage communs aux deux modes (complet et flux)."""
        return {
            "temperature": self._temperature if temperature is None else temperature,
            "top_p": self._top_p,
            "frequency_penalty": self._frequency_penalty,
        }

    async def generer(
        self,
        question: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        contexte: str | None = None,
        historique: list[dict[str, str]] | None = None,
    ) -> str:
        """Génère une réponse agronomique pour la question donnée.

        Args:
            question: Question du producteur.
            temperature: Température d'échantillonnage.
            max_tokens: Nombre maximum de tokens générés.
            contexte: Extraits récupérés (RAG) à injecter, ou None.
            historique: Tours précédents de la conversation, ou None.

        Returns:
            Le texte de la réponse du modèle.

        Raises:
            InferenceUnavailable: Si l'inférence échoue ou répond mal.
        """
        payload = {
            "model": self._model_name,
            "messages": build_messages(question, contexte, historique),
            "max_tokens": max_tokens if max_tokens is not None else self._max_tokens,
            # Réutilise le KV du préfixe commun (message système constant) d'une requête
            # à l'autre -> le prompt système n'est plus re-prérempli (latence CPU).
            "cache_prompt": True,
            **self._params_decodage(temperature),
        }
        try:
            response = await self._client.post(
                f"{self._base_url}/v1/chat/completions", json=payload
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"].strip()
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            logger.error("inference_error", error=str(exc))
            raise InferenceUnavailable("Service d'inférence indisponible") from exc

    async def generer_stream(
        self,
        question: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        contexte: str | None = None,
        historique: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[str]:
        """Génère une réponse en flux (SSE), morceau par morceau.

        Args:
            question: Question du producteur.
            temperature: Température d'échantillonnage.
            max_tokens: Nombre maximum de tokens générés.
            contexte: Extraits récupérés (RAG) à injecter, ou None.
            historique: Tours précédents de la conversation, ou None.

        Yields:
            Les fragments de texte (deltas) au fur et à mesure de la génération.

        Raises:
            InferenceUnavailable: Si l'inférence échoue ou répond mal.
        """
        payload = {
            "model": self._model_name,
            "messages": build_messages(question, contexte, historique),
            "max_tokens": max_tokens if max_tokens is not None else self._max_tokens,
            "stream": True,
            "cache_prompt": True,
            **self._params_decodage(temperature),
        }
        try:
            async with self._client.stream(
                "POST", f"{self._base_url}/v1/chat/completions", json=payload
            ) as response:
                response.raise_for_status()
                async for ligne in response.aiter_lines():
                    if not ligne.startswith("data:"):
                        continue
                    donnees = ligne[len("data:") :].strip()
                    if donnees == "[DONE]":
                        break
                    try:
                        delta = json.loads(donnees)["choices"][0]["delta"].get("content")
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
                    if delta:
                        yield delta
        except httpx.HTTPError as exc:
            logger.error("inference_stream_error", error=str(exc))
            raise InferenceUnavailable("Service d'inférence indisponible") from exc

    async def ready(self) -> bool:
        """Indique si le service d'inférence répond (readiness)."""
        try:
            response = await self._client.get(f"{self._base_url}/health")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def close(self) -> None:
        """Ferme le client HTTP sous-jacent."""
        await self._client.aclose()
