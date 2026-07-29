"""Tests du noyau et de l'infrastructure : cycle de vie, cache dégradé, en-têtes.

Ce module couvre les chemins « d'exploitation » que les tests fonctionnels ne
traversent jamais : arrêt propre de l'application (annulation des tâches de fond,
fermeture des clients sortants), tolérance du cache à une panne Redis, non-
divulgation du serveur, robustesse du flux SSE et dégradation de la géolocalisation.

Aucun appel réseau : Redis est simulé en mémoire, l'inférence via
``httpx.MockTransport``, et le pré-chauffage comme le keepalive sont remplacés par
des tâches factices.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import redis.asyncio as redis
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from app import main
from app.core.cache import CacheClient, _cosinus
from app.core.config import Settings, get_settings
from app.core.security import SecurityHeadersMiddleware
from app.main import _lancer_purge_sessions, _monter_interface, create_app
from app.services.geo import GeoLocalisateur
from app.services.inference import InferenceClient
from app.services.prompts import build_messages

# ---------------------------------------------------------------------------
# Doublures Redis
# ---------------------------------------------------------------------------


class RedisMemoire:
    """Redis asynchrone simulé en mémoire, avec mémorisation des TTL posés."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttl: dict[str, int | None] = {}
        self.hashes: dict[str, dict[str, str]] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value
        self.ttl[key] = ex

    async def hset(self, key: str, field: str, value: str) -> None:
        self.hashes.setdefault(key, {})[field] = value

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))

    async def hdel(self, key: str, *fields: str) -> None:
        bucket = self.hashes.get(key, {})
        for field in fields:
            bucket.pop(field, None)

    async def hlen(self, key: str) -> int:
        return len(self.hashes.get(key, {}))

    async def expire(self, key: str, seconds: int) -> None:
        self.ttl[key] = seconds


class RedisInjoignable:
    """Redis dont chaque opération échoue (panne / coupure réseau simulée)."""

    async def get(self, key: str) -> str:
        raise redis.RedisError("connexion refusée")

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        raise redis.RedisError("connexion refusée")


# ---------------------------------------------------------------------------
# app/core/cache.py — similarité cosinus et cache d'outils
# ---------------------------------------------------------------------------


def test_cosinus_refuse_les_vecteurs_de_dimensions_differentes() -> None:
    """Deux vecteurs de dimensions différentes ne sont jamais « proches » (0.0).

    Cas réel : la migration du modèle d'embeddings 4B (2560 dimensions) vers 0.6B
    (1024) laisse dans l'index des vecteurs de l'ancienne dimension.
    """
    assert _cosinus([1.0, 0.0, 0.0], [1.0, 0.0]) == 0.0


def test_cosinus_refuse_un_vecteur_nul() -> None:
    """Un vecteur nul n'est colinéaire à rien : la similarité vaut 0.0.

    Sans ce garde-fou, la norme nulle provoquerait une division par zéro.
    """
    assert _cosinus([0.0, 0.0, 0.0], [1.0, 0.0, 0.0]) == 0.0
    assert _cosinus([1.0, 0.0, 0.0], [0.0, 0.0, 0.0]) == 0.0


async def test_index_semantique_ignore_les_vecteurs_d_une_autre_dimension() -> None:
    """Un vecteur indexé par un ancien modèle d'embeddings ne produit aucun hit.

    Exigence : changer de modèle d'embeddings ne doit jamais faire resservir une
    réponse au hasard — au pire un miss, jamais une réponse hors sujet.
    """
    faux_redis = RedisMemoire()
    cache = CacheClient(faux_redis, rate_limit_per_min=20)
    await cache.set_cached("Comment tailler le cacaoyer ?", "fr", '{"reponse": "Taillez."}')
    # Entrée héritée de l'ancien modèle : 4 dimensions au lieu de 3.
    await cache.index_semantic("Comment tailler le cacaoyer ?", "fr", [1.0, 0.0, 0.0, 0.0])

    assert await cache.get_semantic("fr", [1.0, 0.0, 0.0], threshold=0.92) is None


