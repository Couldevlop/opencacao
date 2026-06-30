"""Tests de couverture ciblés : chemins async des CronJobs, k8s, documents, pipeline.

Tout est mocké (aucun cluster, réseau ni Redis réels) : on vérifie des branches
réelles (erreur, repli, cas limite), pas du simple « line-hitting ».
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from app.curation import enrichir, watchdog
from app.curation.documents import DocumentStore
from app.curation.k8s import ClusterClient, ClusterIndisponible

# --- watchdog.executer() (chemin async, cluster mocké) ---


class _FauxClientWatchdog:
    """Client cluster simulé pour le watchdog : retourne un CronJob ou échoue."""

    def __init__(self, cronjob: dict | None = None, erreur: Exception | None = None) -> None:
        self.namespace = "opencacao"
        self._cronjob = cronjob
        self._erreur = erreur
        self.ferme = False

    async def get_json(self, chemin: str) -> dict:
        if self._erreur is not None:
            raise self._erreur
        return self._cronjob or {}

    async def close(self) -> None:
        self.ferme = True


async def test_watchdog_executer_alerte_si_suspendu(monkeypatch) -> None:
    """Un CronJob suspendu déclenche un envoi d'email et retourne True."""
    faux = _FauxClientWatchdog(
        cronjob={
            "metadata": {"name": "enrichissement-rag"},
            "spec": {"suspend": True},
            "status": {},
        }
    )
    monkeypatch.setattr(ClusterClient, "from_serviceaccount", classmethod(lambda cls: faux))
    envois: list[tuple[str, str]] = []

    async def faux_envoyer(sujet: str, corps: str) -> None:
        envois.append((sujet, corps))

    monkeypatch.setattr(watchdog.email, "envoyer_alerte", faux_envoyer)

    assert await watchdog.executer() is True
    assert len(envois) == 1
    assert "SUSPENDU" in envois[0][1]
    assert faux.ferme is True  # le client est bien refermé (finally)


