"""Tests de l'index RAG (RagIndex, RagRecuperateur, formatage)."""

from __future__ import annotations

import json
from pathlib import Path

from app.services.rag import (
    Passage,
    RagIndex,
    RagRecuperateur,
    _mots_cles,
    couverture_lexicale,
    formater_contexte,
    recouvrement_lexical,
    reranker,
)


def test_couverture_lexicale_paraphrase_complete() -> None:
    """Une reformulation qui reprend tous les mots-clés couvre à 100 %."""
    cov = couverture_lexicale(
        "Comment tailler le cacaoyer adulte ?", "De quelle façon tailler mes cacaoyers adultes ?"
    )
    assert cov == 1.0  # cacaoyers/adultes radicalisés -> cacaoyer/adulte


def test_couverture_lexicale_qualificatif_different() -> None:
    """Un qualificatif porteur divergent (adulte vs jeune) fait chuter la couverture."""
    cov = couverture_lexicale(
        "Comment tailler le cacaoyer adulte ?", "Comment tailler un jeune cacaoyer ?"
    )
    assert cov < 0.75  # « adulte » absent de l'entrante


def test_couverture_lexicale_sujet_different() -> None:
    """Deux sujets distincts ne se couvrent quasiment pas."""
    cov = couverture_lexicale(
        "Comment lutter contre la pourriture brune ?", "Comment lutter contre les mirides ?"
    )
    assert cov < 0.6  # « pourriture/brune » absents -> sous le seuil du garde-fou (0,75)


def _ecrire_index(chemin: Path) -> None:
    entrees = [
        {
            "texte": "Récoltez les cabosses mûres. Sources : CNRA.",
            "source": "CNRA",
            "vecteur": [1.0, 0.0, 0.0],
        },
        {
            "texte": "Séchez les fèves au soleil. Sources : ANADER.",
            "source": "ANADER",
            "vecteur": [0.0, 1.0, 0.0],
        },
        {
            "texte": "Taillez le cacaoyer. Sources : FAO.",
            "source": "FAO",
            "vecteur": [0.0, 0.0, 1.0],
        },
    ]
    chemin.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entrees) + "\n", encoding="utf-8"
    )


def test_charger_index(tmp_path: Path) -> None:
    chemin = tmp_path / "rag_index.jsonl"
    _ecrire_index(chemin)
    index = RagIndex.charger(chemin)
    assert index is not None
    assert index.taille == 3


def test_charger_index_absent(tmp_path: Path) -> None:
    assert RagIndex.charger(tmp_path / "absent.jsonl") is None


def test_rechercher_retourne_le_plus_proche(tmp_path: Path) -> None:
    chemin = tmp_path / "rag_index.jsonl"
    _ecrire_index(chemin)
    index = RagIndex.charger(chemin)
    passages = index.rechercher([0.9, 0.1, 0.0], k=2, seuil=0.5)
    assert passages
    assert "CNRA" in passages[0].source  # vecteur le plus aligné


def test_rechercher_respecte_le_seuil(tmp_path: Path) -> None:
    chemin = tmp_path / "rag_index.jsonl"
    _ecrire_index(chemin)
    index = RagIndex.charger(chemin)
    # Vecteur orthogonal à tout -> aucune similarité >= seuil élevé.
    assert index.rechercher([0.0, 0.0, 0.0], k=3, seuil=0.5) == []
    assert index.rechercher([1.0, 0.0, 0.0], k=3, seuil=0.99)[0].source == "CNRA"


# --- Reranking (F9) ---


def test_recouvrement_lexical() -> None:
    """Le recouvrement = fraction des mots de la question présents dans le texte."""
    mots = _mots_cles("rapport annuel de la FIRCA")  # {rapport, annuel, firca}
    assert recouvrement_lexical(mots, "le rapport annuel de la FIRCA 2024") == 1.0
    assert recouvrement_lexical(mots, "séchage des fèves") == 0.0
    assert recouvrement_lexical(set(), "n'importe") == 0.0


def test_reranker_voie_lexicale_fait_remonter_le_bon_document() -> None:
    """Un doc au score dense sous le seuil mais au fort recouvrement lexical remonte.

    Cas réel : le nom de source contient « firca » et la question parle de la FIRCA
    -> le rapport remonte devant un passage générique mieux noté en dense.
    """
    candidats = [
        Passage("Conseils généraux sur le séchage.", "ANADER", 0.60),
        Passage(
            "Le rapport annuel présente les projets de recherche cacao.",
            "firca-rapport-annuel-firca-2024.pdf",
            0.50,
        ),
    ]
    retenus = reranker(
        "Que dit le rapport annuel de la FIRCA ?",
        candidats,
        top_k=1,
        poids_lexical=0.35,
        seuil_dense=0.55,
        seuil_lexical=0.5,
    )
    assert retenus and "firca" in retenus[0].source


