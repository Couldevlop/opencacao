"""Tests de la recherche des sources officielles (manifeste + téléchargement)."""

from __future__ import annotations

from pathlib import Path

import httpx

from app.curation.sources import (
    charger_sources,
    extension_pour,
    nom_depuis_url,
    telecharger,
    url_publique_sure,
)


def test_url_publique_sure_bloque_interne() -> None:
    # IP publique -> autorisée.
    assert url_publique_sure("http://8.8.8.8/") is True
    # Adresses internes / privées / métadonnées -> refusées (anti-SSRF).
    assert url_publique_sure("http://127.0.0.1/") is False
    assert url_publique_sure("http://10.0.0.5/") is False
    assert url_publique_sure("http://169.254.169.254/latest/meta-data/") is False
    assert url_publique_sure("http://inference.svc/") is False
    assert url_publique_sure("http://localhost:8001/") is False
    # Schéma non http(s) -> refusé.
    assert url_publique_sure("ftp://example.com/x") is False


def test_nom_depuis_url_query_unique() -> None:
    n1 = nom_depuis_url("http://x.ci/index.php?id=111&Itemid=184", "text/html")
    n2 = nom_depuis_url("http://x.ci/index.php?id=112&Itemid=184", "text/html")
    assert n1.endswith(".html")
    assert "id-111" in n1
    assert n1 != n2  # deux articles distincts -> deux noms distincts


def test_charger_sources(tmp_path: Path) -> None:
    manifeste = tmp_path / "s.yaml"
    manifeste.write_text(
        "documents:\n"
        '  - id: manuel_a\n    source: "CNRA"\n    titre: "Manuel A"\n    url: "http://ex/a.pdf"\n'
        '  - id: ssl_casse\n    titre: "SSL"\n    url: "https://ex/b.pdf"\n    verify: false\n'
        '  - id: sans_url\n    titre: "Incomplet"\n',  # sans url -> ignoré
        encoding="utf-8",
    )
    docs = charger_sources(manifeste)
    assert len(docs) == 2
    assert docs[0]["id"] == "manuel_a"
    assert docs[0]["verify"] is True  # défaut
    assert docs[1]["verify"] is False  # transmis depuis le manifeste


def test_charger_sources_absent(tmp_path: Path) -> None:
    assert charger_sources(tmp_path / "absent.yaml") == []


def test_extension_pour() -> None:
    # Priorité au type de contenu HTTP.
    assert extension_pour("http://x/page", "text/html; charset=utf-8") == ".html"
    assert extension_pour("http://x/doc", "application/pdf") == ".pdf"
    # Sinon repli sur l'extension de l'URL.
    assert extension_pour("http://x/y.pdf", None) == ".pdf"
    assert extension_pour("http://x/page", None) == ".bin"  # inconnu -> rejeté ensuite


async def test_telecharger_ok() -> None:
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200, content=b"%PDF-1.4 ...", headers={"content-type": "application/pdf"}
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        donnees, ct = await telecharger(client, "http://ex/a.pdf")
    assert donnees == b"%PDF-1.4 ..."
    assert ct == "application/pdf"


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
