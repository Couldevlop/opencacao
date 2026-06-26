"""Types métier du domaine agronomique cacao."""

from __future__ import annotations

from enum import Enum


class Langue(str, Enum):
    """Langue de la question et de la réponse."""

    FR = "fr"


class Canal(str, Enum):
    """Canal par lequel la question est posée."""

    SMS = "sms"
    WHATSAPP = "whatsapp"
    WEB = "web"


class Confiance(str, Enum):
    """Niveau de confiance déclaré pour une réponse."""

    FAIBLE = "faible"
    MOYENNE = "moyenne"
    ELEVEE = "elevee"


class NiveauUrgence(str, Enum):
    """Niveau d'urgence détecté pour une question."""

    NORMAL = "normal"
    ELEVE = "eleve"


class CategorieRefus(str, Enum):
    """Catégorie de refus déclenchée par un garde-fou métier."""

    PHYTOSANITAIRE = "phytosanitaire"
    MEDICAL = "medical"
    DIAGNOSTIC_IMAGE = "diagnostic_image"
    HORS_FILIERE = "hors_filiere"
    ZONE_NON_CACAO = "zone_non_cacao"
