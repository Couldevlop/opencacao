"""Tests du service de pipeline (reindex RAG + préparation fine-tuning)."""

from __future__ import annotations

import json
from pathlib import Path

from app.curation import pipeline as pipeline_module
from app.curation.documents import DocumentStore
from app.curation.jobs import JobsRegistry
from app.curation.k8s import ClusterIndisponible
from app.curation.pipeline import PipelineService

# Réponse curée valide : source citée, longueurs dans les bornes, aucun dosage.
_OUTPUT_VALIDE = (
    "Étalez les fèves en couche fine et remuez-les régulièrement pour un séchage "
    "homogène au soleil. Sources : CNRA, ANADER."
)


class FakeEmbeddings:
    """Embeddings simulés : un vecteur par texte, ou None si en panne."""

    def __init__(self, en_panne: bool = False) -> None:
        self._en_panne = en_panne
        self.appels: list[list[str]] = []

    async def embed(self, textes: list[str]) -> list[list[float]] | None:
        self.appels.append(textes)
        if self._en_panne:
            return None
        return [[float(i), 1.0] for i, _ in enumerate(textes)]


class FakeCluster:
    """Cluster simulé : enregistre les redémarrages, ou échoue si configuré."""

    def __init__(self, echoue: bool = False) -> None:
        self._echoue = echoue
        self.restarts: list[str] = []

    async def rollout_restart(self, deployment: str) -> None:
        if self._echoue:
            raise ClusterIndisponible("refusé")
        self.restarts.append(deployment)

    async def close(self) -> None:
        return None


def _service(
    tmp_path: Path,
    embeddings: FakeEmbeddings | None = None,
    cluster: FakeCluster | None = None,
    http_factory=None,
) -> tuple[PipelineService, JobsRegistry]:
    jobs = JobsRegistry(tmp_path / "jobs.jsonl")
    cluster = cluster or FakeCluster()
    service = PipelineService(
        dataset_dir=tmp_path,
        corpus_cure=tmp_path / "corpus_cure.jsonl",
        index_path=tmp_path / "rag_index.jsonl",
        jobs=jobs,
        embeddings=embeddings or FakeEmbeddings(),
        api_deployment="api",
        cluster_factory=lambda: cluster,
        documents=DocumentStore(tmp_path / "documents"),
        http_factory=http_factory,
    )
    return service, jobs


def _ecrire(chemin: Path, lignes: list[dict]) -> None:
    chemin.write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in lignes) + "\n",
        encoding="utf-8",
    )


# --- Reindex RAG ---


async def test_reindex_aucun_nouveau_fait(tmp_path: Path) -> None:
    embeddings = FakeEmbeddings()
    cluster = FakeCluster()
    service, jobs = _service(tmp_path, embeddings, cluster)
    # Corpus curé vide : rien à indexer.
    (tmp_path / "corpus_cure.jsonl").write_text("", encoding="utf-8")
    job = await jobs.creer("rag_reindex")

    await service.reindexer_rag(job["id"])

    relu = await jobs.obtenir(job["id"])
    assert relu["statut"] == "reussi"
    assert relu["details"]["ajoutees"] == 0
    assert embeddings.appels == []  # pas de vectorisation
    assert cluster.restarts == []  # pas de redémarrage inutile


async def test_reindex_succes_ajoute_et_redemarre(tmp_path: Path) -> None:
    embeddings = FakeEmbeddings()
    cluster = FakeCluster()
    service, jobs = _service(tmp_path, embeddings, cluster)
    _ecrire(
        tmp_path / "rag_index.jsonl",
        [{"texte": "fait de base", "source": "CNRA", "vecteur": [1.0, 0.0]}],
    )
    _ecrire(
        tmp_path / "corpus_cure.jsonl",
        [{"instruction": "Comment sécher les fèves ?", "output": _OUTPUT_VALIDE}],
    )
    job = await jobs.creer("rag_reindex")

    await service.reindexer_rag(job["id"])

    relu = await jobs.obtenir(job["id"])
    assert relu["statut"] == "reussi"
    assert relu["details"] == {"ajoutees": 1, "total": 2}
    assert cluster.restarts == ["api"]
    # L'index a grandi, jamais rétréci.
    from app.services.rag_index_builder import lire_index

    textes = {e["texte"] for e in lire_index(tmp_path / "rag_index.jsonl")}
    assert textes == {"fait de base", _OUTPUT_VALIDE}


