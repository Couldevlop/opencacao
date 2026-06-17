"""Index RAG en mémoire : recherche par similarité cosinus (NumPy).

Pas de base vectorielle externe : pour un corpus de quelques milliers d'entrées,
un produit scalaire NumPy est instantané et 100 % souverain. L'index (texte +
source + vecteur) est précalculé hors-ligne (build_rag_index.py) et chargé au
démarrage de l'API.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.core.logging import get_logger
from app.services.embeddings import EmbeddingsClient

logger = get_logger(__name__)


@dataclass(frozen=True)
class Passage:
    """Extrait de connaissance retrouvé, avec sa source et son score de pertinence."""

    texte: str
    source: str
    score: float


class RagIndex:
    """Index vectoriel en mémoire (matrice normalisée + métadonnées)."""

    def __init__(self, textes: list[str], sources: list[str], matrice: np.ndarray) -> None:
        self._textes = textes
        self._sources = sources
        self._matrice = matrice  # (N, D), lignes normalisées L2

    @property
    def taille(self) -> int:
        """Nombre d'entrées indexées."""
        return len(self._textes)

    @classmethod
    def charger(cls, chemin: Path) -> RagIndex | None:
        """Charge l'index depuis un JSONL {texte, source, vecteur}. None si absent/vide."""
        if not chemin.exists():
            logger.warning("rag_index_absent", chemin=str(chemin))
            return None
        textes: list[str] = []
        sources: list[str] = []
        vecteurs: list[list[float]] = []
        for ligne in chemin.read_text(encoding="utf-8").splitlines():
            ligne = ligne.strip()
            if not ligne:
                continue
            try:
                enr = json.loads(ligne)
                vecteur = enr["vecteur"]
            except (json.JSONDecodeError, KeyError):
                continue
            textes.append(str(enr.get("texte", "")))
            sources.append(str(enr.get("source", "")))
            vecteurs.append(vecteur)
        if not vecteurs:
            return None
        matrice = np.asarray(vecteurs, dtype=np.float32)
        normes = np.linalg.norm(matrice, axis=1, keepdims=True)
        normes[normes == 0] = 1.0
        matrice = matrice / normes
        logger.info("rag_index_charge", entrees=len(textes), dimension=int(matrice.shape[1]))
        return cls(textes, sources, matrice)

    def rechercher(self, vecteur: list[float], k: int, seuil: float) -> list[Passage]:
        """Retourne les k passages les plus proches dont la similarité dépasse le seuil."""
        requete = np.asarray(vecteur, dtype=np.float32)
        norme = np.linalg.norm(requete)
        if norme == 0 or self._matrice.size == 0:
            return []
        requete = requete / norme
        scores = self._matrice @ requete
        k = min(k, len(scores))
        indices = np.argpartition(-scores, k - 1)[:k]
        indices = indices[np.argsort(-scores[indices])]
        passages: list[Passage] = []
        for i in indices:
            score = float(scores[i])
            if score >= seuil:
                passages.append(Passage(self._textes[i], self._sources[i], score))
        return passages


def formater_contexte(passages: list[Passage]) -> str:
    """Met en forme les passages récupérés pour injection dans le prompt."""
    blocs = []
    for numero, passage in enumerate(passages, start=1):
        source = f" (source : {passage.source})" if passage.source else ""
        blocs.append(f"[{numero}]{source} {passage.texte}")
    return "\n\n".join(blocs)


class RagRecuperateur:
    """Vectorise la question, cherche dans l'index, et formate le contexte."""

    def __init__(
        self, embeddings: EmbeddingsClient, index: RagIndex, top_k: int, seuil: float
    ) -> None:
        self._embeddings = embeddings
        self._index = index
        self._top_k = top_k
        self._seuil = seuil

    async def contexte_pour(self, question: str) -> str | None:
        """Retourne le bloc de contexte pour la question, ou None si rien de pertinent."""
        vecteurs = await self._embeddings.embed([question])
        if not vecteurs:
            return None
        passages = self._index.rechercher(vecteurs[0], self._top_k, self._seuil)
        if not passages:
            return None
        logger.info("rag_contexte", passages=len(passages), meilleur=round(passages[0].score, 3))
        return formater_contexte(passages)
