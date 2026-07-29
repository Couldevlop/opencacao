"""Tests de couverture ciblée de la console de curation (branches défensives).

Ce module complète les suites existantes (``test_curation*.py``,
``test_documents.py``, ``test_decouverte.py``, ``test_jobs.py``, ``test_k8s.py``,
``test_sources.py``) sur les chemins d'erreur et de refus qui n'étaient pas
exercés : identification du client derrière l'ingress, jetons de session
malformés, divulgation de bannière serveur, refus d'ajout/suppression de
document, robustesse des journaux JSONL partiellement corrompus, garde-fou
anti-SSRF face à une entrée invalide, et bornes de la découverte de sources.

Aucun appel réseau : la découverte et le client cluster passent par
``httpx.MockTransport``, la résolution DNS est substituée.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import socket
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import Response as StarletteResponse

import app.curation.main as cur
from app.curation import decouverte, sources
from app.curation.decouverte import decouvrir
from app.curation.documents import DocumentInvalide, DocumentStore
from app.curation.jobs import JobsRegistry
from app.curation.k8s import ClusterClient
from app.curation.ratelimit import LimiteurConnexion
from app.curation.store import CurationStore, ValidationInvalide

# --- Montage de la console ------------------------------------------------


class PipelineRefusant:
    """Pipeline simulé qui refuse l'ajout par URL avec un motif explicite.

    Reproduit le refus anti-SSRF réel (``DocumentInvalide``) sans aucun accès
    réseau ni résolution DNS.
    """

    def __init__(self, motif: str = "URL non autorisée (hôte interne ou injoignable).") -> None:
        """Initialise le pipeline simulé.

        Args:
            motif: Message porté par l'exception de refus.
        """
        self._motif = motif

    async def ajouter_document_url(self, url: str) -> dict | None:
        """Refuse systématiquement l'URL proposée.

        Args:
            url: URL soumise par le curateur.

        Raises:
            DocumentInvalide: Toujours, avec le motif configuré.
        """
        raise DocumentInvalide(self._motif)


@pytest.fixture
def console_documents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Console ouverte (sans mot de passe) dont le pipeline refuse les URLs."""
    monkeypatch.setattr(cur, "_MOT_DE_PASSE", "")
    monkeypatch.setattr(cur, "_documents", DocumentStore(tmp_path / "documents"))
    monkeypatch.setattr(cur, "_pipeline", PipelineRefusant())
    return TestClient(cur.app)


def _console_protegee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Console protégée par mot de passe, avec un limiteur de connexion neuf."""
    monkeypatch.setattr(cur, "_store", CurationStore(tmp_path, tmp_path / "corpus_cure.jsonl"))
    monkeypatch.setattr(cur, "_MOT_DE_PASSE", "secret")
    monkeypatch.setattr(cur, "_UTILISATEUR", "curateur")
    monkeypatch.setattr(cur, "_LIMITEUR_LOGIN", LimiteurConnexion(max_echecs=2))
    return TestClient(cur.app)


# --- main.py : identification du client derrière l'ingress ----------------


def test_le_blocage_du_login_vise_le_client_reel_et_non_le_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le compteur anti-brute-force est tenu par IP cliente (1ᵉʳ hop X-Forwarded-For).

    Derrière l'ingress, toutes les requêtes arrivent de la même IP TCP : sans
    lecture du premier hop, un seul attaquant bloquerait tous les curateurs, ou
    resterait indétectable en variant la fin de la chaîne de proxys.
    """
    client = _console_protegee(tmp_path, monkeypatch)  # seuil : 2 échecs
    mauvais = {"utilisateur": "curateur", "mot_de_passe": "faux"}

    attaquant = {"x-forwarded-for": "203.0.113.7, 172.16.0.1"}
    for _ in range(2):
        assert client.post("/api/login", json=mauvais, headers=attaquant).status_code == 401

    # Même client, chaîne de proxys différente : c'est bien le 1ᵉʳ hop qui compte.
    memes_client_autre_proxy = {"x-forwarded-for": "203.0.113.7, 10.0.0.5"}
    assert (
        client.post("/api/login", json=mauvais, headers=memes_client_autre_proxy).status_code == 429
    )

    # Un autre curateur, derrière le même proxy, n'est pas pénalisé.
    innocent = {"x-forwarded-for": "198.51.100.9, 172.16.0.1"}
    assert client.post("/api/login", json=mauvais, headers=innocent).status_code == 401


# --- main.py : jetons de session malformés --------------------------------


