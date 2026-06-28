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
        """Retourne la réponse en cache (correspondance exacte), ou None."""
        ...

    async def set_cached(self, question: str, langue: str, payload: str) -> None:
        """Met une réponse en cache."""
        ...

    async def get_semantic(
        self, langue: str, embedding: list[float], threshold: float
    ) -> tuple[str, str] | None:
        """Retourne ``(payload, question)`` d'une entrée cachée proche, ou None."""
        ...

    async def index_semantic(self, question: str, langue: str, embedding: list[float]) -> None:
        """Indexe le vecteur d'une question cachée (recherche par paraphrase)."""
        ...

    async def hit_rate_limit(self, client_ip: str) -> bool:
        """Incrémente le compteur et indique si la limite est dépassée."""
        ...

    async def ping(self) -> bool:
        """Indique si le cache est disponible."""
        ...


@runtime_checkable
class EmbeddingsPort(Protocol):
    """Contrat d'un service de vectorisation (embeddings denses)."""

    async def embed(self, textes: list[str]) -> list[list[float]] | None:
        """Vectorise une liste de textes. Retourne None si le service échoue."""
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
class AuthStorePort(Protocol):
    """Contrat du stockage de l'authentification par lien magique (D2)."""

    async def creer_lien(self, email: str, token_hash: str, expire_le: object) -> None:
        """Enregistre un lien magique (jeton haché) pour un email."""
        ...

    async def consommer_lien(self, token_hash: str) -> str | None:
        """Valide et consomme un lien (usage unique). Renvoie l'email, ou None."""
        ...

    async def compte_pour(self, email: str) -> str:
        """Retourne l'identifiant de compte de l'email (créé s'il n'existe pas)."""
        ...

    async def purger_liens_expires(self) -> int:
        """Supprime les liens expirés/utilisés. Renvoie le nombre supprimé."""
        ...


@runtime_checkable
class LienNotifierPort(Protocol):
    """Contrat d'acheminement d'un lien magique vers l'utilisateur (D2)."""

    async def envoyer_lien(self, email: str, lien: str) -> None:
        """Achemine le lien (console, SMTP…) vers l'adresse donnée."""
        ...


@runtime_checkable
class SessionStorePort(Protocol):
    """Contrat du stockage durable des sessions de conversation (V2)."""

    async def creer_session(self, *args: object, **kwargs: object) -> Session:
        """Crée une session vide et retourne ses métadonnées."""
        ...

    async def obtenir_session(
        self, session_id: str, proprietaire: str | None = ...
    ) -> Session | None:
        """Retourne les métadonnées d'une session, ou None si inconnue/non possédée."""
        ...

    async def obtenir_session_avec_messages(
        self, session_id: str, proprietaire: str | None = ...
    ) -> SessionAvecMessages | None:
        """Retourne une session et tous ses messages, ou None si inconnue/non possédée."""
        ...

    async def lister_sessions(
        self, limite: int = ..., decalage: int = ..., proprietaire: str = ...
    ) -> list[Session]:
        """Liste les sessions d'un appareil, de la plus récemment active à la plus ancienne."""
        ...

    async def rechercher_sessions(
        self, requete: str, proprietaire: str = ..., limite: int = ...
    ) -> list[Session]:
        """Recherche plein-texte (titre + messages) dans les conversations d'un appareil."""
        ...

    async def renommer_session(
        self, session_id: str, titre: str, proprietaire: str | None = ...
    ) -> bool:
        """Renomme une session. True si elle existait (et appartient à l'appareil)."""
        ...

    async def supprimer_session(self, session_id: str, proprietaire: str | None = ...) -> bool:
        """Supprime une session et ses messages. True si elle existait (et possédée)."""
        ...

    async def ajouter_message(
        self, session_id: str, role: str, content: str
    ) -> ConversationMessage | None:
        """Ajoute un message à une session, ou None si la session n'existe pas."""
        ...

    async def lister_messages(self, session_id: str) -> list[ConversationMessage]:
        """Retourne les messages d'une session, du plus ancien au plus récent."""
        ...

    async def purger_anciennes(self, jours: int) -> int:
        """Supprime les conversations inactives depuis plus de ``jours`` (RGPD). Renvoie
        le nombre supprimé."""
        ...
