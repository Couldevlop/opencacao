"""Tests de la recherche des sources officielles (manifeste + téléchargement)."""

from __future__ import annotations

from pathlib import Path

import httpx

from app.curation.sources import charger_sources, nom_fichier, telecharger


def test_charger_sources(tmp_path: Path) -> None:
    manifeste = tmp_path / "s.yaml"
    manifeste.write_text(
        "documents:\n"
        '  - id: manuel_a\n    source: "CNRA"\n    titre: "Manuel A"\n    url: "http://ex/a.pdf"\n'
        '  - id: sans_url\n    titre: "Incomplet"\n',  # sans url -> ignoré
        encoding="utf-8",
    )
    docs = charger_sources(manifeste)
    assert len(docs) == 1
    assert docs[0]["id"] == "manuel_a"
    assert docs[0]["url"] == "http://ex/a.pdf"


def test_charger_sources_absent(tmp_path: Path) -> None:
    assert charger_sources(tmp_path / "absent.yaml") == []


def test_nom_fichier() -> None:
    assert nom_fichier({"id": "doc1", "url": "http://x/y.pdf"}) == "doc1.pdf"
    assert nom_fichier({"id": "doc2", "url": "http://x/page"}) == "doc2.pdf"  # extension par défaut
    assert nom_fichier({"id": "doc3", "url": "http://x/n.txt"}) == "doc3.txt"


async def test_telecharger_ok() -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=b"%PDF-1.4 ..."))
    async with httpx.AsyncClient(transport=transport) as client:
        donnees = await telecharger(client, "http://ex/a.pdf")
    assert donnees == b"%PDF-1.4 ..."


async def test_telecharger_echec_http() -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(404))
    async with httpx.AsyncClient(transport=transport) as client:
        assert await telecharger(client, "http://ex/manquant.pdf") is None


async def test_telecharger_trop_volumineux(monkeypatch) -> None:
    from app.curation import sources as mod

    monkeypatch.setattr(mod, "_TAILLE_MAX", 4)
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=b"trop long"))
    async with httpx.AsyncClient(transport=transport) as client:
        assert await telecharger(client, "http://ex/gros.pdf") is None


def test_manifeste_embarque_present() -> None:
    """Le manifeste est bien embarqué dans l'image (à côté du module)."""
    from app.curation.sources import SOURCES_PATH

    docs = charger_sources(SOURCES_PATH)
    assert len(docs) >= 1  # au moins une source officielle