def test_un_jeton_signe_dont_l_expiration_n_est_pas_un_nombre_est_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une signature valide ne suffit pas : une expiration illisible refuse l'accès.

    Un jeton dont l'expiration n'est pas un entier ne peut pas être comparé à
    l'heure courante ; il doit être traité comme invalide (jamais comme
    « n'expire pas »).
    """
    expiration_illisible = "jamais"
    signature = hmac.new(cur._SECRET, expiration_illisible.encode(), hashlib.sha256).hexdigest()
    jeton = f"{expiration_illisible}.{signature}"

    assert cur._token_valide(jeton) is False

    client = _console_protegee(tmp_path, monkeypatch)
    # Témoin : un jeton bien formé, transmis de la même façon, ouvre bien l'accès.
    temoin = {"cookie": f"{cur._COOKIE}={cur._creer_token()}"}
    assert client.get("/api/stats", headers=temoin).status_code == 200

    malforme = {"cookie": f"{cur._COOKIE}={jeton}"}
    assert client.get("/api/stats", headers=malforme).status_code == 401
    assert client.get("/api/session", headers=malforme).json()["authentifie"] is False


# --- main.py : en-têtes de sécurité ---------------------------------------


async def test_la_banniere_du_serveur_est_retiree_des_reponses() -> None:
    """La console ne divulgue jamais l'en-tête ``Server`` (fingerprinting OWASP).

    Le middleware est appelé directement : l'en-tête ``Server`` est posé par le
    serveur ASGI en production, jamais par le client de test.
    """
    requete = Request({"type": "http", "method": "GET", "path": "/api/sante", "headers": []})

    async def call_next(_: Request) -> StarletteResponse:
        return StarletteResponse("ok", headers={"server": "uvicorn/0.30"})

    reponse = await cur._entetes_securite(requete, call_next)

    assert "server" not in reponse.headers
    assert reponse.headers["x-frame-options"] == "DENY"  # les en-têtes OWASP restent posés


# --- main.py : fin de session ---------------------------------------------


def test_la_deconnexion_efface_le_cookie_de_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Se déconnecter efface le cookie de session du navigateur.

    Sur un poste partagé, quitter la console doit réellement retirer le jeton :
    la réponse doit expirer le cookie sur tout le site (``Path=/``).
    """
    client = _console_protegee(tmp_path, monkeypatch)
    assert (
        client.post(
            "/api/login", json={"utilisateur": "curateur", "mot_de_passe": "secret"}
        ).status_code
        == 200
    )
    assert cur._COOKIE in client.cookies  # session ouverte

    reponse = client.post("/api/logout")

    assert reponse.status_code == 200
    assert reponse.json() == {"ok": True}
    entete = reponse.headers.get("set-cookie", "")
    assert "Max-Age=0" in entete and "Path=/" in entete  # expiration immédiate, tout le site
    assert cur._COOKIE not in client.cookies  # le navigateur a bien purgé le jeton


# --- main.py : refus sur les documents ------------------------------------


def test_ajout_par_url_refuse_renvoie_422_avec_le_motif(console_documents: TestClient) -> None:
    """Une URL écartée par le garde-fou est refusée en 422, motif à l'appui.

    Le curateur doit comprendre pourquoi l'ajout est refusé (hôte interne,
    format inexploitable) ; ce n'est pas une panne (5xx) mais un refus.
    """
    reponse = console_documents.post(
        "/api/documents/url", json={"url": "http://inference.opencacao.svc/"}
    )

    assert reponse.status_code == 422
    assert "non autorisée" in reponse.json()["detail"]


def test_suppression_d_un_nom_non_documentaire_est_refusee(console_documents: TestClient) -> None:
    """Supprimer un nom qui n'est pas un document accepté est refusé en 422.

    La suppression ne doit jamais s'appliquer à un chemin arbitraire du volume
    partagé : le nom est validé avant toute action sur le disque.
    """
    reponse = console_documents.request("DELETE", "/api/documents/id_rsa")

    assert reponse.status_code == 422
    assert "format non supporté" in reponse.json()["detail"]


# --- store.py : robustesse des journaux et bornes du corpus ---------------


