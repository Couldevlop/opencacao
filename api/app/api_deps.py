"""Dépendances FastAPI partagées (clients stockés dans app.state).

Centraliser ces accès permet de les surcharger en test via
``app.dependency_overrides``.
"""

from __future__ import annotations

from fastapi import Depends, Request

from app.application.auth_service import AuthService
from app.application.conseil_agentique import ConseilAgentique
from app.application.conseil_service import ConseilService
from app.application.dialogue_session import DialogueSessionService
from app.application.orchestrateur import Orchestrateur
from app.application.registre import RegistreAgents
from app.application.routage import RouteurIntention
from app.core.config import Settings, get_settings
from app.domain.ports import (
    AuthStorePort,
    CachePort,
    InferencePort,
    JournalPort,
    LienNotifierPort,
    SessionStorePort,
)


def get_app_settings() -> Settings:
    """Retourne les paramètres applicatifs."""
    return get_settings()


def get_inference_client(request: Request) -> InferencePort:
    """Retourne le port d'inférence stocké dans l'état de l'application."""
    return request.app.state.inference


def get_cache_client(request: Request) -> CachePort:
    """Retourne le port de cache stocké dans l'état de l'application."""
    return request.app.state.cache


def get_journal(request: Request) -> JournalPort:
    """Retourne le port de journalisation stocké dans l'état de l'application."""
    return request.app.state.journal


def get_session_store(request: Request) -> SessionStorePort:
    """Retourne le dépôt de sessions de conversation stocké dans l'état de l'application."""
    return request.app.state.sessions


def get_auth_store(request: Request) -> AuthStorePort:
    """Retourne le dépôt d'authentification stocké dans l'état de l'application."""
    return request.app.state.auth_store


def get_notifier(request: Request) -> LienNotifierPort:
    """Retourne le notifier de lien magique stocké dans l'état de l'application."""
    return request.app.state.notifier


def get_auth_service(
    store: AuthStorePort = Depends(get_auth_store),
    notifier: LienNotifierPort = Depends(get_notifier),
    settings: Settings = Depends(get_settings),
) -> AuthService:
    """Construit le cas d'usage d'authentification par lien magique (D2)."""
    return AuthService(store, notifier, ttl_minutes=settings.auth_token_ttl_min)


def _construire_orchestrateur(
    inference: object,
    cache: object,
    journal: object,
    rag: object,
) -> Orchestrateur:
    """Composition racine de la plateforme agentique (testable sans FastAPI).

    Assemble le graphe : registre → 4 agents Cœur enregistrés → routeur →
    orchestrateur. Les outils Météo/Prix sont branchés sur des sources « indisponibles »
    (résultat vide) tant qu'aucune API réelle n'est câblée : l'agent dégrade alors
    proprement en conseil générique, et le socle reste déployable sans dépendance.

    Args:
        inference: Port d'inférence.
        cache: Port de cache/rate-limit.
        journal: Port de journalisation.
        rag: Récupérateur RAG, ou None.

    Returns:
        Un orchestrateur prêt à traiter, avec rag/meteo/prix/reporting enregistrés.
    """
    from app.services.agents.agent_meteo import AgentMeteo
    from app.services.agents.agent_prix import AgentPrix
    from app.services.agents.agent_rag import AgentRag
    from app.services.agents.agent_reglementation import AgentReglementation
    from app.services.agents.agent_reporting import AgentReporting
    from app.services.outils.indisponible import MeteoIndisponible, PrixIndisponible
    from app.services.outils.meteo import OutilMeteo
    from app.services.outils.prix import OutilPrix

    registre = RegistreAgents()
    registre.enregistrer(AgentRag(inference, rag=rag))  # type: ignore[arg-type]
    registre.enregistrer(AgentMeteo(inference, OutilMeteo(MeteoIndisponible())))  # type: ignore[arg-type]
    registre.enregistrer(AgentPrix(inference, OutilPrix(PrixIndisponible())))  # type: ignore[arg-type]
    registre.enregistrer(AgentReglementation(inference, rag=rag))  # type: ignore[arg-type]
    registre.enregistrer(AgentReporting(inference))  # type: ignore[arg-type]
    routeur = RouteurIntention(registre)
    return Orchestrateur(routeur, journal, cache, agent_defaut="rag")  # type: ignore[arg-type]


def get_orchestrateur(request: Request) -> Orchestrateur:
    """Construit l'orchestrateur depuis les ports en état d'application."""
    return _construire_orchestrateur(
        inference=request.app.state.inference,
        cache=request.app.state.cache,
        journal=request.app.state.journal,
        rag=getattr(request.app.state, "rag", None),
    )


def get_conseil_service(request: Request) -> ConseilService | ConseilAgentique:
    """Construit le cas d'usage à partir des ports en état d'application.

    Quand ``agents_enabled`` est ON, renvoie un adaptateur agentique (orchestrateur
    V3) qui présente la même interface que ``ConseilService`` — le dialogue avec
    sessions et le router restent inchangés. Sinon, le cache sémantique n'est branché
    que si activé en configuration ET si le service d'embeddings est disponible
    (partagé avec le RAG) ; à défaut, le service retombe sur le cache exact-match seul.
    """
    settings = get_settings()
    if settings.agents_enabled:
        return ConseilAgentique(get_orchestrateur(request))
    embeddings = (
        getattr(request.app.state, "embeddings", None) if settings.semantic_cache_enabled else None
    )
    return ConseilService(
        inference=request.app.state.inference,
        cache=request.app.state.cache,
        journal=request.app.state.journal,
        rag=getattr(request.app.state, "rag", None),
        embeddings=embeddings,
        semantic_cache_threshold=settings.semantic_cache_threshold,
        semantic_cache_lexical_min=settings.semantic_cache_lexical_min,
    )


def get_dialogue_service(
    conseil: ConseilService = Depends(get_conseil_service),
    sessions: SessionStorePort = Depends(get_session_store),
    settings: Settings = Depends(get_settings),
) -> DialogueSessionService:
    """Construit le cas d'usage de dialogue avec mémoire serveur (sessions V2).

    Dépend de :func:`get_conseil_service` afin que les surcharges de test du
    service de conseil s'appliquent aussi au dialogue avec session.
    """
    return DialogueSessionService(
        conseil,
        sessions,
        fenetre=settings.sessions_fenetre_messages,
        seuil_resume=settings.sessions_resume_seuil,
    )


def get_device_id(request: Request) -> str:
    """Identifiant anonyme de l'appareil (D1), lu dans l'en-tête ``X-Device-Id``.

    Opaque (UUID généré côté navigateur, jamais une IP), il cloisonne les
    conversations par navigateur. Absent (anciens clients, appels directs) → ``""``,
    l'espace « hérité » partagé. Borné en longueur (anti-abus).
    """
    return (request.headers.get("x-device-id") or "").strip()[:64]


def get_client_ip(request: Request) -> str:
    """Détermine l'IP cliente pour le rate-limit.

    Par sécurité (OWASP API4 — anti-spoofing), l'en-tête X-Forwarded-For n'est
    pris en compte que si l'application est configurée pour être derrière un
    proxy de confiance (``trust_forwarded_for``). Sinon on utilise l'IP TCP réelle.
    """
    settings = get_settings()
    if settings.trust_forwarded_for:
        # Derrière Cloudflare (proxy) : l'IP réelle est dans CF-Connecting-IP.
        cf = request.headers.get("cf-connecting-ip")
        if cf:
            return cf.strip()
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
