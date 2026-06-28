"""Tests du build d'index RAG (build_rag_index) — reprise et retry.

Aucun appel réseau réel : le service d'embeddings est mocké. On vérifie que le
build est résumable (ne ré-embedde pas les réponses déjà indexées) et qu'il
résiste à une déconnexion transitoire (retry par lot) — robustesse requise pour
un build long et non supervisé.
"""

from __future__ import annotations

import json
from pathlib import Path

import build_rag_index as bri


def _ecrire_sources(tmp_path: Path, n: int) -> Path:
    src = tmp_path / "corpus.jsonl"
    src.write_text(
        "\n".join(
            json.dumps({"instruction": f"Q{i}", "output": f"R{i}"}) for i in range(n)
        ),
        encoding="utf-8",
    )
    return src


def test_construire_reprend_uniquement_les_paires_manquantes(
    tmp_path, monkeypatch
) -> None:
    """Sur une sortie partielle, seules les réponses absentes sont ré-embeddées."""
    src = _ecrire_sources(tmp_path, 3)
    out = tmp_path / "idx.jsonl"
    # Index partiel : R0 et R1 déjà présents (build interrompu avant R2).
    out.write_text(
        "\n".join(
            json.dumps({"texte": f"R{i}", "source": "", "vecteur": [0.0]})
            for i in range(2)
        )
        + "\n",
        encoding="utf-8",
    )
    appels: list[list[str]] = []

    def faux_embed(
        url: str, textes: list[str], tentatives: int = 6
    ) -> list[list[float]]:
        appels.append(list(textes))
        return [[1.0, 0.0] for _ in textes]

    monkeypatch.setattr(bri, "_embed_batch", faux_embed)
    total = bri.construire([src], "http://x", out)

    assert appels == [["Q2"]]  # seule la paire manquante est ré-embeddée
    assert total == 3  # index complet après reprise


def test_embed_batch_retente_apres_deconnexion_transitoire(monkeypatch) -> None:
    """Une déconnexion transitoire est réessayée puis réussit (pas d'abandon)."""
    monkeypatch.setattr(bri.time, "sleep", lambda _s: None)
    etat = {"n": 0}

    class FauxReponse:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def read(self) -> bytes:
            return json.dumps({"data": [{"embedding": [0.5, 0.5]}]}).encode()

    def faux_urlopen(_req, timeout: float = 0):
        etat["n"] += 1
        if etat["n"] == 1:
            raise ConnectionResetError("remote disconnected")  # blip du port-forward
        return FauxReponse()

    monkeypatch.setattr(bri.urllib.request, "urlopen", faux_urlopen)
    vecteurs = bri._embed_batch("http://x", ["Q"])

    assert vecteurs == [[0.5, 0.5]]
    assert etat["n"] == 2  # 1 échec transitoire + 1 succès