async def test_reindex_embeddings_en_panne(tmp_path: Path) -> None:
    service, jobs = _service(tmp_path, FakeEmbeddings(en_panne=True), FakeCluster())
    _ecrire(
        tmp_path / "corpus_cure.jsonl",
        [{"instruction": "Q nouvelle longue ici", "output": _OUTPUT_VALIDE}],
    )
    job = await jobs.creer("rag_reindex")

    await service.reindexer_rag(job["id"])

    relu = await jobs.obtenir(job["id"])
    assert relu["statut"] == "echec"
    assert "embeddings" in relu["message"].lower()
    assert not (tmp_path / "rag_index.jsonl").exists()  # index inchangé


async def test_reindex_redemarrage_echoue(tmp_path: Path) -> None:
    cluster = FakeCluster(echoue=True)
    service, jobs = _service(tmp_path, FakeEmbeddings(), cluster)
    _ecrire(
        tmp_path / "corpus_cure.jsonl",
        [{"instruction": "Q nouvelle longue ici", "output": _OUTPUT_VALIDE}],
    )
    job = await jobs.creer("rag_reindex")

    await service.reindexer_rag(job["id"])

    relu = await jobs.obtenir(job["id"])
    assert relu["statut"] == "echec"
    assert "redémarrage" in relu["message"].lower()
    # L'index a tout de même été reconstruit (le travail n'est pas perdu).
    assert (tmp_path / "rag_index.jsonl").exists()


async def test_reindex_erreur_inattendue(tmp_path: Path, monkeypatch) -> None:
    service, jobs = _service(tmp_path)

    def boom(_chemin):
        raise RuntimeError("disque cassé")

    monkeypatch.setattr(pipeline_module, "lire_textes_indexes", boom)
    job = await jobs.creer("rag_reindex")

    await service.reindexer_rag(job["id"])

    relu = await jobs.obtenir(job["id"])
    assert relu["statut"] == "echec"
    # OWASP : message générique, pas de détail interne exposé.
    assert "disque cassé" not in relu["message"]


async def test_demarrer_reindex_anti_concurrence(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    premier = await service.demarrer_reindex()
    assert premier is not None
    assert await service.demarrer_reindex() is None  # déjà en cours


# --- Constitution RAG depuis les documents ---

_DOC_TEXTE = (
    "Le cacaoyer prospère à l'ombre dans les zones humides de Côte d'Ivoire. "
    "Un ombrage léger protège les jeunes plants du soleil direct et limite le "
    "stress hydrique pendant la première année de plantation."
)


def _doc(tmp_path: Path, nom: str, texte: str) -> None:
    docs = tmp_path / "documents"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / nom).write_text(texte, encoding="utf-8")


async def test_constituer_aucun_document(tmp_path: Path) -> None:
    service, jobs = _service(tmp_path)
    job = await jobs.creer("rag_constitution")
    await service.constituer_rag(job["id"])
    relu = await jobs.obtenir(job["id"])
    assert relu["statut"] == "echec"
    assert "document" in relu["message"].lower()


async def test_constituer_succes_indexe_et_redemarre(tmp_path: Path) -> None:
    cluster = FakeCluster()
    service, jobs = _service(tmp_path, FakeEmbeddings(), cluster)
    _doc(tmp_path, "guide.txt", _DOC_TEXTE)
    job = await jobs.creer("rag_constitution")

    await service.constituer_rag(job["id"])

    relu = await jobs.obtenir(job["id"])
    assert relu["statut"] == "reussi"
    assert relu["details"]["ajoutees"] >= 1
    assert cluster.restarts == ["api"]
    # Les extraits sont indexés avec le nom du document comme source.
    from app.services.rag_index_builder import lire_index

    entrees = lire_index(tmp_path / "rag_index.jsonl")
    assert entrees and entrees[0]["source"] == "guide.txt"


