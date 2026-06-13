"""Exceptions du domaine, indépendantes du transport HTTP."""

from __future__ import annotations


class DomainError(Exception):
    """Erreur métier de base."""


class InferenceUnavailable(DomainError):
    """Le moteur d'inférence est injoignable ou a renvoyé une réponse invalide."""


class RateLimitDepasse(DomainError):
    """Le client a dépassé le quota de requêtes autorisé."""
