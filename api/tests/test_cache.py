"""Tests unitaires du client de cache Redis (CacheClient).

Redis est simulé en mémoire ; un client défaillant permet de couvrir les
branches de tolérance aux pannes.
"""

from __future__ import annotations

import redis.asyncio as redis

from app.core.cache import CacheClient
from app.core.config import Settings


class FakeRedis:
    """Redis asynchrone simulé, suffisant pour CacheClient."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.counters: dict[str, int] = {}
        self.expirations: dict[str, int] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.closed = False

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    async def incr(self, key: str) -> int:
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    async def expire(self, key: str, seconds: int) -> None:
        self.expirations[key] = seconds

    async def hset(self, key: str, field: str, value: str) -> None:
        self.hashes.setdefault(key, {})[field] = value

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))

    async def hdel(self, key: str, *fields: str) -> None:
        bucket = self.hashes.get(key, {})
        for field in fields:
            bucket.pop(field, None)

    async def hlen(self, key: str) -> int:
        return len(self.hashes.get(key, {}))

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        self.closed = True


class BrokenRedis:
    """Redis qui échoue sur toutes les opérations (panne simulée)."""

    async def get(self, key: str) -> str:
        raise redis.RedisError("down")

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        raise redis.RedisError("down")

    async def incr(self, key: str) -> int:
        raise redis.RedisError("down")

    async def expire(self, key: str, seconds: int) -> None:
        raise redis.RedisError("down")

    async def hset(self, key: str, field: str, value: str) -> None:
        raise redis.RedisError("down")

    async def hgetall(self, key: str) -> dict[str, str]:
        raise redis.RedisError("down")

    async def hdel(self, key: str, *fields: str) -> None:
        raise redis.RedisError("down")

    async def hlen(self, key: str) -> int:
        raise redis.RedisError("down")

    async def ping(self) -> bool:
        raise redis.RedisError("down")

    async def aclose(self) -> None:
        raise redis.RedisError("down")


async def test_set_then_get_cached() -> None:
    """Une réponse mise en cache est relue à l'identique."""
    cache = CacheClient(FakeRedis(), rate_limit_per_min=20)
    await cache.set_cached("Question ?", "fr", '{"reponse": "ok"}')
    assert await cache.get_cached("Question ?", "fr") == '{"reponse": "ok"}'


async def test_get_cached_miss_retourne_none() -> None:
    """Une clé absente retourne None."""
    cache = CacheClient(FakeRedis(), rate_limit_per_min=20)
    assert await cache.get_cached("inconnue", "fr") is None


async def test_rate_limit_pose_expiration_au_premier_appel() -> None:
    """Le premier hit pose une expiration de 60 s sur la clé."""
    fake = FakeRedis()
    cache = CacheClient(fake, rate_limit_per_min=3)
    assert await cache.hit_rate_limit("1.2.3.4") is False
    assert fake.expirations["rate:1.2.3.4"] == 60


async def test_rate_limit_declenche_au_dela_du_seuil() -> None:
    """Au-delà de la limite, hit_rate_limit renvoie True."""
    cache = CacheClient(FakeRedis(), rate_limit_per_min=2)
    resultats = [await cache.hit_rate_limit("9.9.9.9") for _ in range(3)]
    assert resultats == [False, False, True]


async def test_ping_ok() -> None:
    """ping renvoie True quand Redis répond."""
    cache = CacheClient(FakeRedis(), rate_limit_per_min=20)
    assert await cache.ping() is True


async def test_close_appelle_aclose() -> None:
    """close ferme la connexion sous-jacente."""
    fake = FakeRedis()
    cache = CacheClient(fake, rate_limit_per_min=20)
    await cache.close()
    assert fake.closed is True


# --- Tolérance aux pannes : Redis indisponible ---


async def test_get_cached_tolere_panne() -> None:
    cache = CacheClient(BrokenRedis(), rate_limit_per_min=20)
    assert await cache.get_cached("q", "fr") is None