async def test_constituer_embeddings_en_panne(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pipeline_module, "_EMBED_DELAI_S", 0)  # pas d'attente en test
    service, jobs = _service(tmp_path, FakeEmbeddings(en_panne=True))
    _doc(tmp_path, "guide.txt", _DOC_TEXTE)
    job = await jobs.creer("rag_constitution")
    await service.constituer_rag(job["id"])
    relu = await jobs.obtenir(job["id"])
    assert relu["statut"] == "echec"
    assert relu["details"]["ajoutees"] == 0  # rien indexé (échec dès le 1er lot)
    assert not (tmp_path / "rag_index.jsonl").exists()


async def test_constituer_partiel_puis_reprise(tmp_path: Path, monkeypatch) -> None:
    """Un échec d'embeddings en cours conserve le déjà-fait ; relancer reprend."""
    monkeypatch.setattr(pipeline_module, "_EMBED_DELAI_S", 0)

    class EmbeddingsIntermittent:
        """1er passage : 1er lot OK puis panne. Après `reprise=True` : tout OK."""

        def __init__(self) -> None:
            self.lots_ok = 0
            self.reprise = False

        async def embed(self, textes):
            if self.reprise:
                return [[1.0, 0.0] for _ in textes]
            if self.lots_ok >= 1:  # après le 1er lot : panne persistante
                return None
            self.lots_ok += 1
            return [[1.0, 0.0] for _ in textes]

    # Assez d'extraits pour forcer plusieurs lots (lot=32).
    gros = " ".join(f"Phrase numero {i} sur la cacaoculture en Cote d'Ivoire." for i in range(1500))
    _doc(tmp_path, "gros.txt", gros)
    emb = EmbeddingsIntermittent()
    service, jobs = _service(tmp_path, emb)

    job1 = await jobs.creer("rag_constitution")
    await service.constituer_rag(job1["id"])
    r1 = await jobs.obtenir(job1["id"])
    assert r1["statut"] == "echec"  # partiel
    assert r1["details"]["ajoutees"] == 8  # seul le 1er lot (taille 8) indexé et conservé

    # Reprise : le reste s'indexe, sans re-traiter le déjà-fait (dédup).
    emb.reprise = True
    job2 = await jobs.creer("rag_constitution")
    await service.constituer_rag(job2["id"])
    r2 = await jobs.obtenir(job2["id"])
    assert r2["statut"] == "reussi"
    assert r2["details"]["ajoutees"] > 0


async def test_constituer_anti_concurrence(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    assert await service.demarrer_constitution() is not None
    assert await service.demarrer_constitution() is None


# --- Recherche des sources officielles ---


def _http_ok(contenu: bytes = b"%PDF-1.4 contenu"):
    import httpx

    # La fabrique reçoit le mode `verifie` (ignoré ici : transport simulé).
    return lambda verifie=True: httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=contenu))
    )


async def test_collecter_succes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        pipeline_module,
        "charger_sources",
        lambda: [
            {"id": "manuel_a", "source": "CNRA", "titre": "Manuel A", "url": "http://x/a.pdf"}
        ],
    )
    service, jobs = _service(tmp_path, http_factory=_http_ok())
    job = await jobs.creer("recherche_sources")

    await service.collecter_sources(job["id"])

    relu = await jobs.obtenir(job["id"])
    assert relu["statut"] == "reussi"
    assert relu["details"]["telecharges"] == 1
    assert (tmp_path / "documents" / "manuel_a.pdf").exists()


async def test_collecter_idempotent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        pipeline_module,
        "charger_sources",
        lambda: [{"id": "manuel_a", "source": "CNRA", "titre": "A", "url": "http://x/a.pdf"}],
    )
    (tmp_path / "documents").mkdir(parents=True)
    (tmp_path / "documents" / "manuel_a.pdf").write_bytes(b"deja")
    service, jobs = _service(tmp_path, http_factory=_http_ok())
    job = await jobs.creer("recherche_sources")

    await service.collecter_sources(job["id"])

    relu = await jobs.obtenir(job["id"])
    assert relu["details"] == {"telecharges": 0, "deja": 1, "echoues": 0}


