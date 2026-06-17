"""Journal des interactions : jeu de données d'amélioration (humain dans la boucle).

Écrit des lignes JSONL **anonymisées** (aucune IP ni donnée personnelle) :
 - ``interactions.jsonl`` : question, réponse, confiance, sources ;
 - ``feedback.jsonl`` : retour utilisateur (👍/👎) référençant une interaction.

Ce journal alimente la curation puis le ré-entraînement LoRA. Il est **tolérant
aux pannes** : une erreur d'écriture n'interrompt jamais le service.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class JournalFichier:
    """Journalise les interactions et retours dans des fichiers JSONL."""

    _FICHIER_INTERACTIONS = "interactions.jsonl"
    _FICHIER_FEEDBACK = "feedback.jsonl"

    def __init__(self, dossier: Path, actif: bool) -> None:
        """Initialise le journal.

        Args:
            dossier: Répertoire d'écriture des fichiers JSONL.
            actif: Si faux, les identifiants sont générés mais rien n'est écrit.
        """
        self._dossier = dossier
        self._actif = actif
        self._verrou = asyncio.Lock()

    @classmethod
    def from_settings(cls, settings: Settings) -> JournalFichier:
        """Construit un journal à partir des paramètres applicatifs."""
        return cls(Path(settings.dataset_dir or "/data"), actif=settings.log_questions)

    async def enregistrer_interaction(
        self,
        question: str,
        langue: str,
        reponse: str,
        confiance: str,
        sources: list[str],
        redirection_anader: bool,
    ) -> str:
        """Enregistre une interaction anonymisée et retourne son identifiant."""
        interaction_id = uuid4().hex
        await self._ecrire(
            self._FICHIER_INTERACTIONS,
            {
                "id": interaction_id,
                "ts": datetime.now(UTC).isoformat(),
                "langue": langue,
                "question": question,
                "reponse": reponse,
                "confiance": confiance,
                "sources": sources,
                "redirection_anader": redirection_anader,
            },
        )
        return interaction_id

    async def enregistrer_feedback(self, interaction_id: str, vote: str) -> None:
        """Enregistre un retour utilisateur (👍/👎) pour une interaction."""
        await self._ecrire(
            self._FICHIER_FEEDBACK,
            {"id": interaction_id, "ts": datetime.now(UTC).isoformat(), "vote": vote},
        )

    async def _ecrire(self, fichier: str, enregistrement: dict) -> None:
        """Ajoute une ligne JSON au fichier (sous verrou), en tolérant les pannes."""
        if not self._actif:
            return
        ligne = json.dumps(enregistrement, ensure_ascii=False) + "\n"
        try:
            async with self._verrou:
                self._dossier.mkdir(parents=True, exist_ok=True)
                with (self._dossier / fichier).open("a", encoding="utf-8") as handle:
                    handle.write(ligne)
        except OSError as exc:
            logger.warning("journal_ecriture_echouee", fichier=fichier, error=str(exc))