async def test_set_cached_tolere_panne() -> None:
    cache = CacheClient(BrokenRedis(), rate_limit_per_min=20)
    assert await cache.set_cached("q", "fr", "payload") is None


async def test_rate_limit_tolere_panne_laisse_passer() -> None:
    """En cas de panne Redis, on n'applique pas le rate-limit (fail-open)."""
    cache = CacheClient(BrokenRedis(), rate_limit_per_min=20)
    assert await cache.hit_rate_limit("1.1.1.1") is False


async def test_ping_tolere_panne() -> None:
    cache = CacheClient(BrokenRedis(), rate_limit_per_min=20)
    assert await cache.ping() is False


async def test_close_tolere_panne() -> None:
    cache = CacheClient(BrokenRedis(), rate_limit_per_min=20)
    assert await cache.close() is None


def test_from_settings_construit_un_client() -> None:
    """La fabrique construit un CacheClient depuis les paramètres."""
    cache = CacheClient.from_settings(Settings(redis_url="redis://localhost:6379/0"))
    assert isinstance(cache, CacheClient)


def test_cache_key_normalise_casse_accents_ponctuation() -> None:
    """Des formulations proches (casse/accents/ponctuation) tapent la même clé."""
    cle = CacheClient._cache_key
    reference = cle("Quand récolter les cabosses ?", "fr")
    assert cle("quand recolter les cabosses", "fr") == reference
    assert cle("  Quand   RÉCOLTER les cabosses ?!  ", "fr") == reference


def test_cache_key_distingue_questions_et_langues() -> None:
    """Deux questions distinctes — ou deux langues — ont des clés différentes."""
    cle = CacheClient._cache_key
    assert cle("Quand récolter ?", "fr") != cle("Comment tailler ?", "fr")
    assert cle("Quand récolter ?", "fr") != cle("Quand récolter ?", "en")


async def test_cache_hit_malgre_variation_de_forme() -> None:
    """Une réponse mise en cache est relue même si la question varie en forme."""
    cache = CacheClient(FakeRedis(), rate_limit_per_min=20)
    await cache.set_cached("Quand récolter ?", "fr", '{"reponse": "ok"}')
    assert await cache.get_cached("quand  RECOLTER !", "fr") == '{"reponse": "ok"}'


def test_cache_key_depend_du_model_version() -> None:
    """La version du modèle fait partie de la clé (invalidation au redéploiement)."""
    cle = CacheClient._cache_key
    assert cle("Q", "fr", "1.0.0") == cle("Q", "fr", "1.0.0")
    assert cle("Q", "fr", "1.0.0") != cle("Q", "fr", "2.0.0")


async def test_cache_isole_par_model_version() -> None:
    """Un nouveau modèle ne ressert pas les réponses cachées de l'ancien."""
    partage = FakeRedis()
    ancien = CacheClient(partage, rate_limit_per_min=20, model_version="1.0.0")
    nouveau = CacheClient(partage, rate_limit_per_min=20, model_version="2.0.0")
    await ancien.set_cached("Q", "fr", '{"reponse": "ancien"}')
    assert await nouveau.get_cached("Q", "fr") is None  # cache vierge pour le nouveau
    assert await ancien.get_cached("Q", "fr") == '{"reponse": "ancien"}'


def test_cache_key_depend_de_app_version() -> None:
    """La version de l'image API fait aussi partie de la clé (post-traitement modifié)."""
    cle = CacheClient._cache_key
    assert cle("Q", "fr", "1.0.0", "0.6.18") != cle("Q", "fr", "1.0.0", "0.6.19")


async def test_cache_isole_par_app_version() -> None:
    """Un déploiement d'image (post-traitement changé) ne ressert pas l'ancien cache."""
    partage = FakeRedis()
    ancien = CacheClient(partage, rate_limit_per_min=20, app_version="0.6.18")
    nouveau = CacheClient(partage, rate_limit_per_min=20, app_version="0.6.19")
    await ancien.set_cached("Q", "fr", '{"reponse": "ancien"}')
    assert await nouveau.get_cached("Q", "fr") is None


