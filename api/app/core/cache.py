"""Client Redis : cache de réponses et rate-limit par IP."""

from __future__ import annotations

import hashlib

import redis.asyncio as redis

from app.core.config import Settings


class CacheClient:
    """Encapsule Redis pour le cache de réponses et le rate-limit.

    Le client tolère l'absence de Redis : en cas d'erreur de connexion, le cache
    est ignoré (miss) et le rate-limit laisse passer, afin de ne pas rendre l'API
    indisponible pour une panne du cache.
    """

    _CACHE_PREFIX = "cache:chat:"
    _RATE_PREFIX = "rate:"
    _CACHE_TTL_S = 3600

    def __init__(self, client: redis.Redis, rate_limit_per_min: int) -> None:
        """Initialise le client de cache.

        Args:
            client: Connexion Redis asynchrone.
            rate_limit_per_min: Limite de requêtes par minute et par IP.
        """
        self._redis = client
        self._rate_limit = rate_limit_per_min

    @classmethod
    def from_settings(cls, settings: Settings) -> CacheClient:
        """Construit un client à partir des paramètres applicatifs."""
        client = redis.from_url(settings.redis_url, decode_responses=True)
        return cls(client, settings.rate_limit_per_min)

    @staticmethod
    def _cache_key(question: str, langue: str) -> str:
        digest = hashlib.sha256(f"{langue}:{question}".encode()).hexdigest()
        return f"{CacheClient._CACHE_PREFIX}{digest}"

    async def get_cached(self, question: str, langue: str) -> str | None:
        """Retourne la réponse JSON en cache pour la question, ou None."""
        try:
            return await self._redis.get(self._cache_key(question, langue))
        except redis.RedisError:
            return None

    async def set_cached(self, question: str, langue: str, payload: str) -> None:
        """Met en cache la réponse JSON sérialisée pour la question."""
        try:
            await self._redis.set(self._cache_key(question, langue), payload, ex=self._CACHE_TTL_S)
        except redis.RedisError:
            return

    async def hit_rate_limit(self, client_ip: str) -> bool:
        """Incrémente le compteur de l'IP et indique si la limite est dépassée.

        Args:
            client_ip: Adresse IP du client.

        Returns:
            True si la requête doit être rejetée (limite atteinte).
        """
        key = f"{self._RATE_PREFIX}{client_ip}"
        try:
            count = await self._redis.incr(key)
            if count == 1:
                await self._redis.expire(key, 60)
            return count > self._rate_limit
        except redis.RedisError:
            return False

    async def ping(self) -> bool:
        """Vérifie la disponibilité de Redis."""
        try:
            return bool(await self._redis.ping())
        except redis.RedisError:
            return False

    async def close(self) -> None:
        """Ferme la connexion Redis."""
        try:
            await self._redis.aclose()
        except redis.RedisError:
            return
