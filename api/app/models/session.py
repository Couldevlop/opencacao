"""Schémas Pydantic des sessions de conversation persistantes (V2 conversationnelle).

Une *session* représente un fil de discussion durable (comme une conversation
ChatGPT/Claude) : un titre, des métadonnées et une suite ordonnée de messages.
Contrairement au dialogue multi-tours « sans état » de la V1 (l'historique était
renvoyé par le client à chaque requête), ces objets sont **persistés côté serveur**
afin d'être repris, listés et nommés.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.domain import Canal, Langue

TITRE_PAR_DEFAUT = "Nouvelle conversation"


class ConversationMessage(BaseModel):
    """Un message persisté d'une session de conversation.

    Attributes:
        role: Auteur du message (``user`` ou ``assistant``).
        content: Contenu textuel du message.
        cree_le: Horodatage UTC de création du message.
    """

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)
    cree_le: datetime


class Session(BaseModel):
    """Métadonnées d'une session de conversation (sans les messages).

    Attributes:
        id: Identifiant opaque de la session (hex UUID4).
        titre: Titre lisible de la conversation (auto-généré en V2, voir B3).
        langue: Langue de la conversation.
        canal: Canal d'origine de la conversation.
        cree_le: Horodatage UTC de création.
        maj_le: Horodatage UTC du dernier message (sert au tri de la liste).
    """

    id: str
    titre: str = Field(min_length=1, max_length=200)
    langue: Langue = Langue.FR
    canal: Canal = Canal.WEB
    cree_le: datetime
    maj_le: datetime


class SessionAvecMessages(BaseModel):
    """Une session et l'intégralité de ses messages, dans l'ordre chronologique.

    Attributes:
        session: Métadonnées de la session.
        messages: Messages de la session, du plus ancien au plus récent.
    """

    session: Session
    messages: list[ConversationMessage] = Field(default_factory=list)