def test_un_journal_partiellement_corrompu_reste_exploitable(tmp_path: Path) -> None:
    """Lignes vides et lignes illisibles sont ignorées, les autres restent curables.

    Le journal est écrit en continu par l'API : une ligne tronquée (pod tué en
    plein écriture) ne doit pas rendre toute la curation inutilisable.
    """
    valide_1 = {
        "id": "a" * 8,
        "question": "Quand récolter le cacao ?",
        "reponse": "Quand les cabosses sont mûres.",
        "confiance": "faible",
        "sources": [],
        "redirection_anader": False,
    }
    valide_2 = {**valide_1, "id": "b" * 8, "question": "Comment sécher les fèves ?"}
    (tmp_path / "interactions.jsonl").write_text(
        json.dumps(valide_1, ensure_ascii=False)
        + "\n"
        + "\n"  # ligne vide (fin de fichier / écriture partielle)
        + "   \n"  # ligne blanche
        + '{"id": "cccccccc", "question": tronq\n'  # ligne illisible
        + json.dumps(valide_2, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )

    store = CurationStore(tmp_path, tmp_path / "corpus_cure.jsonl")

    assert {item["id"] for item in store.a_curer()} == {"a" * 8, "b" * 8}
    assert store.statistiques()["total"] == 2


async def test_une_instruction_trop_courte_est_refusee_avec_ses_bornes(tmp_path: Path) -> None:
    """Une instruction hors bornes du corpus est refusée, bornes attendues à l'appui.

    Toute paire validée ici doit passer la validation d'entraînement : la
    console refuse en amont plutôt que de faire échouer l'assemblage plus tard.
    """
    store = CurationStore(tmp_path, tmp_path / "corpus_cure.jsonl")

    with pytest.raises(ValidationInvalide) as echec:
        await store.valider(
            "a" * 8,
            "Cacao ?",  # 7 caractères, sous le minimum de 10
            "Récoltez les cabosses bien mûres et colorées en saison sèche. Sources : CNRA.",
        )

    assert "instruction hors bornes" in str(echec.value)
    assert "10-500" in str(echec.value)
    assert not (tmp_path / "corpus_cure.jsonl").exists()  # rien n'est versé au corpus


# --- documents.py : archivage à vide --------------------------------------


def test_archiver_sans_aucun_document_ne_cree_rien(tmp_path: Path) -> None:
    """Archiver alors qu'aucun document n'a été téléversé retourne 0 sans effet de bord."""
    store = DocumentStore(tmp_path / "documents")

    assert store.archiver() == 0
    assert not (tmp_path / "documents").exists()
    assert not (tmp_path / "documents_archive").exists()  # pas d'archive vide créée


# --- jobs.py : robustesse du registre -------------------------------------


async def test_le_registre_de_jobs_ignore_les_lignes_vides(tmp_path: Path) -> None:
    """Une ligne vide dans le registre n'empêche pas de lister les jobs.

    Le fichier est réécrit en entier à chaque mise à jour ; une écriture
    interrompue peut y laisser une ligne vide sans que le suivi soit perdu.
    """
    chemin = tmp_path / "jobs.jsonl"
    chemin.write_text(
        json.dumps({"id": "a" * 16, "type": "rag_reindex", "statut": "reussi"}) + "\n"
        "\n" + json.dumps({"id": "b" * 16, "type": "rag_constitution", "statut": "echec"}) + "\n",
        encoding="utf-8",
    )
    registre = JobsRegistry(chemin)

    jobs = await registre.lister()

    assert [job["id"] for job in jobs] == ["b" * 16, "a" * 16]  # du plus récent au plus ancien
    assert await registre.obtenir("a" * 16) is not None


# --- k8s.py : namespace exposé --------------------------------------------


async def test_le_namespace_expose_est_celui_reellement_patche() -> None:
    """Le namespace annoncé par le client est celui utilisé pour patcher le déploiement.

    La console ne doit jamais redémarrer un déploiement d'un autre namespace que
    le sien (celui du ServiceAccount monté).
    """
    vu: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        vu["url"] = str(request.url)
        return httpx.Response(200, json={"ok": True})

    client = ClusterClient(
        hote="https://kube",
        namespace="opencacao",
        token="jeton",
        verify=False,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    assert client.namespace == "opencacao"
    await client.rollout_restart("api")
    await client.close()
    assert f"/namespaces/{client.namespace}/deployments/api" in vu["url"]


# --- sources.py : garde-fou anti-SSRF, cas d'échec ------------------------


def test_une_valeur_non_textuelle_est_refusee_par_le_garde_fou(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le garde-fou anti-SSRF échoue « fermé » sur une entrée qui n'est pas une URL.

    Aucune résolution DNS ne doit même être tentée : l'entrée est rejetée avant.
    """

    def dns_interdit(*_args: object, **_kwargs: object) -> list:
        raise AssertionError("aucune résolution DNS ne doit être tentée")

    monkeypatch.setattr(socket, "getaddrinfo", dns_interdit)

    assert sources.url_publique_sure(None) is False  # type: ignore[arg-type]
    assert sources.url_publique_sure(12345) is False  # type: ignore[arg-type]


def test_un_hote_non_resolvable_est_refuse(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un hôte dont le DNS ne répond pas est refusé (on ne télécharge pas à l'aveugle).

    Sans résolution, impossible de prouver que l'hôte est public : le garde-fou
    doit refuser plutôt que de laisser passer.
    """

    def dns_en_echec(*_args: object, **_kwargs: object) -> list:
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", dns_en_echec)

    assert sources.url_publique_sure("https://source-officielle-inexistante.ci/guide.pdf") is False


def test_une_url_malformee_est_refusee_sans_lever(monkeypatch: pytest.MonkeyPatch) -> None:
    """Une URL syntaxiquement invalide est refusée, jamais propagée en exception.

    La découverte parcourt des liens trouvés sur des sites tiers : un seul lien
    malformé ne doit pas interrompre toute la campagne de découverte.
    """
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))]
    )

    assert sources.url_publique_sure("http://x:port/guide.pdf") is False  # port invalide
    assert sources.url_publique_sure("http://xn--.com/guide.pdf") is False  # A-label malformé


