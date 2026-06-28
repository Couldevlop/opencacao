"""Point d'entrée FastAPI d'OpenCacao-8B (clean architecture + durcissement OWASP)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import __version__
from app.core.auth_store import AuthStore
from app.core.cache import CacheClient
from app.core.config import Settings, get_settings
from app.core.journal import JournalFichier
from app.core.logging import configure_logging, get_logger
from app.core.parametres import ParametresStore
from app.core.security import BodySizeLimitMiddleware, SecurityHeadersMiddleware
from app.core.sessions import SessionStore
from app.routers import auth, chat, feedback, health, sessions
from app.services.inference import InferenceClient
from app.services.notifier import construire_notifier

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialise et libère les clients partagés (inférence, cache)."""
    settings = get_settings()
    configure_logging(settings.log_level)

    app.state.inference = InferenceClient.from_settings(settings)
    app.state.cache = CacheClient.from_settings(settings)
    app.state.journal = JournalFichier.from_settings(settings)
    app.state.sessions = SessionStore.from_settings(settings)
    app.state.purge_task = None
    if settings.sessions_enabled:
        await app.state.sessions.initialiser()
        app.state.purge_task = _lancer_purge_sessions(app, settings)

    app.state.auth_store = AuthStore.from_settings(settings)
    app.state.parametres = ParametresStore.from_settings(settings)
    app.state.notifier = construire_notifier(settings, app.state.parametres)
    if settings.auth_enabled:
        await app.state.auth_store.initialiser()
        await app.state.parametres.initialiser()
        if settings.auth_canal == "console":
            # OWASP A09 : le canal console expose les jetons dans les logs.
            logger.warning(
                "auth_canal_console_en_prod",
                message="Auth active avec canal console : les liens (jetons) "
                "apparaissent dans les logs. Réservé au dev — configurer SMTP en production.",
            )
    app.state.embeddings, app.state.rag = _construire_rag(settings)
    from app.services.geo import GeoLocalisateur

    app.state.geo = GeoLocalisateur.from_env()
    logger.info("demarrage", version=__version__, backend=settings.inference_backend)

    app.state.prewarm_task = _lancer_prechauffage(app, settings)

    try:
        yield
    finally:
        if app.state.prewarm_task is not None:
            app.state.prewarm_task.cancel()
        if app.state.purge_task is not None:
            app.state.purge_task.cancel()
        await app.state.inference.close()
        await app.state.cache.close()
        if app.state.embeddings is not None:
            await app.state.embeddings.close()
        if hasattr(app.state.notifier, "close"):
            await app.state.notifier.close()
        logger.info("arret")


def _lancer_purge_sessions(app: FastAPI, settings: Settings) -> object | None:
    """Démarre la purge RGPD des conversations expirées (E2) en tâche de fond.

    Purge une fois au démarrage, puis une fois par jour. Non bloquant et tolérant aux
    pannes : une erreur de purge n'interrompt jamais le service.

    Returns:
        La tâche asyncio créée, ou None si la rétention est désactivée.
    """
    if settings.sessions_retention_jours <= 0:
        return None
    import asyncio

    async def boucle() -> None:
        while True:
            try:
                nombre = await app.state.sessions.purger_anciennes(
                    settings.sessions_retention_jours
                )
                if nombre:
                    logger.info(
                        "sessions_purgees",
                        nombre=nombre,
                        retention_jours=settings.sessions_retention_jours,
                    )
            except Exception as exc:  # purge best-effort : ne jamais propager
                logger.warning("purge_sessions_echouee", error=str(exc))
            await asyncio.sleep(86_400)

    return asyncio.create_task(boucle())


def _lancer_prechauffage(app: FastAPI, settings: Settings) -> object | None:
    """Démarre le pré-chauffage du cache en tâche de fond (non bloquant).

    Returns:
        La tâche asyncio créée, ou None si le pré-chauffage est désactivé.
    """
    if not settings.prewarm_enabled:
        return None
    import asyncio

    from app.application.conseil_service import ConseilService
    from app.application.faq import QUESTIONS_FAQ
    from app.application.prewarm import prechauffer_cache

    service = ConseilService(
        inference=app.state.inference,
        cache=app.state.cache,
        journal=app.state.journal,
        rag=app.state.rag,
    )
    return asyncio.create_task(prechauffer_cache(service, QUESTIONS_FAQ))


def _construire_rag(settings: Settings) -> tuple[object | None, object | None]:
    """Charge l'index + le récupérateur RAG si activé/disponible.

    Returns:
        (client_embeddings, recuperateur), ou (None, None) si RAG désactivé/indisponible.
    """
    if not settings.rag_enabled:
        return None, None
    from app.services.embeddings import EmbeddingsClient
    from app.services.rag import RagIndex, RagRecuperateur

    index = RagIndex.charger(Path(settings.rag_index_path))
    if index is None:
        logger.warning("rag_active_mais_index_absent", chemin=settings.rag_index_path)
        return None, None
    embeddings = EmbeddingsClient.from_settings(settings)
    logger.info("rag_actif", entrees=index.taille)
    return embeddings, RagRecuperateur(
        embeddings,
        index,
        settings.rag_top_k,
        settings.rag_min_similarite,
        candidats=settings.rag_candidats,
        poids_lexical=settings.rag_poids_lexical,
        seuil_lexical=settings.rag_seuil_lexical,
        hybride=settings.rag_hybride_enabled,
    )


def create_app() -> FastAPI:
    """Construit et configure l'application FastAPI.

    Returns:
        L'instance FastAPI prête à servir.
    """
    settings = get_settings()
    app = FastAPI(
        title="OpenCacao-8B API",
        description="Conseil agronomique pour les producteurs de cacao ivoiriens.",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.enable_docs else None,
    )

    # --- Middlewares (ordre : le dernier ajouté s'exécute en premier) ---
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(BodySizeLimitMiddleware, max_body_bytes=settings.max_body_bytes)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type"],
        )

    @app.exception_handler(Exception)
    async def erreur_non_geree(request: Request, exc: Exception) -> JSONResponse:
        """Intercepte toute erreur non gérée sans fuiter de détails internes.

        OWASP API8 — Security Misconfiguration : pas de stack trace au client.
        """
        logger.error("erreur_non_geree", chemin=request.url.path, error=str(exc))
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Erreur interne du serveur."},
        )

    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(feedback.router)
    app.include_router(sessions.router)
    app.include_router(auth.router)
    _monter_interface(app, settings)
    return app


def _monter_interface(app: FastAPI, settings: Settings) -> None:
    """Sert l'interface web statique à la racine, si un dossier est disponible.

    Permet de servir l'UI et l'API sur la MÊME origine (aucun CORS). Monté en
    dernier pour ne pas masquer les routes /v1 et /docs. Ignoré si le dossier
    n'existe pas (ex. image API ne contenant que app/).
    """
    candidats: list[Path] = []
    if settings.web_dir:
        candidats.append(Path(settings.web_dir))
    candidats.append(Path(__file__).resolve().parents[2] / "web")
    for dossier in candidats:
        if dossier.is_dir() and (dossier / "index.html").is_file():
            app.mount("/", StaticFiles(directory=str(dossier), html=True), name="web")
            logger.info("interface_montee", dossier=str(dossier))
            return


app = create_app()