async def test_cache_outil_relit_le_resultat_et_pose_son_propre_ttl() -> None:
    """Un résultat d'outil est relu tel quel, avec le TTL demandé par l'appelant.

    Météo et alertes satellite ont des durées de fraîcheur très différentes : le TTL
    est fourni à l'appel, il n'est pas celui du cache de réponses.
    """
    faux_redis = RedisMemoire()
    cache = CacheClient(faux_redis, rate_limit_per_min=20)
    await cache.set_outil("meteo:daloa", '{"pluie_mm": 12}', ttl_s=1800)

    assert await cache.get_outil("meteo:daloa") == '{"pluie_mm": 12}'
    assert faux_redis.ttl["outil:meteo:daloa"] == 1800
    assert await cache.get_outil("meteo:soubre") is None  # clé absente -> miss


async def test_cache_outil_n_est_pas_cloisonne_par_version() -> None:
    """Un déploiement d'image ou de modèle ne jette pas le cache des outils.

    La météo d'une localité ne dépend ni du modèle ni du post-traitement : elle
    reste valable après un redéploiement (contrairement aux réponses générées).
    """
    partage = RedisMemoire()
    avant = CacheClient(partage, rate_limit_per_min=20, model_version="1.0.0", app_version="0.6.1")
    apres = CacheClient(partage, rate_limit_per_min=20, model_version="2.0.0", app_version="0.7.0")
    await avant.set_outil("meteo:daloa", '{"pluie_mm": 12}', ttl_s=1800)

    assert await apres.get_outil("meteo:daloa") == '{"pluie_mm": 12}'


async def test_cache_outil_tolere_un_redis_injoignable() -> None:
    """Un Redis injoignable dégrade le cache d'outils en miss, sans lever d'erreur.

    L'appel d'outil sera simplement refait : une panne de cache ne doit jamais
    rendre l'API indisponible.
    """
    cache = CacheClient(RedisInjoignable(), rate_limit_per_min=20)

    assert await cache.get_outil("meteo:daloa") is None
    assert await cache.set_outil("meteo:daloa", '{"pluie_mm": 12}', ttl_s=1800) is None


# ---------------------------------------------------------------------------
# app/core/security.py — non-divulgation du serveur
# ---------------------------------------------------------------------------


def test_l_entete_server_est_retire_des_reponses() -> None:
    """L'API ne divulgue pas le serveur qui la sert (OWASP : fingerprinting).

    Le serveur ASGI pose un en-tête ``Server`` : le middleware doit le retirer.
    """
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/x")
    async def _x(reponse: Response) -> dict[str, bool]:
        reponse.headers["server"] = "uvicorn"
        return {"ok": True}

    reponse = TestClient(app).get("/x")
    assert reponse.status_code == 200
    assert "server" not in reponse.headers


# ---------------------------------------------------------------------------
# app/services/inference.py — robustesse du flux SSE
# ---------------------------------------------------------------------------


async def test_flux_sse_ignore_les_trames_corrompues() -> None:
    """Une trame SSE illisible est sautée : la réponse en cours n'est pas interrompue.

    Un producteur ne doit pas perdre sa réponse parce qu'un fragment de flux est
    tronqué ou mal formé.
    """
    flux = (
        'data: {"choices":[{"delta":{"content":"Étalez les fèves"}}]}\n\n'
        "data: ceci n'est pas du json\n\n"  # JSONDecodeError
        'data: {"choix": []}\n\n'  # KeyError : pas de "choices"
        'data: {"choices":[]}\n\n'  # IndexError : liste vide
        'data: {"choices":[{"delta":{"content":" au soleil."}}]}\n\n'
        "data: [DONE]\n\n"
    )

    def repondre(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=flux.encode(), headers={"content-type": "text/event-stream"}
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(repondre))
    client = InferenceClient("http://inference:8000", "opencacao-8b", 10.0, client=http)
    morceaux = [m async for m in client.generer_stream("Comment sécher mes fèves ?")]

    assert "".join(morceaux) == "Étalez les fèves au soleil."
    await client.close()


# ---------------------------------------------------------------------------
# app/services/prompts.py — alternance des rôles
# ---------------------------------------------------------------------------