# --- Cache sémantique (similarité d'embeddings) ---


async def test_get_semantic_retrouve_une_paraphrase() -> None:
    """Une question proche (vecteur quasi colinéaire) retrouve réponse ET question cachée."""
    cache = CacheClient(FakeRedis(), rate_limit_per_min=20)
    await cache.set_cached("Comment tailler le cacaoyer ?", "fr", '{"reponse": "Taillez ainsi."}')
    await cache.index_semantic("Comment tailler le cacaoyer ?", "fr", [1.0, 0.0, 0.0])

    # Paraphrase vectorisée presque identique -> cosinus ~1, au-dessus du seuil.
    # get_semantic renvoie (payload, question_cachée) pour permettre le garde-fou lexical.
    trouve = await cache.get_semantic("fr", [0.99, 0.02, 0.0], threshold=0.92)
    assert trouve == ('{"reponse": "Taillez ainsi."}', "Comment tailler le cacaoyer ?")


async def test_get_semantic_sous_le_seuil_retourne_none() -> None:
    """Un vecteur trop différent (sous le seuil) n'est pas servi."""
    cache = CacheClient(FakeRedis(), rate_limit_per_min=20)
    await cache.set_cached("Comment tailler le cacaoyer ?", "fr", '{"reponse": "Taillez."}')
    await cache.index_semantic("Comment tailler le cacaoyer ?", "fr", [1.0, 0.0, 0.0])

    # Vecteur orthogonal -> cosinus 0 -> miss.
    assert await cache.get_semantic("fr", [0.0, 1.0, 0.0], threshold=0.92) is None


async def test_get_semantic_isole_par_langue() -> None:
    """L'index sémantique est cloisonné par langue."""
    cache = CacheClient(FakeRedis(), rate_limit_per_min=20)
    await cache.set_cached("Comment tailler ?", "fr", '{"reponse": "FR"}')
    await cache.index_semantic("Comment tailler ?", "fr", [1.0, 0.0, 0.0])

    assert await cache.get_semantic("en", [1.0, 0.0, 0.0], threshold=0.92) is None


async def test_get_semantic_ignore_une_entree_expiree() -> None:
    """Si le payload a expiré (TTL) mais l'index demeure, on ne sert rien."""
    fake = FakeRedis()
    cache = CacheClient(fake, rate_limit_per_min=20)
    await cache.index_semantic("Comment tailler ?", "fr", [1.0, 0.0, 0.0])  # index sans payload

    assert await cache.get_semantic("fr", [1.0, 0.0, 0.0], threshold=0.92) is None


async def test_index_semantic_plafonne_le_nombre_d_entrees() -> None:
    """Au-delà du plafond, l'index n'enfle pas indéfiniment (éviction)."""
    fake = FakeRedis()
    cache = CacheClient(fake, rate_limit_per_min=20, semantic_max_entries=2)
    for i in range(5):
        await cache.index_semantic(f"Question {i} ?", "fr", [float(i), 1.0, 0.0])
    bucket = next(iter(fake.hashes.values()))
    assert len(bucket) <= 2


async def test_get_semantic_tolere_panne() -> None:
    """Une panne Redis sur l'index sémantique retombe en miss (None)."""
    cache = CacheClient(BrokenRedis(), rate_limit_per_min=20)
    assert await cache.get_semantic("fr", [1.0, 0.0, 0.0], threshold=0.92) is None


async def test_index_semantic_tolere_panne() -> None:
    """Une panne Redis pendant l'indexation est silencieuse (pas de crash)."""
    cache = CacheClient(BrokenRedis(), rate_limit_per_min=20)
    assert await cache.index_semantic("Q ?", "fr", [1.0, 0.0, 0.0]) is None
