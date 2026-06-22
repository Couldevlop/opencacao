"""Tests du pipeline RAG de construction du corpus (build_corpus_rag).

Conformément à CLAUDE §4.4 et §13 : aucun appel réseau réel (LLM et encodeur
mockés) et aucun dosage phytosanitaire chiffré n'est écrit, même en exemple.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

import build_corpus_rag as bcr
from build_corpus_rag import (
    Chunk,
    LocalLLMClient,
    SourceDoc,
    _indices_diversifies,
    _joindre_endpoint,
    _nettoyer_texte,
    charger_manifeste,
    construire_corpus,
    construire_prompt,
    decouper_en_chunks,
    normaliser_instruction,
    paire_valide,
    parser_reponse_llm,
)

_SOURCE = " Sources : CNRA."
_DOC = SourceDoc(
    id="doc_test",
    source="CNRA",
    titre="Doc de test",
    url="http://example.invalid/doc.pdf",
    annee=2020,
)


class FakeEmbedder:
    """Encodeur déterministe : texte identique → vecteur identique."""

    def __init__(self, dim: int = 16) -> None:
        self._dim = dim

    def encoder(self, textes: list[str]) -> np.ndarray:
        vecteurs = []
        for texte in textes:
            graine = int.from_bytes(hashlib.sha256(texte.encode()).digest()[:8], "big")
            rng = np.random.default_rng(graine)
            v = rng.standard_normal(self._dim).astype(np.float32)
            v /= np.linalg.norm(v) + 1e-9
            vecteurs.append(v)
        return np.stack(vecteurs)


class FakeLLM:
    """Renvoie un tableau JSON fixe : 1 paire valide, 1 doublon, 1 sans source."""

    def __init__(self) -> None:
        self.appels = 0

    def generer(self, system: str, user: str) -> str:
        self.appels += 1
        paires = [
            {
                "instruction": "Comment reconnaître une cabosse mûre ?",
                "output": "Une cabosse est mûre quand sa couleur vire au jaune ou orange." + _SOURCE,
            },
            {
                "instruction": "Comment reconnaître une cabosse mûre ?",
                "output": "Réponse doublon identique à la précédente, à éliminer." + _SOURCE,
            },
            {
                "instruction": "À quelle fréquence récolter en pleine campagne ?",
                "output": "On récolte tous les dix à quinze jours en pleine campagne sans citer de source.",
            },
        ]
        return "Voici les paires :\n" + json.dumps(paires, ensure_ascii=False)


def test_joindre_endpoint_serveur_local_ajoute_v1() -> None:
    """Un serveur local (racine sans version) reçoit le préfixe /v1."""
    assert _joindre_endpoint("http://localhost:8000", "chat/completions") == (
        "http://localhost:8000/v1/chat/completions"
    )
    assert _joindre_endpoint("http://localhost:8000/", "models") == (
        "http://localhost:8000/v1/models"
    )


def test_joindre_endpoint_api_versionnee_ne_duplique_pas() -> None:
    """Une base déjà versionnée (ex. Z.ai /v4) ne reçoit pas un /v1 en double."""
    base = "https://api.z.ai/api/coding/paas/v4"
    assert _joindre_endpoint(base, "chat/completions") == f"{base}/chat/completions"
    assert _joindre_endpoint(base, "models") == f"{base}/models"


def test_client_resout_les_endpoints_selon_la_base() -> None:
    """LocalLLMClient construit ses URLs via la résolution tolérante de version."""
    local = LocalLLMClient(base_url="http://localhost:8000", modele="ministral")
    assert local._endpoint == "http://localhost:8000/v1/chat/completions"
    externe = LocalLLMClient(base_url="https://api.z.ai/api/coding/paas/v4", modele="glm-5.2")
    assert externe._endpoint == "https://api.z.ai/api/coding/paas/v4/chat/completions"
    assert externe._endpoint_models == "https://api.z.ai/api/coding/paas/v4/models"


def test_charger_manifeste_reel() -> None:
    chemin = (
        Path(bcr.__file__).resolve().parents[2]
        / "corpus"
        / "sources"
        / "sources_officielles.yaml"
    )
    documents = charger_manifeste(chemin)
    assert documents
    assert all(d.source in bcr.SOURCES_RECONNUES for d in documents)


def test_nettoyer_texte_recolle_cesures_et_espaces() -> None:
    brut = "le cacao-\nyer  aime\nl'ombre"
    assert _nettoyer_texte(brut) == "le cacaoyer aime l'ombre"


def test_decouper_en_chunks_respecte_taille_min() -> None:
    texte = "A" * 2000
    chunks = decouper_en_chunks(_DOC, [(1, texte)], taille=900, chevauchement=150)
    assert chunks
    assert all(len(c.texte) >= bcr.LONGUEUR_CHUNK_MIN for c in chunks)
    assert all(c.doc.id == "doc_test" and c.page == 1 for c in chunks)


def test_decouper_ignore_page_trop_courte() -> None:
    assert decouper_en_chunks(_DOC, [(1, "trop court")]) == []


def test_parser_reponse_llm_tolere_texte_autour() -> None:
    brut = 'blabla [{"instruction": "Q ?", "output": "R."}] fin'
    paires = parser_reponse_llm(brut)
    assert paires == [{"instruction": "Q ?", "input": "", "output": "R."}]


def test_parser_reponse_llm_json_invalide_retourne_vide() -> None:
    assert parser_reponse_llm("pas de json ici") == []
    assert parser_reponse_llm("[ {cassé } ]") == []


def test_normaliser_instruction_ignore_casse_accents_ponctuation() -> None:
    a = normaliser_instruction("Récolté, déjà ?")
    b = normaliser_instruction("recolte deja")
    assert a == b == "recolte deja"


def test_paire_valide_accepte_paire_correcte() -> None:
    paire = {
        "instruction": "Comment aérer ma cacaoyère correctement ?",
        "input": "",
        "output": "Taillez les branches basses pour laisser l'air circuler entre les arbres." + _SOURCE,
    }
    valide, motif = paire_valide(paire)
    assert valide and motif == ""


def test_paire_valide_rejette_sans_source() -> None:
    paire = {
        "instruction": "Comment aérer ma cacaoyère ?",
        "input": "",
        "output": "Taillez les branches basses pour laisser l'air circuler entre les arbres librement.",
    }
    valide, motif = paire_valide(paire)
    assert not valide
    assert "source" in motif


def test_indices_diversifies_retire_redondances() -> None:
    base = np.array([1.0, 0.0], dtype=np.float32)
    quasi = np.array([0.999, 0.0447], dtype=np.float32)
    quasi /= np.linalg.norm(quasi)
    ortho = np.array([0.0, 1.0], dtype=np.float32)
    embeddings = np.stack([base, quasi, ortho])
    indices = _indices_diversifies(embeddings, seuil=0.92)
    assert indices == [0, 2]


def test_construire_corpus_valide_dedup_et_respecte_cible(tmp_path: Path) -> None:
    chunks = [Chunk(doc=_DOC, page=i, texte="extrait " + "x" * 300) for i in range(1, 6)]
    sortie = tmp_path / "out.jsonl"
    stats = construire_corpus(
        chunks=chunks,
        client=FakeLLM(),
        embedder=FakeEmbedder(),
        sortie=sortie,
        cible=10,
        paires_par_chunk=3,
    )
    lignes = [json.loads(x) for x in sortie.read_text(encoding="utf-8").splitlines() if x.strip()]
    # Une seule paire valide et non-doublon par run, malgré 5 chunks.
    assert len(lignes) == 1
    assert lignes[0]["instruction"] == "Comment reconnaître une cabosse mûre ?"
    assert stats.paires_rejetees >= 1  # la paire sans source
    assert stats.paires_doublons >= 1  # le doublon exact


class AngleLLM:
    """Renvoie une paire valide dont la question encode la directive d'angle.

    Permet de vérifier que plusieurs angles produisent des questions distinctes.
    """

    def generer(self, system: str, user: str) -> str:
        directive = user.split("Angle demandé :", 1)[1].split("\n", 1)[0].strip()
        paire = {
            "instruction": f"Question angle {directive[:40]} sur le cacaoyer ?",
            "output": "Réponse pratique fidèle à l'extrait officiel." + _SOURCE,
        }
        return json.dumps([paire], ensure_ascii=False)


def test_construire_prompt_injecte_la_directive_dangle() -> None:
    chunk = Chunk(doc=_DOC, page=1, texte="extrait " + "y" * 300)
    _system, user = construire_prompt(chunk, 3, "comment prévenir le problème.")
    assert "Angle demandé : comment prévenir le problème." in user


def test_multi_angles_multiplie_les_paires_distinctes(tmp_path: Path) -> None:
    chunks = [Chunk(doc=_DOC, page=1, texte="extrait " + "z" * 300)]
    sortie = tmp_path / "out.jsonl"
    angles = (
        ("symptomes", "reconnaître les signes."),
        ("action", "quelle action mener."),
        ("prevention", "comment prévenir."),
    )
    stats = construire_corpus(
        chunks=chunks,
        client=AngleLLM(),
        embedder=FakeEmbedder(),
        sortie=sortie,
        cible=100,
        paires_par_chunk=1,
        angles=angles,
    )
    lignes = [x for x in sortie.read_text(encoding="utf-8").splitlines() if x.strip()]
    # Un seul passage, mais trois angles distincts → trois questions distinctes.
    assert stats.paires_ecrites == 3
    assert len(lignes) == 3


def test_construire_corpus_reprise_idempotente(tmp_path: Path) -> None:
    sortie = tmp_path / "out.jsonl"
    existante = {
        "instruction": "Comment reconnaître une cabosse mûre ?",
        "input": "",
        "output": "Déjà présente." + _SOURCE,
    }
    sortie.write_text(json.dumps(existante, ensure_ascii=False) + "\n", encoding="utf-8")
    chunks = [Chunk(doc=_DOC, page=1, texte="extrait " + "x" * 300)]
    stats = construire_corpus(
        chunks=chunks,
        client=FakeLLM(),
        embedder=FakeEmbedder(),
        sortie=sortie,
        cible=10,
        paires_par_chunk=3,
    )
    # L'instruction déjà présente est reconnue comme doublon : rien de neuf écrit.
    assert stats.paires_ecrites == 0
    lignes = [x for x in sortie.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lignes) == 1
