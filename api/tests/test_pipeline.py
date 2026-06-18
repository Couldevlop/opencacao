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


async def test_constituer_embeddings_en_panne(tmp_path: Path) -> None:
    service, jobs = _service(tmp_path, FakeEmbeddings(en_panne=True))
    _doc(tmp_path, "guide.txt", _DOC_TEXTE)
    job = await jobs.creer("rag_constitution")
    await service.constituer_rag(job["id"])
    relu = await jobs.obtenir(job["id"])
    assert relu["statut"] == "echec"
    assert not (tmp_path / "rag_index.jsonl").exists()


async def test_constituer_anti_concurrence(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    assert await service.demarrer_constitution() is not None
    assert await service.demarrer_constitution() is None


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
