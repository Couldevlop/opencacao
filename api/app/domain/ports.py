"""Ports (interfaces) du domaine — implémentés par l'infrastructure.

Le domaine et l'application dépendent de ces abstractions, jamais des clients
concrets (httpx, redis). C'est l'inversion de dépendance de la clean architecture.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from app.models.session import ConversationMessage, Session, SessionAvecMessages


@runtime_checkable
class InferencePort(Protocol):
    """Contrat d'un moteur d'inférence de langage."""

    async def generer(
        self,
        question: str,
        temperature: float = ...,
        max_tokens: int = ...,
        contexte: str | None = ...,
        historique: list[dict[str, str]] | None = ...,
    ) -> str:
        """Génère une réponse pour la question. Lève InferenceUnavailable si KO."""
        ...

    def generer_stream(
        self,
        question: str,
        temperature: float = ...,
        max_tokens: int = ...,
        contexte: str | None = ...,
        historique: list[dict[str, str]] | None = ...,
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

    async def enregistrer_visite(self, pays: str, continent: str, canal: str) -> None:
        """Enregistre une visite anonymisée (pays + continent + canal, jamais d'IP)."""
        ...


@runtime_checkable
class SessionStorePort(Protocol):
    """Contrat du stockage durable des sessions de conversation (V2)."""

    async def creer_session(self, *args: object, **kwargs: object) -> Session:
        """Crée une session vide et retourne ses métadonnées."""
        ...

    async def obtenir_session(self, session_id: str) -> Session | None:
        """Retourne les métadonnées d'une session, ou None si inconnue."""
        ...

    async def obtenir_session_avec_messages(
        self, session_id: str
    ) -> SessionAvecMessages | None:
        """Retourne une session et tous ses messages, ou None si inconnue."""
        ...

    async def lister_sessions(self, limite: int = ..., decalage: int = ...) -> list[Session]:
        """Liste les sessions, de la plus récemment active à la plus ancienne."""
        ...

    async def renommer_session(self, session_id: str, titre: str) -> bool:
        """Renomme une session. True si elle existait."""
        ...

    async def supprimer_session(self, session_id: str) -> bool:
        """Supprime une session et ses messages. True si elle existait."""
        ...

    async def ajouter_message(
        self, session_id: str, role: str, content: str
    ) -> ConversationMessage | None:
        """Ajoute un message à une session, ou None si la session n'existe pas."""
        ...

    async def lister_messages(self, session_id: str) -> list[ConversationMessage]:
        """Retourne les messages d'une session, du plus ancien au plus récent."""
        ...
