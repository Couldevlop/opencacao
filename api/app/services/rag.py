"""Index RAG en mémoire : recherche par similarité cosinus (NumPy).

Pas de base vectorielle externe : pour un corpus de quelques milliers d'entrées,
un produit scalaire NumPy est instantané et 100 % souverain. L'index (texte +
source + vecteur) est précalculé hors-ligne (build_rag_index.py) et chargé au
démarrage de l'API.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.core.logging import get_logger
from app.services.embeddings import EmbeddingsClient

logger = get_logger(__name__)

# Mots vides français (ignorés au recouvrement lexical du reranking).
_MOTS_VIDES = frozenset(
    "le la les un une des du de da au aux et ou ni car mais donc or que qui quoi dont "
    "ce cet cette ces mon ma mes ton ta tes son sa ses notre nos votre vos leur leurs "
    "a as ai est sont etre ete avec sans sous sur dans pour par en vers chez entre "
    "comment quel quelle quels quelles pourquoi quand combien plus moins tres pas ne "
    "je tu il elle nous vous ils elles on se sa".split()
)
_RE_MOT = re.compile(r"[a-z0-9]{3,}")


def _sans_accents(texte: str) -> str:
    """Minuscule sans accents (pour un appariement lexical robuste)."""
    decompose = unicodedata.normalize("NFD", texte.lower())
    return "".join(c for c in decompose if unicodedata.category(c) != "Mn")


def _tokens(texte: str) -> list[str]:
    """Liste des mots significatifs d'un texte (sans accents, sans mots vides).

    Conserve les répétitions (nécessaire au calcul des fréquences BM25), contrairement
    à :func:`_mots_cles` qui en fait un ensemble.
    """
    return [m for m in _RE_MOT.findall(_sans_accents(texte)) if m not in _MOTS_VIDES]


def _mots_cles(texte: str) -> set[str]:
    """Ensemble des mots significatifs d'un texte (sans accents, sans mots vides)."""
    return set(_tokens(texte))


def recouvrement_lexical(mots_question: set[str], texte: str) -> float:
    """Fraction des mots de la question présents dans le texte (0 à 1)."""
    if not mots_question:
        return 0.0
    return len(mots_question & _mots_cles(texte)) / len(mots_question)


def _radical(mot: str) -> str:
    """Radicalisation légère : retire un -s/-x final (pluriels) pour apparier
    « cacaoyer » et « cacaoyers », « adulte » et « adultes »."""
    return mot[:-1] if len(mot) > 4 and mot[-1] in "sx" else mot


def couverture_lexicale(reference: str, candidat: str) -> float:
    """Fraction des mots-clés (radicalisés) de ``reference`` présents dans ``candidat``.

    Sert de garde-fou au cache sémantique : on n'autorise un hit que si la question
    entrante (``candidat``) reprend les mots porteurs de la question cachée
    (``reference``). Un qualificatif divergent (« adulte » vs « jeune ») fait ainsi
    chuter la couverture et bloque un faux positif.

    Args:
        reference: Question cachée dont on exige la couverture.
        candidat: Question entrante.

    Returns:
        Couverture de 0.0 à 1.0 (1.0 = tous les mots-clés de référence présents).
    """
    ref = {_radical(m) for m in _mots_cles(reference)}
    if not ref:
        return 0.0
    cand = {_radical(m) for m in _mots_cles(candidat)}
    return len(ref & cand) / len(ref)


