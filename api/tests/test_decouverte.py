"""Tests de la découverte automatique de sources (allowlist, extraction PDF, dédup)."""

from __future__ import annotations

from pathlib import Path

import httpx

from app.curation import decouverte
from app.curation.decouverte import (
    _domaine_autorise,
    decouvrir,
    est_pdf,
    extraire_liens_pdf,
)
from app.curation.documents import DocumentStore


def test_est_pdf() -> None:
    assert est_pdf("https://cnra.ci/docs/guide.pdf") is True
    assert est_pdf("https://cnra.ci/docs/guide.PDF?v=2") is True  # casse + requête
    assert est_pdf("https://cnra.ci/page.html") is False


def test_domaine_autorise() -> None:
    assert _domaine_autorise("https://www.cnra.ci/x.pdf") is True  # sous-domaine
    assert _domaine_autorise("https://firca.ci/x.pdf") is True
    assert _domaine_autorise("https://mechant.com/x.pdf") is False
    assert _domaine_autorise("https://cnra.ci.mechant.com/x.pdf") is False  # pas un suffixe


def test_extraire_liens_pdf_filtre() -> None:
    html = (
        '<a href="/docs/guide-cacao.pdf">guide</a>'
        '<a href="https://mechant.com/vol.pdf">hors domaine</a>'
        '<a href="https://firca.ci/page.html">pas un pdf</a>'
        '<a href="https://www.cnra.ci/fiches/maladie.pdf">fiche</a>'
    )
    liens = extraire_liens_pdf(html, "https://cnra.ci/publications/")
    assert "https://cnra.ci/docs/guide-cacao.pdf" in liens  # relatif résolu
    assert "https://www.cnra.ci/fiches/maladie.pdf" in liens
    assert "https://mechant.com/vol.pdf" not in liens  # domaine non autorisé
    assert all(u.endswith(".pdf") for u in liens)


async def test_decouvrir_retourne_nouveaux_pdf(tmp_path: Path, monkeypatch) -> None:
    # Pas de DNS en test : on considère les URLs publiques.
    monkeypatch.setattr(decouverte, "url_publique_sure", lambda u: True)
    html = (
        b'<a href="https://cnra.ci/docs/nouveau-guide.pdf">x</a>'
        b'<a href="https://mechant.com/x.pdf">y</a>'
        b'<a href="https://cnra.ci/page.html">z</a>'
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=html, headers={"content-type": "text/html"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    store = DocumentStore(tmp_path / "documents")
    try:
        candidats = await decouvrir(client, store, max_docs=10)
    finally:
        await client.aclose()

    urls = {c["url"] for c in candidats}
    assert "https://cnra.ci/docs/nouveau-guide.pdf" in urls
    assert "https://mechant.com/x.pdf" not in urls  # domaine non autorisé écarté
    assert all(c["url"].endswith(".pdf") for c in candidats)


async def test_decouvrir_ecarte_deja_connus(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(decouverte, "url_publique_sure", lambda u: True)
    html = b'<a href="https://cnra.ci/docs/deja-la.pdf">x</a>'

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=html, headers={"content-type": "text/html"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    store = DocumentStore(tmp_path / "documents")
    # Pré-enregistre le document sous le nom qu'il aurait -> doit être écarté.
    from app.curation.sources import nom_depuis_url

    nom = nom_depuis_url("https://cnra.ci/docs/deja-la.pdf", "application/pdf")
    store.enregistrer(nom, b"%PDF-1.4 contenu")
    try:
        candidats = await decouvrir(client, store, max_docs=10)
    finally:
        await client.aclose()
    assert candidats == []  # déjà présent -> aucun nouveau
