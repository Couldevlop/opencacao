"""Tests de la gestion des documents (stockage, découpage, validation)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.curation.documents import DocumentInvalide, DocumentStore, decouper, nom_sur


def test_nom_sur_neutralise_chemin() -> None:
    assert nom_sur("rapport.pdf") == "rapport.pdf"
    assert nom_sur("../../etc/passwd.txt") == "passwd.txt"  # pas de traversée
    assert nom_sur("  mon doc.md ") == "mon_doc.md"  # espaces -> _


def test_nom_sur_refuse_format_et_vide() -> None:
    with pytest.raises(DocumentInvalide):
        nom_sur("virus.exe")
    with pytest.raises(DocumentInvalide):
        nom_sur("   ")


def test_decouper_extraits_et_filtre_court() -> None:
    texte = ("Phrase de cacao numéro. " * 80).strip()
    extraits = decouper(texte, taille=300, chevauchement=50)
    assert len(extraits) >= 2
    assert all(len(e) >= 50 for e in extraits)
    assert decouper("court") == []  # trop court -> écarté
    assert decouper("   ") == []


def test_store_enregistrer_lister_supprimer(tmp_path: Path) -> None:
    store = DocumentStore(tmp_path / "documents")
    store.enregistrer("guide.txt", b"Contenu agronomique du cacao.")
    items = store.lister()
    assert items == [{"nom": "guide.txt", "taille": 29}]
    assert store.supprimer("guide.txt") is True
    assert store.lister() == []
    assert store.supprimer("guide.txt") is False  # déjà absent


def test_extraire_html(tmp_path: Path) -> None:
    from app.curation.documents import extraire_texte

    page = tmp_path / "page.html"
    page.write_text(
        "<html><head><title>x</title><style>.a{}</style></head>"
        "<body><script>var a=1;</script><h1>Coop&eacute;rative</h1>"
        "<p>Le cacao en C&ocirc;te d'Ivoire.</p></body></html>",
        encoding="utf-8",
    )
    texte = extraire_texte(page)
    assert "Coopérative" in texte  # entités décodées
    assert "Le cacao" in texte
    assert "var a=1" not in texte  # script ignoré
    assert ".a{}" not in texte  # style ignoré


def test_extraits_inclut_html(tmp_path: Path) -> None:
    store = DocumentStore(tmp_path / "documents")
    contenu = "<html><body><p>" + ("Le cacaoyer aime l'ombre. " * 12) + "</p></body></html>"
    store.enregistrer("page.html", contenu.encode("utf-8"))
    extraits = store.extraits()
    assert extraits and extraits[0][0] == "page.html"


def test_store_existe_prefixe(tmp_path: Path) -> None:
    store = DocumentStore(tmp_path / "documents")
    assert store.existe_prefixe("manuel") is False
    store.enregistrer("manuel.pdf", b"%PDF")
    assert store.existe_prefixe("manuel") is True  # quel que soit le format
    assert store.existe_prefixe("autre") is False


def test_store_existe(tmp_path: Path) -> None:
    store = DocumentStore(tmp_path / "documents")
    assert store.existe("guide.txt") is False
    store.enregistrer("guide.txt", b"contenu")
    assert store.existe("guide.txt") is True
    assert store.existe("mauvais.exe") is False  # nom invalide -> False, pas d'erreur


def test_store_refuse_vide_et_mauvais_format(tmp_path: Path) -> None:
    store = DocumentStore(tmp_path / "documents")
    with pytest.raises(DocumentInvalide):
        store.enregistrer("vide.txt", b"")
    with pytest.raises(DocumentInvalide):
        store.enregistrer("image.png", b"data")


def test_store_extraits_par_document(tmp_path: Path) -> None:
    store = DocumentStore(tmp_path / "documents")
    long_texte = "Le cacaoyer aime l'ombre et l'humidité en Côte d'Ivoire. " * 10
    store.enregistrer("a.txt", long_texte.encode("utf-8"))
    store.enregistrer("b.md", long_texte.encode("utf-8"))
    extraits = store.extraits()
    assert extraits  # au moins un extrait
    sources = {nom for nom, _ in extraits}
    assert sources == {"a.txt", "b.md"}


def test_from_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATASET_DIR", str(tmp_path))
    store = DocumentStore.from_env()
    store.enregistrer("x.txt", b"abc")
    assert (tmp_path / "documents" / "x.txt").exists()
