"""Tests complémentaires du pipeline : branches d'erreur de constitution/découverte/collecte."""

from __future__ import annotations

from pathlib import Path

import httpx

from app.curation import pipeline as pipeline_module

from .test_pipeline import FakeCluster, FakeEmbeddings, _doc, _service

_DOC_TEXTE = (
    "Le cacaoyer prospère à l'ombre dans les zones humides de Côte d'Ivoire. "
    "Un ombrage léger protège les jeunes plants du soleil direct et limite le "
    "stress hydrique pendant la première année de plantation."
)


# --- Constitution : index déjà à jour (lignes 294-300) ---


async def test_constituer_aucun_nouvel_extrait(tmp_path: Path) -> None:
    """Si tous les extraits sont déjà indexés, le job réussit sans rien ajouter."""
    service, jobs = _service(tmp_path, FakeEmbeddings())
    _doc(tmp_path, "guide.txt", _DOC_TEXTE)
    # 1re constitution : remplit l'index.
    j1 = await jobs.creer("rag_constitution")
    await service.constituer_rag(j1["id"])
    # 2e constitution : aucun nouvel extrait.
    j2 = await jobs.creer("rag_constitution")
    await service.constituer_rag(j2["id"])
    relu = await jobs.obtenir(j2["id"])
    assert relu["statut"] == "reussi"
    assert relu["details"] == {"ajoutees": 0, "total": relu["details"]["total"]}
    assert "déjà à jour" in relu["message"]


# --- Constitution : redémarrage de l'API échoue (lignes 313-323) ---


async def test_constituer_redemarrage_echoue(tmp_path: Path) -> None:
    """Index enrichi mais rollout en échec -> statut echec, travail conservé."""
    cluster = FakeCluster(echoue=True)
    service, jobs = _service(tmp_path, FakeEmbeddings(), cluster)
    _doc(tmp_path, "guide.txt", _DOC_TEXTE)
    job = await jobs.creer("rag_constitution")
    await service.constituer_rag(job["id"])
    relu = await jobs.obtenir(job["id"])
    assert relu["statut"] == "echec"
    assert "redémarrage" in relu["message"].lower()
    assert (tmp_path / "rag_index.jsonl").exists()  # index bien écrit


# --- Constitution : erreur inattendue (lignes 351-353) ---


async def test_constituer_erreur_inattendue(tmp_path: Path, monkeypatch) -> None:
    service, jobs = _service(tmp_path)
    _doc(tmp_path, "guide.txt", _DOC_TEXTE)

    def boom(_chemin):
        raise RuntimeError("index illisible")

    monkeypatch.setattr(pipeline_module, "lire_textes_indexes", boom)
    job = await jobs.creer("rag_constitution")
    await service.constituer_rag(job["id"])
    relu = await jobs.obtenir(job["id"])
    assert relu["statut"] == "echec"
    assert "index illisible" not in relu["message"]  # OWASP : message générique


# --- Découverte : anti-concurrence (lignes 449-451) ---


async def test_demarrer_decouverte_anti_concurrence(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    assert await service.demarrer_decouverte() is not None
    assert await service.demarrer_decouverte() is None


# --- Découverte : téléchargement échoué + document invalide (485-486, 491-492) ---


async def test_decouvrir_sources_echecs(tmp_path: Path, monkeypatch) -> None:
    """Un téléchargement raté et un format refusé sont comptés comme échecs."""

    async def faux_decouvrir(client, store, max_docs=25):
        return [
            {"url": "https://x/absent.pdf", "nom": "absent.pdf"},  # 404 -> échec
            {"url": "https://x/bon.pdf", "nom": "mauvais.exe"},  # format refusé -> échec
        ]

    monkeypatch.setattr(pipeline_module, "decouvrir", faux_decouvrir)

    def fab(verifie):
        def handler(req: httpx.Request) -> httpx.Response:
            if "absent" in str(req.url):
                return httpx.Response(404)
            return httpx.Response(200, content=b"%PDF data")

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    service, jobs = _service(tmp_path, http_factory=fab)
    job = await jobs.creer("decouverte_sources")
    await service.decouvrir_sources(job["id"])
    relu = await jobs.obtenir(job["id"])
    assert relu["statut"] == "reussi"
    assert relu["details"]["telecharges"] == 0  # les deux ont échoué
    assert relu["details"]["decouverts"] == 2


# --- Découverte : erreur inattendue (lignes 502-504) ---


async def test_decouvrir_sources_erreur_inattendue(tmp_path: Path, monkeypatch) -> None:
    async def boom(client, store, max_docs=25):
        raise RuntimeError("crash réseau")

    monkeypatch.setattr(pipeline_module, "decouvrir", boom)

    def fab(verifie):
        return httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))

    service, jobs = _service(tmp_path, http_factory=fab)
    job = await jobs.creer("decouverte_sources")
    await service.decouvrir_sources(job["id"])
    relu = await jobs.obtenir(job["id"])
    assert relu["statut"] == "echec"
    assert "crash réseau" not in relu["message"]


# --- Collecte : téléchargement échoué + format refusé (556-557, 564-565) ---


async def test_collecter_echecs_telechargement_et_format(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        pipeline_module,
        "charger_sources",
        lambda: [
            {"id": "absent", "source": "CNRA", "titre": "Absent", "url": "http://x/absent.pdf"},
            {"id": "vide", "source": "CNRA", "titre": "Vide", "url": "http://x/vide.pdf"},
        ],
    )

    def fab(verifie):
        def handler(req: httpx.Request) -> httpx.Response:
            if "absent" in str(req.url):
                return httpx.Response(404)
            return httpx.Response(200, content=b"")  # contenu vide -> DocumentInvalide

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    service, jobs = _service(tmp_path, http_factory=fab)
    job = await jobs.creer("recherche_sources")
    await service.collecter_sources(job["id"])
    relu = await jobs.obtenir(job["id"])
    assert relu["statut"] == "reussi"
    assert relu["details"]["telecharges"] == 0
    assert relu["details"]["echoues"] == 2


# --- Collecte : erreur inattendue (lignes 579-581) ---


async def test_collecter_erreur_inattendue(tmp_path: Path, monkeypatch) -> None:
    def boom():
        raise RuntimeError("manifeste corrompu")

    monkeypatch.setattr(pipeline_module, "charger_sources", boom)
    service, jobs = _service(tmp_path)
    job = await jobs.creer("recherche_sources")
    await service.collecter_sources(job["id"])
    relu = await jobs.obtenir(job["id"])
    assert relu["statut"] == "echec"
    assert "manifeste corrompu" not in relu["message"]
