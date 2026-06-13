"""Schémas Pydantic de la requête et de la réponse /v1/chat."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.domain import Canal, Confiance, Langue

DISCLAIMER = (
    "OpenCacao est un outil d'aide à la décision. Pour confirmation, "
    "contactez votre agent ANADER ou la délégation du Conseil du Café-Cacao."
)


class ChatRequest(BaseModel):
    """Requête de conseil agronomique.

    Attributes:
        question: Question posée par le producteur.
        langue: Langue de la question (fr par défaut).
        canal: Canal d'origine de la question.
    """

    question: str = Field(min_length=3, max_length=2000)
    langue: Langue = Langue.FR
    canal: Canal = Canal.WEB


class ChatResponse(BaseModel):
    """Réponse de conseil agronomique.

    Attributes:
        reponse: Texte de la réponse.
        sources: Sources citées.
        confiance: Niveau de confiance de la réponse.
        redirection_anader: Vrai si la réponse oriente vers l'ANADER.
        disclaimer: Mention légale obligatoire.
    """

    reponse: str
    sources: list[str] = Field(default_factory=list)
    confiance: Confiance = Confiance.MOYENNE
    redirection_anader: bool = False
    disclaimer: str = DISCLAIMER


class VersionResponse(BaseModel):
    """Réponse de /v1/version."""

    api_version: str
    model_name: str
    model_version: str
    inference_backend: str
