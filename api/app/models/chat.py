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
        historique: Tours précédents de la conversation (clarifications). Utilisé
            uniquement en mode « sans état » (sans session_id) : le client renvoie
            l'historique à chaque tour. Borné à 20 messages pour éviter les abus.
        session_id: Session de conversation persistée (V2). Si fourni, l'historique
            fait autorité côté serveur et le champ ``historique`` est ignoré.
    """

    question: str = Field(min_length=3, max_length=2000)
    langue: Langue = Langue.FR
    canal: Canal = Canal.WEB
    historique: list[Message] = Field(default_factory=list, max_length=20)
    session_id: str | None = Field(default=None, max_length=64)


class ChatResponse(BaseModel):
    """Réponse de conseil agronomique.

    Attributes:
        reponse: Texte de la réponse.
        sources: Sources citées.
        confiance: Niveau de confiance de la réponse.
        redirection_anader: Vrai si la réponse oriente vers l'ANADER.
        disclaimer: Mention légale obligatoire.
        interaction_id: Identifiant pour rattacher un retour 👍/👎 (si journalisé).
        session_id: Session de conversation (V2), renvoyée si la requête en utilisait une.
    """

    reponse: str
    sources: list[str] = Field(default_factory=list)
    confiance: Confiance = Confiance.MOYENNE
    redirection_anader: bool = False
    disclaimer: str = DISCLAIMER
    interaction_id: str | None = None
    session_id: str | None = None


class FeedbackRequest(BaseModel):
    """Retour d'un utilisateur sur une réponse (pour la boucle d'amélioration).

    Attributes:
        interaction_id: Identifiant de l'interaction concernée.
        vote: Avis de l'utilisateur (positif ou négatif).
    """

    interaction_id: str = Field(min_length=8, max_length=64)
    vote: Literal["up", "down"]


class Capacites(BaseModel):
    """Ce que l'API ouvre réellement, à cet instant.

    L'interface réunit ses trois destinations dans une seule fenêtre : sans cette
    déclaration, baisser un drapeau laisserait dans la barre latérale une porte qui ne
    mène nulle part. Ce n'est pas divulguer — l'existence d'une route se découvre en
    l'appelant — c'est dire honnêtement ce qui est disponible.
    """

    parcelles: bool
    rapports: bool
    vision: bool


class VersionResponse(BaseModel):
    """Réponse de /v1/version."""

    api_version: str
    model_name: str
    model_version: str
    inference_backend: str
    # Déclaré à côté du backend, qui dit COMMENT on sert ; celui-ci dit AVEC QUOI.
    # Utile en scène : on vérifie d'un coup d'œil sur quoi tourne la production.
    profil_materiel: str
    # Vrai quand la sentinelle a ramené le service au CPU d'elle-même. L'interface s'en
    # sert pour afficher un avis honnête — « service de secours », et non « bientôt ».
    repli_cpu: bool = False
    capacites: Capacites
