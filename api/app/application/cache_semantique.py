"""Cache sémantique partagé : couche embeddings + garde-fou lexical (DRY V2/V3).

Encapsule la logique de cache par similarité — vectorisation de la question, recherche
d'un voisin proche (cosinus ≥ seuil) validé par un garde-fou lexical, puis indexation.
Réutilisé par ``ConseilService`` (V2) et l'orchestrateur (V3) : une seule source de
vérité pour une politique de cache identique.

Inerte si ``embeddings`` est None : ``vecteur``/``hit`` renvoient None et ``indexer`` ne
fait rien. L'appelant tient donc toujours une instance (pas de ``None`` à tester partout),
simplement neutre quand la couche sémantique est désactivée.
"""

from __future__ import annotations

import json

from app.core.logging import get_logger
from app.domain.ports import CachePort, EmbeddingsPort
from app.services.rag import couverture_lexicale

logger = get_logger(__name__)


class CacheSemantique:
    """Couche de cache sémantique (tour unique). Neutre si ``embeddings`` est None."""

    def __init__(
        self,
        cache: CachePort,
        embeddings: EmbeddingsPort | None,
        seuil: float = 0.92,
        seuil_lexical: float = 0.75,
    ) -> None:
        """Initialise la couche.

        Args:
            cache: Port de cache (expose ``get_semantic``/``index_semantic``).
            embeddings: Service de vectorisation, ou None pour désactiver la couche.
            seuil: Similarité cosinus minimale pour servir un voisin (``get_semantic``).
            seuil_lexical: Couverture lexicale minimale (garde-fou anti-faux-positif).
        """
        self._cache = cache
        self._embeddings = embeddings
        self._seuil = seuil
        self._seuil_lexical = seuil_lexical

    async def vecteur(self, question: str, historique: list[dict[str, str]]) -> list[float] | None:
        """Embedding de la question, ou None (couche off, multi-tours, ou échec).

        Le cache sémantique ne sert qu'en tour unique : une réponse multi-tours dépend
        du contexte conversationnel et ne doit ni servir ni polluer le cache.
        """
        if self._embeddings is None or historique:
            return None
        vecteurs = await self._embeddings.embed([question])
        return vecteurs[0] if vecteurs else None

    async def hit(self, question: str, langue: str, embedding: list[float] | None) -> dict | None:
        """Paquet caché sémantiquement proche ET lexicalement compatible, ou None.

        Deux conditions : similarité cosinus ≥ seuil (assurée par ``get_semantic``) ET
        garde-fou lexical — la question entrante doit reprendre les mots-clés de la
        question cachée. Ce dernier bloque un voisin sémantique au qualificatif divergent
        (« cacaoyer adulte » vs « jeune »), dont la réponse diffère.
        """
        if embedding is None:
            return None
        trouve = await self._cache.get_semantic(langue, embedding, self._seuil)
        if trouve is None:
            return None
        payload, question_cachee = trouve
        if couverture_lexicale(question_cachee, question) < self._seuil_lexical:
            logger.info("cache_semantique_rejet_lexical")
            return None
        return json.loads(payload)

    async def indexer(self, question: str, langue: str, embedding: list[float] | None) -> None:
        """Indexe l'embedding de la question (no-op si ``embedding`` est None)."""
        if embedding is not None:
            await self._cache.index_semantic(question, langue, embedding)
