"""Schémas Pydantic de la requête et de la réponse /v1/chat."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.models.domain import Canal, Confiance, Langue

DISCLAIMER = (
    "OpenCacao est un outil d'aide à la décision. Pour confirmation, "
    "contactez votre agent ANADER ou la délégation du Conseil du Café-Cacao."
)


class Message(BaseModel):
    """Un tour de conversation (pour le dialogue multi-tours).

    Attributes:
        role: Auteur du message (``user`` ou ``assistant``).
        content: Contenu textuel du message.
    """

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)


class ChatRequest(BaseModel):
    """Requête de conseil agronomique.

    Attributes:
        question: Question posée par le producteur (dernier message).
        langue: Langue de la question (fr par défaut).
        canal: Canal d'origine de la question.
        historique: Tours précédents de la conversation (clarifications). Le serveur
            est sans état : le client renvoie l'historique à chaque tour. Borné à
            20 messages pour éviter les abus.
    """

    question: str = Field(min_length=3, max_length=2000)
    langue: Langue = Langue.FR
    canal: Canal = Canal.WEB
    historique: list[Message] = Field(default_factory=list, max_length=20)


class ChatResponse(BaseModel):
    """Réponse de conseil agronomique.

    Attributes:
        reponse: Texte de la réponse.
        sources: Sources citées.
        confiance: Niveau de confiance de la réponse.
        redirection_anader: Vrai si la réponse oriente vers l'ANADER.
        disclaimer: Mention légale obligatoire.
        interaction_id: Identifiant pour rattacher un retour 👍/👎 (si journalisé).
    """

    reponse: str
    sources: list[str] = Field(default_factory=list)
    confiance: Confiance = Confiance.MOYENNE
    redirection_anader: bool = False
    disclaimer: str = DISCLAIMER
    interaction_id: str | None = None


class FeedbackRequest(BaseModel):
    """Retour d'un utilisateur sur une réponse (pour la boucle d'amélioration).

    Attributes:
        interaction_id: Identifiant de l'interaction concernée.
        vote: Avis de l'utilisateur (positif ou négatif).
    """

    interaction_id: str = Field(min_length=8, max_length=64)
    vote: Literal["up", "down"]


class VersionResponse(BaseModel):
    """Réponse de /v1/version."""

    api_version: str
    model_name: str
    model_version: str
    inference_backend: str
