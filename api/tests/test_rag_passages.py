"""Tests de l'extraction des passages RAG (support de la provenance, C3)."""

from __future__ import annotations

import numpy as np

from app.services.rag import Passage, RagIndex, RagRecuperateur


class FauxEmbeddings:
    """Service d'embeddings contrôlé par le test (aucun réseau)."""

    def __init__(self, vecteur: list[float] | None = None) -> None:
        self.vecteur = vecteur

    async def embed(self, textes: list[str]) -> list[list[float]] | None:
        return None if self.vecteur is None else [self.vecteur]


def _index() -> RagIndex:
    return RagIndex(
        [
            "La production ivoirienne de cacao avoisine 2,2 millions de tonnes.",
            "Le prix bord-champ du cacao est fixé par campagne.",
        ],
        ["CNRA", "ICCO"],
        np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32),
    )


def _recuperateur(embeddings: FauxEmbeddings) -> RagRecuperateur:
    return RagRecuperateur(embeddings, _index(), top_k=2, seuil=0.0, hybride=False)


async def test_les_passages_portent_leur_source():
    """C est CETTE source qui deviendra la provenance d une affirmation."""
    passages = await _recuperateur(FauxEmbeddings([1.0, 0.0])).passages_pour("production cacao")
    assert passages
    assert all(isinstance(passage, Passage) for passage in passages)
    assert passages[0].source == "CNRA"


async def test_sans_embeddings_aucun_passage():
    """Service d embeddings absent : on ne fabrique pas de contexte."""
    assert await _recuperateur(FauxEmbeddings(None)).passages_pour("production") == []


async def test_le_contexte_reste_construit_a_partir_des_memes_passages():
    """Non-regression : contexte_pour ne change pas de comportement."""
    recuperateur = _recuperateur(FauxEmbeddings([1.0, 0.0]))
    passages = await recuperateur.passages_pour("production cacao")
    contexte = await recuperateur.contexte_pour("production cacao")
    assert contexte is not None
    assert passages[0].texte in contexte


async def test_sans_passage_le_contexte_est_none():
    assert await _recuperateur(FauxEmbeddings(None)).contexte_pour("production") is None
