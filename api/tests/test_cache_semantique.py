"""Helper de cache sémantique partagé (V2/V3) : vectorisation + garde-fou lexical."""

from __future__ import annotations

import json

import pytest

from app.application.cache_semantique import CacheSemantique


class _EmbeddingsFactice:
    def __init__(self, vecteur: list[float] | None = None, echoue: bool = False) -> None:
        self._vecteur = vecteur if vecteur is not None else [1.0, 0.0]
        self._echoue = echoue
        self.appels: list[list[str]] = []

    async def embed(self, textes: list[str]) -> list[list[float]] | None:
        self.appels.append(textes)
        return None if self._echoue else [self._vecteur]


class _CacheFactice:
    def __init__(self, trouve: tuple[str, str] | None = None) -> None:
        self._trouve = trouve
        self.indexes: list[tuple[str, str, list[float]]] = []

    async def get_semantic(self, langue, embedding, threshold):
        return self._trouve

    async def index_semantic(self, question, langue, embedding) -> None:
        self.indexes.append((question, langue, embedding))


def _paquet(reponse: str = "Taillez en saison sèche.") -> str:
    return json.dumps(
        {"reponse": reponse, "confiance": "moyenne", "sources": [], "redirection": False}
    )


@pytest.mark.asyncio
async def test_vecteur_none_si_embeddings_absent() -> None:
    sem = CacheSemantique(_CacheFactice(), embeddings=None)
    assert await sem.vecteur("comment tailler ?", []) is None


@pytest.mark.asyncio
async def test_vecteur_none_en_multi_tours() -> None:
    emb = _EmbeddingsFactice()
    sem = CacheSemantique(_CacheFactice(), embeddings=emb)
    assert await sem.vecteur("et ensuite ?", [{"role": "user", "content": "x"}]) is None
    assert emb.appels == []  # pas d'embedding calculé en multi-tours


@pytest.mark.asyncio
async def test_vecteur_none_si_embed_echoue() -> None:
    sem = CacheSemantique(_CacheFactice(), embeddings=_EmbeddingsFactice(echoue=True))
    assert await sem.vecteur("comment tailler ?", []) is None


@pytest.mark.asyncio
async def test_vecteur_retourne_l_embedding() -> None:
    sem = CacheSemantique(_CacheFactice(), embeddings=_EmbeddingsFactice([0.2, 0.9]))
    assert await sem.vecteur("comment tailler ?", []) == [0.2, 0.9]


@pytest.mark.asyncio
async def test_hit_none_si_embedding_none() -> None:
    sem = CacheSemantique(_CacheFactice(_paquet()), embeddings=_EmbeddingsFactice())
    assert await sem.hit("comment tailler ?", "fr", None) is None


@pytest.mark.asyncio
async def test_hit_sert_le_paquet_si_lexicalement_compatible() -> None:
    # Question cachée et entrante partagent les mots-clés → servi.
    cache = _CacheFactice((_paquet(), "comment tailler le cacaoyer"))
    sem = CacheSemantique(cache, embeddings=_EmbeddingsFactice(), seuil_lexical=0.5)
    paquet = await sem.hit("comment tailler mon cacaoyer", "fr", [1.0, 0.0])
    assert paquet is not None
    assert paquet["reponse"] == "Taillez en saison sèche."


@pytest.mark.asyncio
async def test_hit_rejete_si_lexicalement_divergent() -> None:
    # Voisin sémantique mais qualificatif divergent → rejeté par le garde-fou lexical.
    cache = _CacheFactice((_paquet(), "comment tailler le cacaoyer adulte"))
    sem = CacheSemantique(cache, embeddings=_EmbeddingsFactice(), seuil_lexical=0.99)
    assert await sem.hit("comment fertiliser le jeune plant", "fr", [1.0, 0.0]) is None


@pytest.mark.asyncio
async def test_hit_none_si_cache_vide() -> None:
    sem = CacheSemantique(_CacheFactice(None), embeddings=_EmbeddingsFactice())
    assert await sem.hit("comment tailler ?", "fr", [1.0, 0.0]) is None


@pytest.mark.asyncio
async def test_indexer_noop_si_embedding_none() -> None:
    cache = _CacheFactice()
    sem = CacheSemantique(cache, embeddings=_EmbeddingsFactice())
    await sem.indexer("comment tailler ?", "fr", None)
    assert cache.indexes == []


@pytest.mark.asyncio
async def test_indexer_enregistre_l_embedding() -> None:
    cache = _CacheFactice()
    sem = CacheSemantique(cache, embeddings=_EmbeddingsFactice())
    await sem.indexer("comment tailler ?", "fr", [0.1, 0.2])
    assert cache.indexes == [("comment tailler ?", "fr", [0.1, 0.2])]


@pytest.mark.asyncio
async def test_instance_inerte_sans_embeddings() -> None:
    # embeddings=None → couche entièrement inerte (vecteur/hit None, indexer no-op).
    cache = _CacheFactice(_paquet())
    sem = CacheSemantique(cache, embeddings=None)
    emb = await sem.vecteur("q", [])
    assert emb is None
    assert await sem.hit("q", "fr", emb) is None
    await sem.indexer("q", "fr", emb)
    assert cache.indexes == []
