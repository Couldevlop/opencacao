"""Ports (interfaces) du domaine — implémentés par l'infrastructure.

Le domaine et l'application dépendent de ces abstractions, jamais des clients
concrets (httpx, redis). C'est l'inversion de dépendance de la clean architecture.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable


@runtime_checkable
class InferencePort(Protocol):
    """Contrat d'un moteur d'inférence de langage."""

    async def generer(
        self,
        question: str,
        temperature: float = ...,
        max_tokens: int = ...,
        contexte: str | None = ...,
    ) -> str:
        """Génère une réponse pour la question. Lève InferenceUnavailable si KO."""
        ...

    def generer_stream(
        self,
        question: str,
        temperature: float = ...,
        max_tokens: int = ...,
        contexte: str | None = ...,
    ) -> AsyncIterator[str]:
        """Génère une réponse en flux (deltas). Lève InferenceUnavailable si KO."""
        ...

    async def ready(self) -> bool:
        """Indique si le moteur est prêt à répondre."""
        ...


@runtime_checkable
class CachePort(Protocol):
    """Contrat d'un cache de réponses avec rate-limit."""

    async def get_cached(self, question: str, langue: str) -> str | None:
        """Retourne la réponse en cache, ou None."""
        ...

    async def set_cached(self, question: str, langue: str, payload: str) -> None:
        """Met une réponse en cache."""
        ...

    async def hit_rate_limit(self, client_ip: str) -> bool:
        """Incrémente le compteur et indique si la limite est dépassée."""
        ...

    async def ping(self) -> bool:
        """Indique si le cache est disponible."""
        ...


@runtime_checkable
class JournalPort(Protocol):
    """Contrat de journalisation des interactions (jeu de données d'amélioration)."""

    async def enregistrer_interaction(
        self,
        question: str,
        langue: str,
        reponse: str,
        confiance: str,
        sources: list[str],
        redirection_anader: bool,
    ) -> str:
        """Enregistre une interaction (anonymisée) et retourne son identifiant."""
        ...

    async def enregistrer_feedback(self, interaction_id: str, vote: str) -> None:
        """Enregistre un retour utilisateur (👍/👎) pour une interaction."""
        ...
