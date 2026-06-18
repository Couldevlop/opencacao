"""Registre des tâches longues du pipeline (reindex RAG, préparation fine-tuning).

Persiste l'état des jobs dans un JSONL sur le volume partagé (``/data``) pour
survivre aux redémarrages du pod de la console. Accès sérialisé par un verrou
asyncio (la console est mono-réplica). Chaque job porte un statut, des horodatages
et une queue de log bornée — afin d'alimenter le suivi côté interface.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from app.core.logging import get_logger

logger = get_logger(__name__)

Statut = Literal["en_cours", "reussi", "echec"]

# Borne la queue de log par job (évite une croissance non maîtrisée du fichier).
_MAX_LOG = 50
# Borne le nombre de jobs conservés (les plus anciens sont purgés).
_MAX_JOBS = 100


def _maintenant() -> str:
    """Horodatage ISO 8601 en UTC."""
    return datetime.now(UTC).isoformat()


class JobsRegistry:
    """Registre persistant des jobs du pipeline (JSONL, accès verrouillé)."""

    def __init__(self, chemin: Path) -> None:
        """Initialise le registre.

        Args:
            chemin: Fichier JSONL de persistance (un job par ligne).
        """
        self._chemin = chemin
        self._verrou = asyncio.Lock()

    @classmethod
    def from_env(cls) -> JobsRegistry:
        """Construit le registre depuis l'environnement (``DATASET_DIR``)."""
        dossier = Path(os.environ.get("DATASET_DIR", "/data"))
        return cls(dossier / "jobs.jsonl")

    def _lire(self) -> list[dict]:
        if not self._chemin.exists():
            return []
        jobs: list[dict] = []
        for ligne in self._chemin.read_text(encoding="utf-8").splitlines():
            ligne = ligne.strip()
            if not ligne:
                continue
            try:
                jobs.append(json.loads(ligne))
            except json.JSONDecodeError:
                continue
        return jobs

    def _ecrire(self, jobs: list[dict]) -> None:
        self._chemin.parent.mkdir(parents=True, exist_ok=True)
        contenu = "".join(json.dumps(job, ensure_ascii=False) + "\n" for job in jobs)
        self._chemin.write_text(contenu, encoding="utf-8")

    async def creer(self, type_: str, details: dict | None = None) -> dict:
        """Crée un job ``en_cours`` et le persiste.

        Args:
            type_: Type de tâche (ex. ``"rag_reindex"``, ``"finetuning_prepare"``).
            details: Métadonnées libres attachées au job.

        Returns:
            Le job créé.
        """
        job = {
            "id": secrets.token_hex(8),
            "type": type_,
            "statut": "en_cours",
            "cree_le": _maintenant(),
            "maj_le": _maintenant(),
            "message": "",
            "log": [],
            "details": details or {},
        }
        async with self._verrou:
            jobs = self._lire()
            jobs.append(job)
            self._ecrire(jobs[-_MAX_JOBS:])
        logger.info("job_cree", job_id=job["id"], type=type_)
        return job

    async def maj(
        self,
        job_id: str,
        *,
        statut: Statut | None = None,
        message: str | None = None,
        log: str | None = None,
        details: dict | None = None,
    ) -> dict | None:
        """Met à jour un job (statut, message, ajout de log, fusion de détails).

        Args:
            job_id: Identifiant du job.
            statut: Nouveau statut, si fourni.
            message: Message courant (résumé), si fourni.
            log: Ligne à ajouter à la queue de log, si fournie.
            details: Détails à fusionner, si fournis.

        Returns:
            Le job mis à jour, ou ``None`` s'il est introuvable.
        """
        async with self._verrou:
            jobs = self._lire()
            cible: dict | None = None
            for job in jobs:
                if job.get("id") == job_id:
                    cible = job
                    break
            if cible is None:
                return None
            if statut is not None:
                cible["statut"] = statut
            if message is not None:
                cible["message"] = message
            if log is not None:
                file = deque(cible.get("log", []), maxlen=_MAX_LOG)
                file.append(f"{_maintenant()} {log}")
                cible["log"] = list(file)
            if details is not None:
                cible["details"] = {**cible.get("details", {}), **details}
            cible["maj_le"] = _maintenant()
            self._ecrire(jobs)
            return dict(cible)

    async def lister(self) -> list[dict]:
        """Retourne les jobs, du plus récent au plus ancien."""
        async with self._verrou:
            return list(reversed(self._lire()))

    async def obtenir(self, job_id: str) -> dict | None:
        """Retourne un job par son identifiant, ou ``None``."""
        async with self._verrou:
            for job in self._lire():
                if job.get("id") == job_id:
                    return job
        return None

    async def actif(self, type_: str) -> bool:
        """Indique si un job de ce type est déjà en cours (anti-concurrence)."""
        async with self._verrou:
            return any(
                job.get("type") == type_ and job.get("statut") == "en_cours" for job in self._lire()
            )