# --- decouverte.py : bornes et filtres ------------------------------------


def _client_mock(pages: dict[str, bytes]) -> httpx.AsyncClient:
    """Client HTTP simulé servant ``pages`` par hôte (404 pour les hôtes absents).

    Args:
        pages: Contenu HTML à servir, indexé par hôte.

    Returns:
        Un client ``httpx`` branché sur un transport simulé (aucun réseau).
    """

    def handler(request: httpx.Request) -> httpx.Response:
        contenu = pages.get(request.url.host or "")
        if contenu is None:
            return httpx.Response(404)
        return httpx.Response(200, content=contenu, headers={"content-type": "text/html"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_une_page_de_depart_injoignable_n_interrompt_pas_la_decouverte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un site officiel hors ligne est sauté ; les autres sont quand même explorés."""
    monkeypatch.setattr(decouverte, "url_publique_sure", lambda _u: True)
    pages = {  # seul l'ANADER répond ; les autres seeds renvoient 404
        "www.anader.ci": b'<a href="/docs/fiche-cacao.pdf">fiche</a>',
    }
    client = _client_mock(pages)
    try:
        candidats = await decouvrir(client, DocumentStore(tmp_path / "documents"), max_docs=10)
    finally:
        await client.aclose()

    assert [c["url"] for c in candidats] == ["https://www.anader.ci/docs/fiche-cacao.pdf"]


async def test_un_lien_vers_un_hote_non_public_est_ecarte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un lien PDF pointant vers un hôte non public est écarté (anti-SSRF).

    Le domaine peut être dans la liste blanche et néanmoins résoudre vers une
    adresse interne : les deux garde-fous s'appliquent.
    """
    monkeypatch.setattr(decouverte, "url_publique_sure", lambda url: "interne" not in url)
    pages = {
        "cnra.ci": (
            b'<a href="https://cnra.ci/docs/guide-public.pdf">public</a>'
            b'<a href="https://cnra.ci/interne/inventaire.pdf">interne</a>'
        )
    }
    client = _client_mock(pages)
    try:
        candidats = await decouvrir(client, DocumentStore(tmp_path / "documents"), max_docs=10)
    finally:
        await client.aclose()

    urls = {c["url"] for c in candidats}
    assert urls == {"https://cnra.ci/docs/guide-public.pdf"}


async def test_la_decouverte_s_arrete_au_plafond_de_candidats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La découverte ne retourne jamais plus de candidats que le plafond fixé.

    Une page officielle peut lister des centaines de PDF : le téléchargement qui
    suit doit rester borné.
    """
    monkeypatch.setattr(decouverte, "url_publique_sure", lambda _u: True)
    proposes = {
        "https://cnra.ci/docs/guide-1.pdf",
        "https://cnra.ci/docs/guide-2.pdf",
        "https://cnra.ci/docs/guide-3.pdf",
    }
    pages = {"cnra.ci": "".join(f'<a href="{u}">x</a>' for u in proposes).encode()}
    client = _client_mock(pages)
    try:
        candidats = await decouvrir(client, DocumentStore(tmp_path / "documents"), max_docs=2)
    finally:
        await client.aclose()

    assert len(candidats) == 2
    assert {c["url"] for c in candidats} <= proposes
