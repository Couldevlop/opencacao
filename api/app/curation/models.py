"""Schémas Pydantic de la console de curation."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ValidationRequest(BaseModel):
    """Validation d'une paire Q/R vers le corpus d'entraînement.

    Attributes:
        interaction_id: Interaction d'origine.
        instruction: Question (éventuellement reformulée par le curateur).
        output: Réponse validée/corrigée par le curateur.
    """

    interaction_id: str = Field(min_length=8, max_length=64)
    instruction: str = Field(min_length=1, max_length=2000)
    output: str = Field(min_length=1, max_length=8000)


class RejetRequest(BaseModel):
    """Rejet d'une interaction (écartée de la curation)."""

    interaction_id: str = Field(min_length=8, max_length=64)


class LoginRequest(BaseModel):
    """Identifiants de connexion à la console."""

    utilisateur: str = Field(min_length=1, max_length=64)
    mot_de_passe: str = Field(min_length=1, max_length=128)