async def test_collecter_source_verify_false(tmp_path: Path, monkeypatch) -> None:
    """Une source à certificat cassé (verify: false) est tout de même téléchargée."""
    monkeypatch.setattr(
        pipeline_module,
        "charger_sources",
        lambda: [
            {
                "id": "ssl_casse",
                "source": "X",
                "titre": "SSL",
                "url": "https://x/s.pdf",
                "verify": False,
            }
        ],
    )
    modes: list[bool] = []
    import httpx

    def fabrique(verifie):
        modes.append(verifie)
        return httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, content=b"%PDF"))
        )

    service, jobs = _service(tmp_path, http_factory=fabrique)
    job = await jobs.creer("recherche_sources")
    await service.collecter_sources(job["id"])

    relu = await jobs.obtenir(job["id"])
    assert relu["details"]["telecharges"] == 1
    assert modes == [False]  # le client non-vérifié a bien été demandé


async def test_collecter_aucune_source(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pipeline_module, "charger_sources", lambda: [])
    service, jobs = _service(tmp_path)
    job = await jobs.creer("recherche_sources")
    await service.collecter_sources(job["id"])
    relu = await jobs.obtenir(job["id"])
    assert relu["statut"] == "echec"


async def test_demarrer_recherche_anti_concurrence(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    assert await service.demarrer_recherche() is not None
    assert await service.demarrer_recherche() is None


async def test_ajouter_document_url(tmp_path: Path, monkeypatch) -> None:
    import httpx

    monkeypatch.setattr(pipeline_module, "url_publique_sure", lambda u: True)
    page = b"<html><body><h1>ANADER</h1><p>Le cacao en Cote d'Ivoire.</p></body></html>"

    def fab(verifie):
        return httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, content=page, headers={"content-type": "text/html"})
            )
        )

    service, _ = _service(tmp_path, http_factory=fab)
    doc = await service.ajouter_document_url("https://www.anader.ci/")
    assert doc is not None
    assert doc["nom"].endswith(".html")  # détecté comme page HTML
    assert (tmp_path / "documents" / doc["nom"]).exists()


async def test_ajouter_document_url_repli_tls(tmp_path: Path, monkeypatch) -> None:
    """Sur certificat cassé (vérif TLS), on bascule en non-vérifié et ça passe."""
    import httpx

    monkeypatch.setattr(pipeline_module, "url_publique_sure", lambda u: True)
    appels: list[bool] = []

    def fab(verifie):
        appels.append(verifie)

        def handler(req):
            if verifie:
                raise httpx.ConnectError("certificate verify failed")
            return httpx.Response(
                200, content=b"<html>ok</html>", headers={"content-type": "text/html"}
            )

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    service, _ = _service(tmp_path, http_factory=fab)
    doc = await service.ajouter_document_url("https://cert-casse.ci/p")
    assert doc is not None
    assert appels == [True, False]  # a réessayé sans vérification TLS


async def test_ajouter_document_url_injoignable(tmp_path: Path, monkeypatch) -> None:
    import httpx

    monkeypatch.setattr(pipeline_module, "url_publique_sure", lambda u: True)

    def fab(verifie):
        return httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404)))

    service, _ = _service(tmp_path, http_factory=fab)
    assert await service.ajouter_document_url("https://x/absent") is None


async def test_ajouter_document_url_ssrf_bloque(tmp_path: Path, monkeypatch) -> None:
    """Une URL interne (SSRF) est refusée avant tout téléchargement."""
    import pytest

    monkeypatch.setattr(pipeline_module, "url_publique_sure", lambda u: False)
    service, _ = _service(tmp_path)
    with pytest.raises(pipeline_module.DocumentInvalide):
        await service.ajouter_document_url("http://inference:8000/")