async def test_watchdog_executer_ok_pas_d_alerte(monkeypatch) -> None:
    """Un CronJob sain ne déclenche aucun email et retourne False."""
    recent = (datetime.now(UTC) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    faux = _FauxClientWatchdog(
        cronjob={
            "metadata": {"name": "enrichissement-rag"},
            "spec": {},
            "status": {"lastSuccessfulTime": recent},
        }
    )
    monkeypatch.setattr(ClusterClient, "from_serviceaccount", classmethod(lambda cls: faux))
    appele = False

    async def faux_envoyer(sujet: str, corps: str) -> None:
        nonlocal appele
        appele = True

    monkeypatch.setattr(watchdog.email, "envoyer_alerte", faux_envoyer)

    assert await watchdog.executer() is False
    assert appele is False


async def test_watchdog_executer_hors_cluster(monkeypatch) -> None:
    """Hors cluster (pas de ServiceAccount), on journalise et on retourne False."""

    def boom(cls):
        raise ClusterIndisponible("hors cluster")

    monkeypatch.setattr(ClusterClient, "from_serviceaccount", classmethod(boom))
    assert await watchdog.executer() is False


async def test_watchdog_executer_lecture_echec(monkeypatch) -> None:
    """Une lecture du CronJob qui échoue (RBAC/réseau) retourne False sans email."""
    faux = _FauxClientWatchdog(erreur=ClusterIndisponible("403"))
    monkeypatch.setattr(ClusterClient, "from_serviceaccount", classmethod(lambda cls: faux))
    assert await watchdog.executer() is False
    assert faux.ferme is True  # fermé malgré l'erreur (finally)


async def test_watchdog_executer_respecte_env(monkeypatch) -> None:
    """Le nom du CronJob et le seuil d'âge proviennent de l'environnement."""
    monkeypatch.setenv("ENRICH_CRONJOB", "mon-cron")
    monkeypatch.setenv("WATCHDOG_MAX_AGE_H", "1")
    ancien = (datetime.now(UTC) - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    vu: dict = {}

    class _Client(_FauxClientWatchdog):
        async def get_json(self, chemin: str) -> dict:
            vu["chemin"] = chemin
            return {
                "metadata": {"name": "mon-cron"},
                "spec": {},
                "status": {"lastSuccessfulTime": ancien},
            }

    monkeypatch.setattr(ClusterClient, "from_serviceaccount", classmethod(lambda cls: _Client()))

    async def faux_envoyer(sujet: str, corps: str) -> None:
        vu["sujet"] = sujet

    monkeypatch.setattr(watchdog.email, "envoyer_alerte", faux_envoyer)

    assert await watchdog.executer() is True  # 5 h > seuil 1 h -> alerte
    assert "mon-cron" in vu["chemin"]
    assert "mon-cron" in vu["sujet"]


# --- enrichir._executer_supervise() (alerte email en cas d'échec) ---


async def test_enrichir_supervise_alerte_et_releve(monkeypatch) -> None:
    """Si l'enrichissement échoue, on envoie une alerte PUIS on relève l'exception."""

    async def faux_executer() -> None:
        raise RuntimeError("embeddings hs")

    monkeypatch.setattr(enrichir, "executer", faux_executer)
    envois: list[tuple[str, str]] = []

    async def faux_envoyer(sujet: str, corps: str) -> None:
        envois.append((sujet, corps))

    monkeypatch.setattr(enrichir.email, "envoyer_alerte", faux_envoyer)

    with pytest.raises(RuntimeError):
        await enrichir._executer_supervise()
    assert len(envois) == 1
    assert "embeddings hs" in envois[0][1]


async def test_enrichir_supervise_succes_pas_d_alerte(monkeypatch) -> None:
    """En cas de succès, aucune alerte n'est envoyée."""

    async def faux_executer() -> None:
        return None

    monkeypatch.setattr(enrichir, "executer", faux_executer)
    appele = False

    async def faux_envoyer(sujet: str, corps: str) -> None:
        nonlocal appele
        appele = True

    monkeypatch.setattr(enrichir.email, "envoyer_alerte", faux_envoyer)
    await enrichir._executer_supervise()
    assert appele is False


# --- k8s.get_json (lecture API server, transport simulé) ---


def _client_k8s(handler) -> ClusterClient:
    transport = httpx.MockTransport(handler)
    return ClusterClient(
        hote="https://kube",
        namespace="opencacao",
        token="jeton",
        verify=False,
        client=httpx.AsyncClient(transport=transport),
    )


async def test_get_json_ok() -> None:
    """get_json lit une ressource et renvoie son JSON, avec le bon en-tête d'auth."""
    vu: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        vu["url"] = str(request.url)
        vu["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"kind": "CronJob"})

    client = _client_k8s(handler)
    data = await client.get_json("/apis/batch/v1/namespaces/opencacao/cronjobs/x")
    await client.close()
    assert data == {"kind": "CronJob"}
    assert vu["auth"] == "Bearer jeton"
    assert vu["url"].endswith("/cronjobs/x")


async def test_get_json_erreur_leve_indisponible() -> None:
    """Un refus de l'API server (404) lève ClusterIndisponible."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "absent"})

    client = _client_k8s(handler)
    with pytest.raises(ClusterIndisponible):
        await client.get_json("/apis/batch/v1/namespaces/opencacao/cronjobs/absent")
    await client.close()


async def test_http_cree_le_client_paresseusement() -> None:
    """Sans client injecté, _http() en crée un (création paresseuse), refermé par close()."""
    client = ClusterClient(hote="https://kube", namespace="ns", token="t", verify=False)
    assert client._client is None
    interne = client._http()
    assert interne is not None
    assert client._http() is interne  # mémoïsé
    await client.close()


async def test_close_sans_client_ne_fait_rien() -> None:
    """close() est sûr même si aucun client HTTP n'a été créé."""
    client = ClusterClient(hote="https://kube", namespace="ns", token="t", verify=False)
    await client.close()  # ne doit pas lever


# --- documents : extraction PDF + branches d'erreur ---


def test_extraire_pdf_via_pypdf(tmp_path: Path, monkeypatch) -> None:
    """extraire_texte délègue à pypdf pour un .pdf (pypdf mocké, pas de vrai PDF)."""
    from app.curation import documents as documents_module

    class _FakePage:
        def extract_text(self) -> str:
            return "Le cacao en Côte d'Ivoire."

    class _FakeReader:
        def __init__(self, _chemin: str) -> None:
            self.pages = [_FakePage(), _FakePage()]

    import pypdf

    monkeypatch.setattr(pypdf, "PdfReader", _FakeReader)
    chemin = tmp_path / "doc.pdf"
    chemin.write_bytes(b"%PDF")
    texte = documents_module.extraire_texte(chemin)
    assert texte.count("Le cacao") == 2


def test_extraits_ignore_repertoires_et_documents_illisibles(tmp_path: Path, monkeypatch) -> None:
    """Un document illisible n'interrompt pas l'extraction du reste ; sous-dossiers ignorés."""
    from app.curation import documents as documents_module

    store = DocumentStore(tmp_path / "documents")
    long_texte = "Le cacaoyer aime l'ombre et l'humidite en Cote d'Ivoire. " * 10
    store.enregistrer("ok.txt", long_texte.encode("utf-8"))
    store.enregistrer("casse.txt", long_texte.encode("utf-8"))
    # Un sous-répertoire dans le dossier des documents doit être ignoré.
    (tmp_path / "documents" / "sous_dossier").mkdir()

    vrai_extraire = documents_module.extraire_texte

    def extraire_partiel(chemin: Path) -> str:
        if chemin.name == "casse.txt":
            raise ValueError("PDF corrompu")
        return vrai_extraire(chemin)

    monkeypatch.setattr(documents_module, "extraire_texte", extraire_partiel)
    extraits = store.extraits()
    sources = {nom for nom, _ in extraits}
    assert sources == {"ok.txt"}  # casse.txt sauté, mais ok.txt extrait


def test_extraits_dossier_absent(tmp_path: Path) -> None:
    """Sans dossier de documents, extraits() retourne une liste vide."""
    store = DocumentStore(tmp_path / "inexistant")
    assert store.extraits() == []


def test_archiver_remplace_archive_existante(tmp_path: Path) -> None:
    """Archiver écrase une archive du même nom (pas d'accumulation de doublons)."""
    store = DocumentStore(tmp_path / "documents")
    store.enregistrer("guide.txt", b"version 1 du guide cacao")
    assert store.archiver() == 1
    # Réenregistre puis ré-archive : l'ancienne archive est remplacée.
    store.enregistrer("guide.txt", b"version 2 du guide cacao")
    assert store.archiver() == 1
    archive = tmp_path / "documents_archive" / "guide.txt"
    assert archive.read_bytes() == b"version 2 du guide cacao"


def test_archiver_ignore_sous_repertoires(tmp_path: Path) -> None:
    """Un sous-répertoire dans le dossier actif n'est pas archivé (seuls les fichiers)."""
    store = DocumentStore(tmp_path / "documents")
    store.enregistrer("doc.txt", b"contenu du document cacao")
    (tmp_path / "documents" / "sous").mkdir()
    assert store.archiver() == 1  # seul le fichier compte


# --- Points d'entrée CLI des CronJobs (main wrappers, asyncio.run mocké) ---


def test_watchdog_main(monkeypatch) -> None:
    """main() configure le log et lance executer() via asyncio.run."""
    appels: list[str] = []
    monkeypatch.setattr(watchdog, "configure_logging", lambda niveau: appels.append(niveau))
    monkeypatch.setattr(watchdog.asyncio, "run", lambda coro: appels.append("run") or coro.close())
    watchdog.main()
    assert "run" in appels


def test_enrichir_main(monkeypatch) -> None:
    """main() configure le log et lance l'enrichissement supervisé via asyncio.run."""
    appels: list[str] = []
    monkeypatch.setattr(enrichir, "configure_logging", lambda niveau: appels.append(niveau))
    monkeypatch.setattr(enrichir.asyncio, "run", lambda coro: appels.append("run") or coro.close())
    enrichir.main()
    assert "run" in appels


# --- contacts : annuaire illisible et siège absent ---


def test_contacts_annuaire_illisible(monkeypatch, tmp_path: Path) -> None:
    """Un annuaire YAML absent/illisible renvoie {} (pas d'erreur), sans contact trouvé."""
    from app.services import contacts, localites

    # chercher() délègue désormais à localites ; siege() utilise encore contacts.
    # On rend donc les DEUX annuaires illisibles pour vérifier la dégradation complète.
    contacts._annuaire.cache_clear()
    localites._annuaire.cache_clear()
    localites._index.cache_clear()
    absent = tmp_path / "absent.yaml"
    monkeypatch.setattr(contacts, "_CHEMIN", absent)
    monkeypatch.setattr(localites, "_CHEMIN", absent)
    assert contacts._annuaire() == {}
    assert contacts.chercher("Je suis à Bouaké") is None
    assert contacts.siege() is None  # pas de siège dans un annuaire vide
    # Restaure les caches pour les autres tests (relecture du vrai fichier).
    contacts._annuaire.cache_clear()
    localites._annuaire.cache_clear()
    localites._index.cache_clear()
