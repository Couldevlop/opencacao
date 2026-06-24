"""Cas d'usage : conseil avec mémoire serveur (sessions de conversation persistées).

Enveloppe le :class:`ConseilService` « sans état » pour la V2 conversationnelle :
quand un ``session_id`` est fourni, l'historique fait autorité **côté serveur**
(chargé depuis le dépôt, jamais renvoyé par le client) et chaque tour y est persisté.
Sans ``session_id``, le comportement « sans état » historique est conservé tel quel
(le client fournit l'historique) — la V2 reste rétrocompatible avec la V1.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.application.conseil_service import ConseilService
from app.core.logging import get_logger
from app.domain.entities import Conseil
from app.domain.ports import SessionStorePort
from app.models.domain import Langue

logger = get_logger(__name__)


class DialogueSessionService:
    """Oriente le conseil vers la mémoire serveur quand une session est active."""

    def __init__(
        self,
        conseil: ConseilService,
        sessions: SessionStorePort,
        max_messages: int = 200,
    ) -> None:
        """Initialise l'orchestrateur.

        Args:
            conseil: Cas d'usage central du conseil agronomique.
            sessions: Dépôt durable des sessions de conversation.
            max_messages: Nombre de messages récents réinjectés au modèle (fenêtre).
        """
        self._conseil = conseil
        self._sessions = sessions
        self._max_messages = max_messages

    async def _historique_serveur(self, session_id: str) -> list[dict[str, str]]:
        """Reconstitue l'historique d'une session (fenêtre des messages récents)."""
        messages = await self._sessions.lister_messages(session_id)
        recents = messages[-self._max_messages :] if self._max_messages else messages
        return [{"role": m.role, "content": m.content} for m in recents]

    async def _persister_tour(self, session_id: str, question: str, reponse: str) -> None:
        """Enregistre le tour (question utilisateur puis réponse de l'assistant)."""
        await self._sessions.ajouter_message(session_id, "user", question)
        if reponse.strip():
            await self._sessions.ajouter_message(session_id, "assistant", reponse)

    async def conseiller(
        self,
        question: str,
        langue: Langue,
        client_ip: str,
        session_id: str | None = None,
        historique: list[dict[str, str]] | None = None,
    ) -> Conseil | None:
        """Produit un conseil, en mémoire serveur si ``session_id`` est fourni.

        Returns:
            Le conseil produit ; ou ``None`` si un ``session_id`` est fourni mais
            qu'aucune session correspondante n'existe (le routeur le traduit en 404).

        Raises:
            RateLimitDepasse, InferenceUnavailable: propagées par le cas d'usage.
        """
        if session_id is None:
            return await self._conseil.conseiller(question, langue, client_ip, historique)

        if await self._sessions.obtenir_session(session_id) is None:
            return None
        historique_serveur = await self._historique_serveur(session_id)
        conseil = await self._conseil.conseiller(question, langue, client_ip, historique_serveur)
        await self._persister_tour(session_id, question, conseil.reponse)
        return conseil

    async def conseiller_stream(
        self,
        question: str,
        langue: Langue,
        client_ip: str,
        session_id: str | None = None,
        historique: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[dict]:
        """Variante en flux. Persiste le tour à la fin d'un flux complet.

        Émet ``{"type": "error", "kind": "session_inconnue"}`` si le ``session_id``
        fourni n'existe pas. L'événement final ``done`` est enrichi du ``session_id``.
        En cas d'erreur (rate-limit / indisponibilité) levée par le flux interne,
        rien n'est persisté (le tour est resté incomplet).
        """
        if session_id is None:
            async for evenement in self._conseil.conseiller_stream(
                question, langue, client_ip, historique
            ):
                yield evenement
            return

        if await self._sessions.obtenir_session(session_id) is None:
            yield {"type": "error", "kind": "session_inconnue"}
            return

        historique_serveur = await self._historique_serveur(session_id)
        morceaux: list[str] = []
        async for evenement in self._conseil.conseiller_stream(
            question, langue, client_ip, historique_serveur
        ):
            if evenement.get("type") == "token":
                morceaux.append(evenement.get("text", ""))
            elif evenement.get("type") == "done":
                evenement = {**evenement, "session_id": session_id}
            yield evenement

        await self._persister_tour(session_id, question, "".join(morceaux))