def test_reranker_filtre_les_non_eligibles() -> None:
    """Un passage faible en dense ET en lexical est écarté."""
    candidats = [Passage("Sujet sans rapport.", "X", 0.40)]
    assert (
        reranker(
            "Que dit le rapport de la FIRCA ?",
            candidats,
            top_k=3,
            poids_lexical=0.35,
            seuil_dense=0.55,
            seuil_lexical=0.5,
        )
        == []
    )


def test_reranker_respecte_top_k() -> None:
    """Le reranking ne renvoie jamais plus de top_k passages."""
    candidats = [Passage(f"texte {i}", "ANADER", 0.9) for i in range(5)]
    assert (
        len(
            reranker("q", candidats, top_k=2, poids_lexical=0.3, seuil_dense=0.5, seuil_lexical=0.5)
        )
        == 2
    )


def test_formater_contexte() -> None:
    passages = [Passage("Réponse A", "CNRA", 0.9), Passage("Réponse B", "", 0.7)]
    texte = formater_contexte(passages)
    assert "[1] (source : CNRA) Réponse A" in texte
    assert "[2] Réponse B" in texte


class _FauxEmbeddings:
    def __init__(self, vecteur: list[float] | None) -> None:
        self._vecteur = vecteur

    async def embed(self, textes: list[str]) -> list[list[float]] | None:
        return None if self._vecteur is None else [self._vecteur]


async def test_recuperateur_retourne_contexte(tmp_path: Path) -> None:
    chemin = tmp_path / "rag_index.jsonl"
    _ecrire_index(chemin)
    index = RagIndex.charger(chemin)
    rag = RagRecuperateur(_FauxEmbeddings([1.0, 0.0, 0.0]), index, top_k=1, seuil=0.5)
    contexte = await rag.contexte_pour("Quand récolter ?")
    assert contexte is not None
    assert "CNRA" in contexte


async def test_recuperateur_sans_resultat(tmp_path: Path) -> None:
    chemin = tmp_path / "rag_index.jsonl"
    _ecrire_index(chemin)
    index = RagIndex.charger(chemin)
    # Embeddings en panne -> None ; et seuil trop haut -> None.
    assert await RagRecuperateur(_FauxEmbeddings(None), index, 1, 0.5).contexte_pour("q") is None
    rag = RagRecuperateur(_FauxEmbeddings([0.0, 0.0, 0.0]), index, 1, 0.9)
    assert await rag.contexte_pour("q") is None


# --- Injection du contexte dans le prompt ---


def test_build_messages_sans_contexte() -> None:
    from app.services.prompts import build_messages

    messages = build_messages("Quand récolter ?")
    assert len(messages) == 2
    assert messages[-1] == {"role": "user", "content": "Quand récolter ?"}


def test_build_messages_avec_contexte() -> None:
    from app.services.prompts import build_messages

    messages = build_messages("Quand récolter ?", contexte="[1] (source : CNRA) ...")
    # Un SEUL message système (contrainte du template Ministral 3) ; le contexte
    # est injecté dans le message utilisateur.
    assert len(messages) == 2
    assert sum(1 for m in messages if m["role"] == "system") == 1
    assert "CNRA" in messages[-1]["content"]
    assert "Quand récolter ?" in messages[-1]["content"]


def test_build_messages_force_alternance_des_roles() -> None:
    """Un historique mal formé est normalisé en rôles alternés (évite le 500 Jinja)."""
    from app.services.prompts import build_messages

    historique = [
        {"role": "assistant", "content": "Bonjour"},  # assistant en tête -> retiré
        {"role": "user", "content": "Q1"},
        {"role": "user", "content": "Q2"},  # deux 'user' de suite -> fusionnés
        {"role": "assistant", "content": "R1"},
    ]
    messages = build_messages("Q3", historique=historique)
    assert messages[0]["role"] == "system"
    apres = [m["role"] for m in messages[1:]]
    assert apres[0] == "user" and apres[-1] == "user"
    assert all(apres[i] != apres[i + 1] for i in range(len(apres) - 1))
    assert "Q3" in messages[-1]["content"]


# --- Construction du RAG au démarrage (_construire_rag) ---


async def test_construire_rag_desactive() -> None:
    from app.core.config import Settings
    from app.main import _construire_rag

    assert _construire_rag(Settings(rag_enabled=False)) == (None, None)


async def test_construire_rag_actif(tmp_path: Path) -> None:
    from app.core.config import Settings
    from app.main import _construire_rag

    chemin = tmp_path / "rag_index.jsonl"
    _ecrire_index(chemin)
    embeddings, recuperateur = _construire_rag(
        Settings(rag_enabled=True, rag_index_path=str(chemin))
    )
    assert embeddings is not None
    assert recuperateur is not None
    await embeddings.close()


async def test_construire_rag_actif_mais_index_absent(tmp_path: Path) -> None:
    from app.core.config import Settings
    from app.main import _construire_rag

    resultat = _construire_rag(
        Settings(rag_enabled=True, rag_index_path=str(tmp_path / "absent.jsonl"))
    )
    assert resultat == (None, None)
