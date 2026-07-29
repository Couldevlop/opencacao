"""Dépendances FastAPI partagées (clients stockés dans app.state).

Centraliser ces accès permet de les surcharger en test via
``app.dependency_overrides``.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from app.application.auth_service import AuthService
from app.application.cache_semantique import CacheSemantique
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
    ParcelleStorePort,
    SessionStorePort,
)
from app.services.constats import ServiceConstats
from app.services.parcelles import ServiceParcelles


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


def get_parcelle_store(request: Request) -> ParcelleStorePort:
    """Retourne le dépôt de parcelles stocké dans l'état de l'application."""
    return request.app.state.parcelles


def get_service_parcelles(request: Request) -> ServiceParcelles:
    """Retourne le service métier des parcelles stocké dans l'état de l'application."""
    return request.app.state.service_parcelles


def get_service_constats(request: Request) -> ServiceConstats:
    """Retourne le service du constat visuel stocké dans l'état de l'application."""
    return request.app.state.service_constats


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
    cache_semantique: CacheSemantique | None = None,
    dialogue_naturel: bool = False,
) -> Orchestrateur:
    """Composition racine de la plateforme agentique (testable sans FastAPI).

    Assemble le graphe : registre → agents Cœur enregistrés (RAG, Météo, Prix,
    Réglementation, Normes, Satellite, Reporting) → routeur → orchestrateur. Les
    outils Météo/Prix sont branchés sur des sources réelles (Open-Meteo, prix
    officiel de campagne) ; Satellite s'appuie sur Global Forest Watch si une clé
    API est configurée, sinon sur une source neutre (résultat vide) : l'agent
    dégrade alors proprement en consigne d'indisponibilité, et le socle reste
    déployable sans dépendance.

    Args:
        inference: Port d'inférence.
        cache: Port de cache/rate-limit.
        journal: Port de journalisation.
        rag: Récupérateur RAG, ou None.
        cache_semantique: Couche de cache sémantique (paraphrases), ou None → exact seul.
        dialogue_naturel: Si vrai, la clarification est formulée par le modèle
            (naturelle) plutôt que par le texte scripté. Défaut False (inchangé).

    Returns:
        Un orchestrateur prêt à traiter, avec rag/meteo/prix/reglementation/normes/
        satellite/reporting enregistrés.
    """
    from app.services.agents.agent_meteo import AgentMeteo
    from app.services.agents.agent_normes import AgentNormes
    from app.services.agents.agent_prix import AgentPrix
    from app.services.agents.agent_rag import AgentRag
    from app.services.agents.agent_reglementation import AgentReglementation
    from app.services.agents.agent_reporting import AgentReporting
    from app.services.agents.agent_satellite import AgentSatellite
    from app.services.outils.indisponible import SatelliteIndisponible
    from app.services.outils.meteo import OutilMeteo
    from app.services.outils.meteo_openmeteo import MeteoOpenMeteo
    from app.services.outils.prix import OutilPrix
    from app.services.outils.prix_campagne import PrixCampagne
    from app.services.outils.satellite import OutilSatellite
    from app.services.outils.satellite_gfw import SatelliteGfw

    settings = get_settings()
    meteo = MeteoOpenMeteo(timeout_s=settings.meteo_timeout_s)
    prix = PrixCampagne(settings.prix_bord_champ_fcfa_kg, settings.prix_campagne)
    satellite = (
        SatelliteGfw(settings.gfw_api_key, timeout_s=settings.gfw_timeout_s)
        if settings.gfw_api_key
        else SatelliteIndisponible()
    )

    # Le cache de résultats d'outils réutilise le client Redis applicatif (fail-soft).
    outil_meteo = OutilMeteo(meteo, cache=cache, ttl_s=settings.outil_cache_meteo_ttl_s)
    outil_satellite = OutilSatellite(
        satellite, cache=cache, ttl_s=settings.outil_cache_satellite_ttl_s
    )

    registre = RegistreAgents()
    registre.enregistrer(AgentRag(inference, rag=rag))  # type: ignore[arg-type]
    registre.enregistrer(AgentMeteo(inference, outil_meteo))  # type: ignore[arg-type]
    registre.enregistrer(AgentPrix(inference, OutilPrix(prix), rag=rag))  # type: ignore[arg-type]
    registre.enregistrer(AgentReglementation(inference, rag=rag))  # type: ignore[arg-type]
    registre.enregistrer(AgentNormes(inference, rag=rag))  # type: ignore[arg-type]
    registre.enregistrer(AgentSatellite(inference, outil_satellite, rag=rag))  # type: ignore[arg-type]
    registre.enregistrer(AgentReporting(inference))  # type: ignore[arg-type]
    routeur = RouteurIntention(registre)
    return Orchestrateur(
        routeur,
        journal,  # type: ignore[arg-type]
        cache,  # type: ignore[arg-type]
        agent_defaut="rag",
        cache_semantique=cache_semantique,
        inference=inference,  # type: ignore[arg-type]
        dialogue_naturel=dialogue_naturel,
    )


def get_orchestrateur(request: Request) -> Orchestrateur:
    """Construit l'orchestrateur depuis les ports en état d'application.

    Le cache sémantique (parité V2) n'est branché que si activé en configuration ET si
    le service d'embeddings est disponible (partagé avec le RAG) ; sinon, une couche
    inerte laisse l'orchestrateur sur le cache exact-match seul.
    """
    settings = get_settings()
    cache = request.app.state.cache
    embeddings = (
        getattr(request.app.state, "embeddings", None) if settings.semantic_cache_enabled else None
    )
    cache_semantique = CacheSemantique(
        cache,
        embeddings,
        seuil=settings.semantic_cache_threshold,
        seuil_lexical=settings.semantic_cache_lexical_min,
    )
    return _construire_orchestrateur(
        inference=request.app.state.inference,
        cache=cache,
        cache_semantique=cache_semantique,
        journal=request.app.state.journal,
        rag=getattr(request.app.state, "rag", None),
        dialogue_naturel=settings.dialogue_naturel_enabled,
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
        dialogue_naturel=settings.dialogue_naturel_enabled,
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


def get_device_id_obligatoire(request: Request) -> str:
    """Identifiant d'appareil **exigé**, pour les ressources rattachées à un producteur.

    ``get_device_id`` retombe sur ``""`` quand l'en-tête est absent : c'est l'espace
    « hérité » partagé, un choix de compatibilité assumé pour les conversations V2.

    Appliqué aux **parcelles**, cet espace deviendrait une fuite : tout appelant
    omettant l'en-tête verrait — et pourrait modifier — les parcelles de tous les
    autres appelants sans en-tête, alors qu'une parcelle porte le polygone GPS exact
    de la plantation d'un producteur. Les parcelles sont récentes : aucun client
    hérité à ménager, on exige donc l'identifiant.

    Raises:
        HTTPException: 400 si l'en-tête est absent ou vide.
    """
    identifiant = get_device_id(request)
    if not identifiant:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="En-tête X-Device-Id requis pour accéder aux parcelles.",
        )
    return identifiant


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
