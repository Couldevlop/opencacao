"""Schémas Pydantic de l'authentification par lien magique (D2, optionnelle).

L'auth est *passwordless* : l'utilisateur saisit son email, reçoit un lien à usage
unique et de courte durée, et obtient en retour un **identifiant de compte** opaque
et stable. Cet identifiant sert ensuite de ``proprietaire`` (comme le device id de la
D1) — ses conversations le suivent donc d'un appareil à l'autre.

L'email est validé par une expression régulière simple (pas de dépendance
``email-validator`` hors spec §2.1) : on vérifie une forme plausible, la preuve réelle
de possession venant du clic sur le lien envoyé à cette adresse.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

# Forme plausible « local@domaine.tld », sans prétention à la RFC 5322 complète.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class DemandeLienRequest(BaseModel):
    """Demande d'envoi d'un lien magique.

    Attributes:
        email: Adresse email de l'utilisateur (forme validée par regex).
    """

    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def _email_plausible(cls, valeur: str) -> str:
        valeur = valeur.strip().lower()
        if not _EMAIL.match(valeur):
            raise ValueError("Adresse email invalide.")
        return valeur


class VerifierLienRequest(BaseModel):
    """Vérification d'un jeton de lien magique.

    Attributes:
        token: Jeton opaque extrait du lien (paramètre ``auth`` de l'URL).
    """

    token: str = Field(min_length=16, max_length=256)


class IdentiteResponse(BaseModel):
    """Identité renvoyée après vérification réussie.

    Attributes:
        account_id: Identifiant de compte opaque et secret (sert de proprietaire).
        email: Email vérifié, pour affichage côté client.
    """

    account_id: str
    email: str