class _BM25:
    """Index BM25 (sparse) en mémoire — recall lexical complémentaire au dense (F10).

    Implémentation autonome (aucune dépendance hors spec). BM25 Okapi : favorise les
    documents contenant les termes *rares* de la requête (noms de maladies/variétés)
    que la similarité dense peut classer hors du vivier.
    """

    def __init__(self, docs_tokens: list[list[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self._k1 = k1
        self._b = b
        self._n = len(docs_tokens)
        self._dl = [len(t) for t in docs_tokens]
        self._avgdl = (sum(self._dl) / self._n) if self._n else 0.0
        self._tf: list[Counter[str]] = [Counter(t) for t in docs_tokens]
        df: Counter[str] = Counter()
        for tokens in docs_tokens:
            df.update(set(tokens))
        # IDF BM25 (lissé, plancher à 0 pour éviter les contributions négatives).
        self._idf = {
            mot: max(0.0, math.log(1 + (self._n - freq + 0.5) / (freq + 0.5)))
            for mot, freq in df.items()
        }

    @classmethod
    def construire(cls, textes: list[str]) -> _BM25:
        """Construit l'index BM25 en tokenisant chaque document (mêmes règles que F9)."""
        return cls([_tokens(texte) for texte in textes])

    def scores(self, mots_requete: set[str]) -> np.ndarray:
        """Score BM25 de chaque document pour les mots de la requête."""
        res = np.zeros(self._n, dtype=np.float32)
        if self._avgdl == 0:
            return res
        for i in range(self._n):
            tf = self._tf[i]
            dl = self._dl[i]
            total = 0.0
            for mot in mots_requete:
                freq = tf.get(mot, 0)
                if freq == 0:
                    continue
                idf = self._idf.get(mot, 0.0)
                denom = freq + self._k1 * (1 - self._b + self._b * dl / self._avgdl)
                total += idf * freq * (self._k1 + 1) / denom
            res[i] = total
        return res

    def top(self, mots_requete: set[str], n: int) -> list[int]:
        """Indices des n meilleurs documents par score BM25 (score strictement positif)."""
        scores = self.scores(set(mots_requete))
        ordre = [i for i in np.argsort(-scores).tolist() if scores[i] > 0]
        return ordre[:n]


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
        # Index BM25 (F10) : recall lexical sur tout le corpus. On indexe « texte +
        # source » comme le reranking, pour que le nom de source soit aussi cherchable.
        self._bm25 = _BM25.construire(
            [f"{texte} {source}" for texte, source in zip(textes, sources, strict=True)]
        )

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
        # Lecture EN FLUX (ligne à ligne) + conversion immédiate en float32 :
        # évite de garder en mémoire le fichier entier et les listes de floats Python
        # (sinon pic mémoire >> à la taille de l'index -> OOM).
        vecteurs: list[np.ndarray] = []
        with chemin.open(encoding="utf-8") as handle:
            for ligne in handle:
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
                vecteurs.append(np.asarray(vecteur, dtype=np.float32))
        if not vecteurs:
            return None
        matrice = np.vstack(vecteurs)
        normes = np.linalg.norm(matrice, axis=1, keepdims=True)
        normes[normes == 0] = 1.0
        matrice = matrice / normes
        logger.info("rag_index_charge", entrees=len(textes), dimension=int(matrice.shape[1]))
        return cls(textes, sources, matrice)

    def rechercher(self, vecteur: list[float], k: int, seuil: float) -> list[Passage]:
        """Retourne les k passages les plus proches dont la similarité dépasse le seuil."""
        return [p for p in self.candidats(vecteur, k) if p.score >= seuil]

    def candidats(self, vecteur: list[float], n: int) -> list[Passage]:
        """Retourne les n passages les plus proches par cosinus, SANS filtre de seuil.

        Sert de vivier au reranking (F9) : on récupère large par similarité dense,
        puis on réordonne en tenant compte du recouvrement lexical.
        """
        requete = np.asarray(vecteur, dtype=np.float32)
        norme = np.linalg.norm(requete)
        if norme == 0 or self._matrice.size == 0:
            return []
        requete = requete / norme
        scores = self._matrice @ requete
        n = min(n, len(scores))
        indices = np.argpartition(-scores, n - 1)[:n]
        indices = indices[np.argsort(-scores[indices])]
        return [Passage(self._textes[i], self._sources[i], float(scores[i])) for i in indices]

    def vivier_hybride(self, vecteur: list[float], question: str, n: int) -> list[Passage]:
        """Vivier hybride (F10) : union des n meilleurs DENSE et des n meilleurs BM25.

        Le canal dense capte la proximité sémantique ; le canal BM25 rattrape les
        documents lexicalement pertinents (terme rare : maladie, variété) que le dense
        classe hors du vivier. Chaque passage porte son score dense réel (0 si nul) afin
        que le reranking (F9) fusionne dense + lexical de façon homogène.

        Args:
            vecteur: Vecteur dense de la question.
            question: Question (pour le canal BM25 lexical).
            n: Taille de chaque canal avant union.

        Returns:
            Les passages du vivier hybride, triés par score dense décroissant.
        """
        requete = np.asarray(vecteur, dtype=np.float32)
        norme = np.linalg.norm(requete)
        if norme == 0 or self._matrice.size == 0:
            return []
        scores = self._matrice @ (requete / norme)
        n = min(n, len(scores))
        denses = np.argpartition(-scores, n - 1)[:n].tolist()
        bm25 = self._bm25.top(_mots_cles(question), n)
        # Union en préservant l'unicité ; tri final par score dense décroissant.
        indices = sorted(set(denses) | set(bm25), key=lambda i: -scores[i])
        return [Passage(self._textes[i], self._sources[i], float(scores[i])) for i in indices]


def reranker(
    question: str,
    candidats: list[Passage],
    *,
    top_k: int,
    poids_lexical: float,
    seuil_dense: float,
    seuil_lexical: float,
) -> list[Passage]:
    """Réordonne des candidats par score fusionné (dense + recouvrement lexical).

    Un passage est éligible si sa similarité dense atteint ``seuil_dense`` OU si son
    recouvrement lexical atteint ``seuil_lexical`` — ce dernier fait remonter un
    document littéralement pertinent (p. ex. dont le nom de source contient « firca »)
    même quand l'embedding seul le classait trop bas. On garde les ``top_k`` meilleurs.

    Args:
        question: Question du producteur.
        candidats: Passages récupérés par similarité dense (vivier).
        top_k: Nombre de passages à conserver in fine.
        poids_lexical: Poids du recouvrement lexical dans le score fusionné (0–1).
        seuil_dense: Similarité dense minimale pour être éligible.
        seuil_lexical: Recouvrement lexical minimal pour être éligible (voie de secours).

    Returns:
        Les passages retenus, du plus pertinent au moins pertinent.
    """
    mots_question = _mots_cles(question)
    notes: list[tuple[float, Passage]] = []
    for passage in candidats:
        lexical = recouvrement_lexical(mots_question, f"{passage.texte} {passage.source}")
        if passage.score >= seuil_dense or lexical >= seuil_lexical:
            fusion = (1.0 - poids_lexical) * passage.score + poids_lexical * lexical
            notes.append((fusion, passage))
    notes.sort(key=lambda couple: -couple[0])
    return [passage for _, passage in notes[:top_k]]


def formater_contexte(passages: list[Passage]) -> str:
    """Met en forme les passages récupérés pour injection dans le prompt."""
    blocs = []
    for numero, passage in enumerate(passages, start=1):
        source = f" (source : {passage.source})" if passage.source else ""
        blocs.append(f"[{numero}]{source} {passage.texte}")
    return "\n\n".join(blocs)


class RagRecuperateur:
    """Vectorise la question, récupère un vivier, rerank, et formate le contexte."""

    def __init__(
        self,
        embeddings: EmbeddingsClient,
        index: RagIndex,
        top_k: int,
        seuil: float,
        *,
        candidats: int = 12,
        poids_lexical: float = 0.35,
        seuil_lexical: float = 0.5,
        hybride: bool = True,
    ) -> None:
        """Initialise le récupérateur RAG.

        Args:
            embeddings: Client d'embeddings.
            index: Index vectoriel en mémoire.
            top_k: Nombre de passages injectés in fine.
            seuil: Similarité dense minimale (voie principale).
            candidats: Taille du vivier récupéré par similarité dense avant reranking.
            poids_lexical: Poids du recouvrement lexical dans le score fusionné.
            seuil_lexical: Recouvrement lexical minimal (voie de secours du reranking).
        """
        self._embeddings = embeddings
        self._index = index
        self._top_k = top_k
        self._seuil = seuil
        self._candidats = max(candidats, top_k)
        self._poids_lexical = poids_lexical
        self._seuil_lexical = seuil_lexical
        self._hybride = hybride

    async def contexte_pour(self, question: str) -> str | None:
        """Retourne le bloc de contexte pour la question, ou None si rien de pertinent."""
        vecteurs = await self._embeddings.embed([question])
        if not vecteurs:
            return None
        if self._hybride:
            viviers = self._index.vivier_hybride(vecteurs[0], question, self._candidats)
        else:
            viviers = self._index.candidats(vecteurs[0], self._candidats)
        passages = reranker(
            question,
            viviers,
            top_k=self._top_k,
            poids_lexical=self._poids_lexical,
            seuil_dense=self._seuil,
            seuil_lexical=self._seuil_lexical,
        )
        if not passages:
            return None
        logger.info(
            "rag_contexte",
            passages=len(passages),
            meilleur=round(passages[0].score, 3),
            viviers=len(viviers),
        )
        return formater_contexte(passages)