def test_question_fusionnee_avec_un_historique_finissant_par_l_utilisateur() -> None:
    """Un historique se terminant par l'utilisateur absorbe la question courante.

    Le template de chat de Ministral 3 refuse deux messages ``user`` consécutifs
    (« conversation roles must alternate ») : l'API renverrait un 503. On fusionne
    donc les deux tours plutôt que d'en ajouter un.
    """
    historique = [
        {"role": "user", "content": "Mes feuilles jaunissent"},
        {"role": "assistant", "content": "Dans quelle localité ?"},
        {"role": "user", "content": "À Daloa"},  # clarification sans réponse assistant
    ]
    messages = build_messages("Que faire ?", historique=historique)

    roles = [m["role"] for m in messages[1:]]
    assert roles == ["user", "assistant", "user"]  # aucun doublon de rôle
    # Le dernier tour utilisateur porte la clarification ET la question courante.
    assert messages[-1]["content"].startswith("À Daloa\n")
    assert "Que faire ?" in messages[-1]["content"]


# ---------------------------------------------------------------------------
# app/services/geo.py — dégradation sans base GeoLite2
# ---------------------------------------------------------------------------


def test_geolocalisation_absente_ne_bloque_jamais_le_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sans base GeoLite2 sur le nœud, la localisation renvoie "" au lieu d'échouer.

    L'analytique des visites est accessoire : son indisponibilité ne doit ni lever
    d'exception ni relancer d'ouverture à chaque requête (une seule tentative). La
    bibliothèque est simulée pour que le test vaille aussi là où elle est installée.
    """

    def _interdit(chemin: str) -> object:
        raise AssertionError("aucune base à ouvrir : le fichier n'existe pas")

    monkeypatch.setitem(sys.modules, "maxminddb", SimpleNamespace(open_database=_interdit))
    absente = tmp_path / "GeoLite2-Country.mmdb"
    geo = GeoLocalisateur(absente)

    assert geo.pays("1.2.3.4") == ""
    assert geo.localiser("1.2.3.4") == ("", "")

    # Une base déposée après coup n'est pas rechargée : la tentative est unique.
    absente.write_bytes(b"contenu invalide")
    assert geo.pays("1.2.3.4") == ""


# ---------------------------------------------------------------------------
# app/main.py — cycle de vie
# ---------------------------------------------------------------------------


@pytest.fixture
def env_api_isolee(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Isole l'application : ni pré-chauffage, ni keepalive, ni écriture dans /data."""
    monkeypatch.setenv("PREWARM_ENABLED", "false")
    monkeypatch.setenv("KV_KEEPALIVE_S", "0")
    monkeypatch.setenv("SESSIONS_DB_PATH", str(tmp_path / "sessions.db"))
    monkeypatch.setenv("PARCELLES_DB_PATH", str(tmp_path / "parcelles.db"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_les_taches_de_fond_sont_annulees_a_l_arret(
    env_api_isolee: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L'arrêt annule le pré-chauffage et le keepalive : aucune tâche ne survit.

    Ces deux boucles sont infinies. Si l'arrêt ne les annulait pas, elles
    continueraient d'appeler l'inférence pendant la fermeture du processus.
    """
    annulees: set[str] = set()

    async def faux_prechauffage(service: object, questions: object) -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            annulees.add("prechauffage")
            raise

    async def faux_keepalive(inference: object, periode_s: int) -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            annulees.add("keepalive")
            raise

    monkeypatch.setenv("PREWARM_ENABLED", "true")
    monkeypatch.setenv("KV_KEEPALIVE_S", "600")
    get_settings.cache_clear()
    monkeypatch.setattr("app.application.prewarm.prechauffer_cache", faux_prechauffage)
    monkeypatch.setattr("app.application.keepalive.boucle_keepalive", faux_keepalive)

    app = create_app()
    with TestClient(app) as client:
        assert client.get("/v1/health").status_code == 200
        assert app.state.prewarm_task is not None
        assert app.state.keepalive_task is not None

    assert annulees == {"prechauffage", "keepalive"}


def test_l_arret_ferme_les_clients_sortants(
    env_api_isolee: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L'arrêt ferme le client d'embeddings et le notifier (pas de socket pendante).

    Ces deux clients ne sont construits que dans certaines configurations (RAG actif,
    notifier email) : leur fermeture est conditionnelle et doit rester garantie.
    """

    class _ClientFermable:
        def __init__(self) -> None:
            self.ferme = False

        async def close(self) -> None:
            self.ferme = True

    embeddings = _ClientFermable()
    notifier = _ClientFermable()
    monkeypatch.setattr(main, "_construire_rag", lambda settings: (embeddings, None))
    monkeypatch.setattr(main, "construire_notifier", lambda settings, parametres: notifier)

    with TestClient(create_app()):
        assert embeddings.ferme is False  # toujours ouvert pendant le service

    assert embeddings.ferme is True
    assert notifier.ferme is True


async def test_la_purge_des_sessions_journalise_et_survit_a_une_erreur(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La purge RGPD tourne en boucle et une erreur ne l'interrompt jamais (E2).

    Un verrou SQLite passager ne doit pas éteindre définitivement la purge : la
    conservation illimitée des conversations serait un manquement au RGPD.
    """
    vrai_sleep = asyncio.sleep
    tours: list[str] = []
    troisieme_tour = asyncio.Event()

    class _SessionsFactices:
        async def purger_anciennes(self, retention_jours: int) -> int:
            tours.append("appel")
            if len(tours) == 1:
                return 3  # purge effective -> journalisée
            if len(tours) == 2:
                raise RuntimeError("base verrouillée")  # avalée, jamais propagée
            troisieme_tour.set()
            await vrai_sleep(3600)  # immobilise la boucle, le test reprend la main
            return 0

    faux_app = SimpleNamespace(state=SimpleNamespace(sessions=_SessionsFactices()))

    async def sommeil_immediat(_secondes: float) -> None:
        await vrai_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", sommeil_immediat)
    tache = _lancer_purge_sessions(faux_app, Settings(sessions_retention_jours=30))
    await asyncio.wait_for(troisieme_tour.wait(), timeout=5)
    tache.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tache

    assert len(tours) == 3  # l'échec du 2e tour n'a pas tué la boucle


def test_origine_cors_configuree_est_autorisee_les_autres_non(
    env_api_isolee: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seules les origines déclarées reçoivent l'en-tête CORS d'autorisation."""
    monkeypatch.setenv("CORS_ORIGINS", "https://opencacao.openlabconsulting.com")
    get_settings.cache_clear()

    with TestClient(create_app()) as client:
        autorisee = client.get(
            "/v1/health", headers={"Origin": "https://opencacao.openlabconsulting.com"}
        )
        inconnue = client.get("/v1/health", headers={"Origin": "https://pirate.example"})

    assert autorisee.headers["access-control-allow-origin"] == (
        "https://opencacao.openlabconsulting.com"
    )
    assert "access-control-allow-origin" not in inconnue.headers


def test_erreur_non_geree_ne_fuite_aucun_detail_interne(env_api_isolee: None) -> None:
    """Une exception imprévue donne un 500 générique, sans trace ni détail interne.

    OWASP API8 — Security Misconfiguration : le message d'exception peut contenir
    un chemin, une requête SQL ou un secret ; il ne sort jamais vers le client.
    """
    app = create_app()

    async def _exploser() -> None:
        raise RuntimeError("connexion refusée sur redis://:motdepasse@redis:6379")

    app.add_api_route("/v1/_incident", _exploser, methods=["GET"])
    # L'interface statique est montée sur "/" et capterait la route : on la place devant.
    app.router.routes.insert(0, app.router.routes.pop())

    with TestClient(app, raise_server_exceptions=False) as client:
        reponse = client.get("/v1/_incident")

    assert reponse.status_code == 500
    assert reponse.json() == {"detail": "Erreur interne du serveur."}
    assert "motdepasse" not in reponse.text


def test_interface_servie_depuis_le_dossier_configure(tmp_path: Path) -> None:
    """``WEB_DIR`` a la priorité sur la détection automatique du dossier ``web/``.

    C'est ce qui permet le déploiement mono-conteneur (UI et API sur la même
    origine, donc aucun CORS) avec un dossier d'interface choisi à l'exécution.
    """
    dossier = tmp_path / "interface"
    dossier.mkdir()
    (dossier / "index.html").write_text(
        "<!doctype html><title>Interface de test</title>", encoding="utf-8"
    )

    app = FastAPI()
    _monter_interface(app, Settings(web_dir=str(dossier)))
    reponse = TestClient(app).get("/")

    assert reponse.status_code == 200
    assert "Interface de test" in reponse.text  # et non l'index du dépôt
