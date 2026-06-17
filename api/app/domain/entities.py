"""Entités du domaine — objets métier purs, sans dépendance framework."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.domain import Confiance


@dataclass(frozen=True)
class Conseil:
    """Résultat métier d'une demande de conseil agronomique.

    Attributes:
        reponse: Texte du conseil.
        sources: Sources fiables citées.
        confiance: Niveau de confiance estimé.
        redirection_anader: Vrai si la réponse oriente vers l'ANADER.
        interaction_id: Identifiant de journalisation (pour rattacher un retour).
    """

    reponse: str
    confiance: Confiance
    sources: list[str] = field(default_factory=list)
    redirection_anader: bool = False
    interaction_id: str | None = None
