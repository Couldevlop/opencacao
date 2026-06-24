"""Schémas Pydantic de la console de curation."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ParametreExpediteurRequest(BaseModel):
    """Réglage de l'adresse d'expédition des emails (auth), depuis la console.

    Attributes:
        email: Adresse expéditrice (doit être vérifiée chez le fournisseur).
        nom: Nom affiché de l'expéditeur.
    """

    email: str = Field(min_length=3, max_length=254)
    nom: str = Field(default="OpenCacao", min_length=1, max_length=80)

    @field_validator("email")
    @classmethod
    def _email_plausible(cls, valeur: str) -> str:
        valeur = valeur.strip().lower()
        if not _EMAIL.match(valeur):
            raise ValueError("Adresse email invalide.")
        return valeur


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


class DocumentUpload(BaseModel):
    """Téléversement d'un document source (contenu encodé en base64).

    L'encodage base64 évite la dépendance ``python-multipart`` : le navigateur lit
    le fichier et envoie son contenu en JSON.

    Attributes:
        nom: Nom du fichier (validé/assaini côté serveur).
        contenu_base64: Contenu binaire du fichier encodé en base64.
    """

    nom: str = Field(min_length=1, max_length=255)
    contenu_base64: str = Field(min_length=1)


class DocumentUrl(BaseModel):
    """Ajout d'un document/page par URL (téléchargé côté serveur)."""

    url: str = Field(min_length=8, max_length=2000, pattern=r"^https?://")
