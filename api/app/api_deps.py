"""Dépendances FastAPI partagées (clients stockés dans app.state).

Centraliser ces accès permet de les surcharger en test via
``app.dependency_overrides``.
"""

from __future__ import annotations

from fastapi import Request

from app.application.conseil_service import ConseilService
from app.core.config import Settings, get_settings
from app.domain.ports import CachePort, InferencePort, JournalPort


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


def get_conseil_service(request: Request) -> ConseilService:
    """Construit le cas d'usage à partir des ports en état d'application."""
    return ConseilService(
        inference=request.app.state.inference,
        cache=request.app.state.cache,
        journal=request.app.state.journal,
        rag=getattr(request.app.state, "rag", None),
    )


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
