"""Client Redis : cache de réponses (exact + sémantique) et rate-limit par IP."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata

import redis.asyncio as redis

from app.core.config import Settings

_ESPACES = re.compile(r"\s+")
_PONCTUATION = re.compile(r"[^\w\s]")


def _cosinus(a: list[float], b: list[float]) -> float:
    """Similarité cosinus entre deux vecteurs. 0.0 si l'un est nul ou de taille ≠."""
    if len(a) != len(b):
        return 0.0
    produit = sum(x * y for x, y in zip(a, b, strict=True))
    norme_a = math.sqrt(sum(x * x for x in a))
    norme_b = math.sqrt(sum(y * y for y in b))
    if norme_a == 0.0 or norme_b == 0.0:
        return 0.0
    return produit / (norme_a * norme_b)


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
    _SEMIDX_PREFIX = "semidx:chat:"
    _RATE_PREFIX = "rate:"
    _CACHE_TTL_S = 604_800  # 7 jours : les conseils agronomiques sont stables.

    def __init__(
        self,
        client: redis.Redis,
        rate_limit_per_min: int,
        model_version: str = "",
        app_version: str = "",
        semantic_max_entries: int = 2000,
    ) -> None:
        """Initialise le client de cache.

        Args:
            client: Connexion Redis asynchrone.
            rate_limit_per_min: Limite de requêtes par minute et par IP.
            model_version: Version du modèle, incluse dans la clé de cache pour
                invalider automatiquement le cache au redéploiement d'un modèle.
            app_version: Version de l'image API, également incluse dans la clé : un
                déploiement qui change le post-traitement (extraction de sources,
                RAG…) n'aille pas resservir d'anciennes réponses devenues fausses.
            semantic_max_entries: Plafond d'entrées de l'index sémantique par
                (version, langue), pour borner la taille et le coût du balayage.
        """
        self._redis = client
        self._rate_limit = rate_limit_per_min
        self._model_version = model_version
        self._app_version = app_version
        self._semantic_max_entries = semantic_max_entries

    @classmethod
    def from_settings(cls, settings: Settings) -> CacheClient:
        """Construit un client à partir des paramètres applicatifs."""
        client = redis.from_url(settings.redis_url, decode_responses=True)
        return cls(
            client,
            settings.rate_limit_per_min,
            settings.model_version,
            settings.app_version,
            settings.semantic_cache_max_entries,
        )

    @staticmethod
    def _cache_key(
        question: str, langue: str, model_version: str = "", app_version: str = ""
    ) -> str:
        base = f"{app_version}:{model_version}:{langue}:{_normaliser_question(question)}"
        digest = hashlib.sha256(base.encode()).hexdigest()
        return f"{CacheClient._CACHE_PREFIX}{digest}"

    async def get_cached(self, question: str, langue: str) -> str | None:
        """Retourne la réponse JSON en cache pour la question, ou None."""
        try:
            return await self._redis.get(
                self._cache_key(question, langue, self._model_version, self._app_version)
            )
        except redis.RedisError:
            return None

    async def set_cached(self, question: str, langue: str, payload: str) -> None:
        """Met en cache la réponse JSON sérialisée pour la question."""
        try:
            await self._redis.set(
                self._cache_key(question, langue, self._model_version, self._app_version),
                payload,
                ex=self._CACHE_TTL_S,
            )
        except redis.RedisError:
            return

    def _semidx_key(self, langue: str) -> str:
        """Clé du HASH d'index sémantique, cloisonné par (versions, langue)."""
        return f"{self._SEMIDX_PREFIX}{self._app_version}:{self._model_version}:{langue}"

    async def index_semantic(self, question: str, langue: str, embedding: list[float]) -> None:
        """Indexe le vecteur d'une question cachée, pour la recherche par paraphrase.

        Le champ du HASH est la clé de cache exacte : un hit sémantique permet ainsi
        de récupérer directement le payload existant. L'index est plafonné par
        ``semantic_max_entries`` (éviction d'une entrée arbitraire au-delà).

        Args:
            question: Question dont la réponse vient d'être cachée.
            langue: Langue de la réponse.
            embedding: Vecteur dense de la question.
        """
        bucket = self._semidx_key(langue)
        champ = self._cache_key(question, langue, self._model_version, self._app_version)
        try:
            if await self._redis.hlen(bucket) >= self._semantic_max_entries:
                existants = await self._redis.hgetall(bucket)
                for ancien in list(existants)[
                    : max(1, len(existants) - self._semantic_max_entries + 1)
                ]:
                    await self._redis.hdel(bucket, ancien)
            # On stocke le vecteur ET la question : un hit sémantique pourra ainsi
            # être confirmé par un garde-fou lexical côté application.
            await self._redis.hset(bucket, champ, json.dumps({"e": embedding, "q": question}))
            await self._redis.expire(bucket, self._CACHE_TTL_S)
        except redis.RedisError:
            return

    async def get_semantic(
        self, langue: str, embedding: list[float], threshold: float
    ) -> tuple[str, str] | None:
        """Retourne ``(payload, question)`` d'une entrée cachée proche, ou None.

        Balaie l'index ``(versions, langue)``, retient la meilleure similarité cosinus
        et ne renvoie l'entrée que si elle atteint ``threshold``. La question est
        renvoyée pour permettre un garde-fou lexical côté application. Une entrée dont
        le payload a expiré (TTL) est purgée et ignorée.

        Args:
            langue: Langue de la requête.
            embedding: Vecteur dense de la question entrante.
            threshold: Similarité cosinus minimale pour retenir une correspondance.

        Returns:
            ``(payload JSON, question cachée)`` de la meilleure correspondance, ou None.
        """
        bucket = self._semidx_key(langue)
        try:
            entrees = await self._redis.hgetall(bucket)
            meilleure_cle: str | None = None
            meilleure_question = ""
            meilleure_sim = threshold
            for cle, valeur_json in entrees.items():
                entree = json.loads(valeur_json)
                sim = _cosinus(embedding, entree["e"])
                if sim >= meilleure_sim:
                    meilleure_sim = sim
                    meilleure_cle = cle
                    meilleure_question = entree["q"]
            if meilleure_cle is None:
                return None
            payload = await self._redis.get(meilleure_cle)
            if payload is None:  # entrée orpheline (payload expiré) -> purge
                await self._redis.hdel(bucket, meilleure_cle)
                return None
            return payload, meilleure_question
        except (redis.RedisError, ValueError, KeyError):
            return None

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
