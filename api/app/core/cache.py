"""Client Redis : cache de réponses et rate-limit par IP."""

from __future__ import annotations

import hashlib
import re
import unicodedata

import redis.asyncio as redis

from app.core.config import Settings

_ESPACES = re.compile(r"\s+")
_PONCTUATION = re.compile(r"[^\w\s]")


def _normaliser_question(question: str) -> str:
    """Normalise une question pour maximiser les correspondances de cache.

    Insensible à la casse, aux accents, à la ponctuation et aux espaces multiples :
    « Quand récolter ? » et « quand recolter » tapent ainsi la même entrée.

    Args:
        question: Question brute du producteur.

    Returns:
        Forme canonique servant de base à la clé de cache.
    """
    sans_accents = "".join(
        c for c in unicodedata.normalize("NFKD", question) if not unicodedata.combining(c)
    )
    sans_ponctuation = _PONCTUATION.sub(" ", sans_accents.lower())
    return _ESPACES.sub(" ", sans_ponctuation).strip()


class CacheClient:
    """Encapsule Redis pour le cache de réponses et le rate-limit.

    Le client tolère l'absence de Redis : en cas d'erreur de connexion, le cache
    est ignoré (miss) et le rate-limit laisse passer, afin de ne pas rendre l'API
    indisponible pour une panne du cache.
    """

    _CACHE_PREFIX = "cache:chat:"
    _RATE_PREFIX = "rate:"
    _CACHE_TTL_S = 604_800  # 7 jours : les conseils agronomiques sont stables.

    def __init__(
        self, client: redis.Redis, rate_limit_per_min: int, model_version: str = ""
    ) -> None:
        """Initialise le client de cache.

        Args:
            client: Connexion Redis asynchrone.
            rate_limit_per_min: Limite de requêtes par minute et par IP.
            model_version: Version du modèle, incluse dans la clé de cache pour
                invalider automatiquement le cache au redéploiement d'un modèle
                (plus de réponses périmées resservies, sans purge manuelle).
        """
        self._redis = client
        self._rate_limit = rate_limit_per_min
        self._model_version = model_version

    @classmethod
    def from_settings(cls, settings: Settings) -> CacheClient:
        """Construit un client à partir des paramètres applicatifs."""
        client = redis.from_url(settings.redis_url, decode_responses=True)
        return cls(client, settings.rate_limit_per_min, settings.model_version)

    @staticmethod
    def _cache_key(question: str, langue: str, model_version: str = "") -> str:
        base = f"{model_version}:{langue}:{_normaliser_question(question)}"
        digest = hashlib.sha256(base.encode()).hexdigest()
        return f"{CacheClient._CACHE_PREFIX}{digest}"

    async def get_cached(self, question: str, langue: str) -> str | None:
        """Retourne la réponse JSON en cache pour la question, ou None."""
        try:
            return await self._redis.get(self._cache_key(question, langue, self._model_version))
        except redis.RedisError:
            return None

    async def set_cached(self, question: str, langue: str, payload: str) -> None:
        """Met en cache la réponse JSON sérialisée pour la question."""
        try:
            await self._redis.set(
                self._cache_key(question, langue, self._model_version),
                payload,
                ex=self._CACHE_TTL_S,
            )
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
