"""Stockage de la curation : lit le journal, écrit le corpus curé.

Sources (volume partagé avec l'API) :
 - ``interactions.jsonl`` / ``feedback.jsonl`` : produits par l'API ;
 - ``curation_state.jsonl`` : statut de curation (validé / rejeté) par interaction ;
 - ``corpus_cure.jsonl`` : paires validées au format d'entraînement
   (``{"instruction", "input", "output"}``), prêtes pour le ré-entraînement LoRA.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from app.core.logging import get_logger
from app.services import guardrails

logger = get_logger(__name__)

# Règles alignées sur la validation du corpus (enrich_corpus.py) : toute paire
# validée ici doit passer la validation d'entraînement, sans surprise à l'assemblage.
_MIN_INSTRUCTION, _MAX_INSTRUCTION = 10, 500
_MIN_OUTPUT, _MAX_OUTPUT = 50, 2000
_SOURCES = ("CNRA", "ANADER", "Conseil du Café-Cacao", "FAO")


class DosageRefuse(Exception):
    """Levée si une réponse à valider contient un dosage (jamais versé au corpus)."""


class ValidationInvalide(Exception):
    """Levée si l'instruction/output est hors des bornes du corpus."""


class CurationStore:
    """Accès au journal et écriture du corpus curé (fichiers JSONL)."""

    def __init__(self, dossier: Path, corpus_cure: Path) -> None:
        """Initialise le store.

        Args:
            dossier: Répertoire du journal (interactions/feedback/état).
            corpus_cure: Fichier de sortie des paires validées.
        """
        self._dossier = dossier
        self._corpus_cure = corpus_cure
        self._verrou = asyncio.Lock()

    @classmethod
    def from_env(cls) -> CurationStore:
        """Construit le store depuis l'environnement (DATASET_DIR, CORPUS_CURE)."""
        dossier = Path(os.environ.get("DATASET_DIR", "/data"))
        corpus = Path(os.environ.get("CORPUS_CURE", str(dossier / "corpus_cure.jsonl")))
        return cls(dossier, corpus)

    # --- Lecture ---

    def _lire_jsonl(self, fichier: str) -> list[dict]:
        chemin = self._dossier / fichier
        if not chemin.exists():
            return []
        enregistrements: list[dict] = []
        for ligne in chemin.read_text(encoding="utf-8").splitlines():
            ligne = ligne.strip()
            if not ligne:
                continue
            try:
                enregistrements.append(json.loads(ligne))
            except json.JSONDecodeError:
                continue
        return enregistrements

    def _statuts(self) -> dict[str, str]:
        """Statut de curation par interaction (dernier connu)."""
        statuts: dict[str, str] = {}
        for enr in self._lire_jsonl("curation_state.jsonl"):
            if "id" in enr and "statut" in enr:
                statuts[enr["id"]] = enr["statut"]
        return statuts

    def _votes(self) -> dict[str, dict[str, int]]:
        """Agrège les retours 👍/👎 par interaction."""
        votes: dict[str, dict[str, int]] = {}
        for enr in self._lire_jsonl("feedback.jsonl"):
            cle = enr.get("id")
            vote = enr.get("vote")
            if cle is None or vote not in ("up", "down"):
                continue
            compteur = votes.setdefault(cle, {"up": 0, "down": 0})
            compteur[vote] += 1
        return votes

    def a_curer(self) -> list[dict]:
        """Interactions restant à curer, triées par priorité (👎, faible confiance).

        Exclut les refus (redirection ANADER) — réponses canoniques, sans intérêt
        pour le corpus — et les interactions déjà traitées.
        """
        statuts = self._statuts()
        votes = self._votes()
        priorite_conf = {"faible": 2, "moyenne": 1, "elevee": 0}
        items: list[dict] = []
        for inter in self._lire_jsonl("interactions.jsonl"):
            cle = inter.get("id")
            if not cle or cle in statuts or inter.get("redirection_anader"):
                continue
            v = votes.get(cle, {"up": 0, "down": 0})
            score = v["down"] * 10 + priorite_conf.get(inter.get("confiance", ""), 0)
            if not inter.get("sources"):
                score += 1
            items.append({**inter, "votes": v, "priorite": score})
        items.sort(key=lambda i: i["priorite"], reverse=True)
        return items

    def statistiques(self) -> dict[str, int]:
        """Compte des interactions par statut."""
        statuts = self._statuts()
        return {
            "total": len(self._lire_jsonl("interactions.jsonl")),
            "a_curer": len(self.a_curer()),
            "valides": sum(1 for s in statuts.values() if s == "valide"),
            "rejetes": sum(1 for s in statuts.values() if s == "rejete"),
        }

    # --- Écriture ---

    async def _ajouter(self, chemin: Path, enregistrement: dict) -> None:
        ligne = json.dumps(enregistrement, ensure_ascii=False) + "\n"
        async with self._verrou:
            chemin.parent.mkdir(parents=True, exist_ok=True)
            with chemin.open("a", encoding="utf-8") as handle:
                handle.write(ligne)

    async def valider(self, interaction_id: str, instruction: str, output: str) -> None:
        """Valide une paire et l'ajoute au corpus curé.

        Raises:
            DosageRefuse: si la réponse contient un dosage phytosanitaire.
            ValidationInvalide: si les longueurs sont hors bornes du corpus.
        """
        instruction, output = instruction.strip(), output.strip()
        if not _MIN_INSTRUCTION <= len(instruction) <= _MAX_INSTRUCTION:
            raise ValidationInvalide(
                f"instruction hors bornes ({len(instruction)}, attendu 10-500)"
            )
        if not _MIN_OUTPUT <= len(output) <= _MAX_OUTPUT:
            raise ValidationInvalide(f"réponse hors bornes ({len(output)}, attendu 50-2000)")
        # Garde-fou réutilisé : jamais de dosage dans le corpus.
        if guardrails.verifier_reponse(output) is not None:
            raise DosageRefuse("la réponse contient un dosage phytosanitaire")
        # Comme le corpus : une source reconnue doit être citée.
        if not any(source.lower() in output.lower() for source in _SOURCES):
            raise ValidationInvalide("aucune source reconnue citée (CNRA, ANADER, etc.)")

        await self._ajouter(
            self._corpus_cure, {"instruction": instruction, "input": "", "output": output}
        )
        await self._marquer(interaction_id, "valide")
        logger.info("curation_validee", interaction_id=interaction_id)

    async def rejeter(self, interaction_id: str) -> None:
        """Rejette une interaction (ne sera plus proposée à la curation)."""
        await self._marquer(interaction_id, "rejete")
        logger.info("curation_rejetee", interaction_id=interaction_id)

    async def _marquer(self, interaction_id: str, statut: str) -> None:
        await self._ajouter(
            self._dossier / "curation_state.jsonl",
            {"id": interaction_id, "statut": statut, "ts": datetime.now(UTC).isoformat()},
        )