# --- Préparation fine-tuning ---


async def test_preparer_succes_ecrit_corpus_et_procedure(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    _ecrire(
        tmp_path / "corpus_cure.jsonl",
        [{"instruction": "Comment sécher les fèves ?", "output": _OUTPUT_VALIDE}],
    )

    job = await service.preparer_finetuning()

    assert job is not None
    assert job["statut"] == "reussi"
    assert job["details"]["paires"] == 1
    assert "pod_train.sh" in job["details"]["procedure"]
    sortie = tmp_path / "corpus_entrainement_cure.jsonl"
    assert sortie.exists()
    assert (
        json.loads(sortie.read_text(encoding="utf-8").splitlines()[0])["output"] == _OUTPUT_VALIDE
    )


async def test_preparer_aucune_paire_valide(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    # Output trop court + aucune source : invalide.
    _ecrire(
        tmp_path / "corpus_cure.jsonl",
        [{"instruction": "Question courte ici", "output": "trop court"}],
    )

    job = await service.preparer_finetuning()

    assert job is not None
    assert job["statut"] == "echec"
    assert not (tmp_path / "corpus_entrainement_cure.jsonl").exists()


async def test_preparer_ecarte_dosage(tmp_path: Path, monkeypatch) -> None:
    """Le garde-fou dosage est appliqué à l'assemblage (jamais de dosage au corpus)."""

    def faux_verif(texte: str):
        # Simule la détection d'un dosage sans écrire de dosage réel dans le test.
        return object() if "MARQUEUR" in texte else None

    monkeypatch.setattr(pipeline_module.guardrails, "verifier_reponse", faux_verif)
    service, _ = _service(tmp_path)
    _ecrire(
        tmp_path / "corpus_cure.jsonl",
        [
            {"instruction": "Comment sécher les fèves ?", "output": _OUTPUT_VALIDE},
            {"instruction": "Quel traitement appliquer ?", "output": _OUTPUT_VALIDE + " MARQUEUR"},
        ],
    )

    job = await service.preparer_finetuning()

    assert job is not None
    assert job["statut"] == "reussi"
    assert job["details"]["paires"] == 1  # le marqueur est écarté
    assert job["details"]["rejetees"] == 1


async def test_preparer_dedup(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    _ecrire(
        tmp_path / "corpus_cure.jsonl",
        [
            {"instruction": "Comment sécher les fèves ?", "output": _OUTPUT_VALIDE},
            {"instruction": "comment  sécher  les  fèves ?", "output": _OUTPUT_VALIDE},
        ],
    )

    job = await service.preparer_finetuning()

    assert job is not None
    assert job["details"]["paires"] == 1
    assert job["details"]["rejetees"] == 1


async def test_preparer_erreur_inattendue(tmp_path: Path, monkeypatch) -> None:
    service, jobs = _service(tmp_path)
    _ecrire(
        tmp_path / "corpus_cure.jsonl",
        [{"instruction": "Comment sécher les fèves ?", "output": _OUTPUT_VALIDE}],
    )

    def boom(_sources):
        raise RuntimeError("lecture impossible")

    monkeypatch.setattr(pipeline_module, "charger_paires", boom)

    job = await service.preparer_finetuning()

    assert job is not None
    assert job["statut"] == "echec"
    # OWASP : aucun détail interne exposé.
    assert "lecture impossible" not in job["message"]


async def test_preparer_anti_concurrence(tmp_path: Path) -> None:
    service, jobs = _service(tmp_path)
    await jobs.creer("finetuning_prepare")  # un job déjà en cours
    assert await service.preparer_finetuning() is None


async def test_from_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATASET_DIR", str(tmp_path))
    monkeypatch.setenv("EMBEDDINGS_URL", "http://embeddings:8001")
    monkeypatch.setenv("API_DEPLOYMENT", "api")
    jobs = JobsRegistry.from_env()
    service = PipelineService.from_env(jobs)
    assert service._api_deployment == "api"
    assert service._dataset_dir == tmp_path
