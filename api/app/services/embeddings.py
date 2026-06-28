"""Client vers le service d'embeddings (llama.cpp, API OpenAI-compatible).

Sert à vectoriser la question de l'utilisateur au moment de la requête, pour la
recherche RAG. Souverain (modèle local) et sans PyTorch côté API.
"""

from __future__ import annotations

import httpx

from app.core.config import Settings
from app.core.logging import get_logger
from app.services.rag_index_builder import formater_pour_embedding

logger = get_logger(__name__)


class EmbeddingsClient:
    """Appelle /v1/embeddings d'un serveur llama.cpp en mode embeddings."""

    def __init__(
        self, base_url: str, timeout_s: float, client: httpx.AsyncClient | None = None
    ) -> None:
        """Initialise le client.

        Args:
            base_url: URL de base du service d'embeddings.
            timeout_s: Timeout des requêtes, en secondes.
            client: Client httpx injectable (tests).
        """
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=timeout_s)

    @classmethod
    def from_settings(cls, settings: Settings) -> EmbeddingsClient:
        """Construit un client à partir des paramètres applicatifs."""
        return cls(base_url=settings.embeddings_url, timeout_s=settings.request_timeout_s)

    async def embed(self, textes: list[str]) -> list[list[float]] | None:
        """Vectorise une liste de textes. Retourne None si le service échoue.

        Tolérant : une panne d'embeddings ne doit pas casser la requête (on
        retombe simplement sur une génération sans contexte).
        """
        if not textes:
            return []
        # Préfixe d'instruction Qwen3 — identique à celui de l'indexation RAG, sans
        # quoi les vecteurs requête/index ne seraient pas comparables.
        entrees = [formater_pour_embedding(texte) for texte in textes]
        try:
            response = await self._client.post(
                f"{self._base_url}/v1/embeddings",
                json={"input": entrees, "model": "embeddings"},
            )
            response.raise_for_status()
            donnees = response.json()["data"]
            return [item["embedding"] for item in donnees]
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            logger.warning("embeddings_indisponible", error=str(exc))
            return None

    async def close(self) -> None:
        """Ferme le client HTTP sous-jacent."""
        await self._client.aclose()
