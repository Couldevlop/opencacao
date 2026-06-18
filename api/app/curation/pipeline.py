"""Orchestration du pipeline depuis la console : reindex RAG + préparation fine-tuning.

Garde les *routers* fins : toute la logique (lecture du corpus curé, vectorisation,
fusion additive de l'index, redémarrage de l'API, assemblage du corpus
d'entraînement) vit ici. Les dépendances réseau/cluster sont injectables pour les
tests.

Deux actions :

* **Reindex RAG** (à chaud) — n'ajoute que les faits curés absents de l'index
  existant puis redémarre l'API (rolling). Ne peut jamais réduire l'index.
* **Préparation fine-tuning** — assemble/valide/dédoublonne le corpus curé en un
  fichier prêt à l'entraînement et fournit la procédure exacte à lancer sur un
  pod GPU externe (le CPU du cluster ne peut pas entraîner).
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from collections.abc import Callable
from pathlib import Path

from app.core.logging import get_logger
from app.curation.jobs import JobsRegistry
from app.curation.k8s import ClusterClient, ClusterIndisponible
from app.services import guardrails
from app.services.embeddings import EmbeddingsClient
from app.services.rag_index_builder import (
    ajouter_entrees,
    charger_paires,
    construire_entrees,
    filtrer_nouvelles,
    lire_textes_indexes,
)

logger = get_logger(__name__)

# Bornes alignées sur la validation du corpus (store.py / enrich_corpus.py).
_MIN_INSTRUCTION, _MAX_INSTRUCTION = 10, 500
_MIN_OUTPUT, _MAX_OUTPUT = 50, 2000
_SOURCES = ("CNRA", "ANADER", "Conseil du Café-Cacao", "FAO")
_ESPACES = re.compile(r"\s+")

# Procédure exacte à exécuter sur un pod GPU externe (RunPod) après préparation.
# Le CPU Hetzner ne peut pas entraîner : la console prépare, l'opérateur déclenche.
_PROCEDURE = (
    "# Sur un pod RunPod GPU (A100/RTX 4090), CUDA ≥ 12.8, Jupyter actif :\n"
    "cd /workspace\n"
    "git clone -b develop https://github.com/Couldevlop/opencacao.git\n"
    "cd opencacao\n"
    "export KUBECONFIG=...          # accès cluster\n"
    "make corpus-cure              # rapatrie corpus_cure.jsonl depuis le cluster\n"
    "export HF_TOKEN=hf_xxxxxxxx\n"
    "tmux new -s train\n"
    "bash training/scripts/pod_train.sh    # LoRA 4-bit + fusion -> models/opencacao-8b\n"
    "bash training/scripts/pod_gguf.sh     # export GGUF Q4_K_M\n"
    "# Puis, depuis ton poste :\n"
    "make redeploy-model GGUF=models/opencacao-8b-Q4_K_M.gguf VERSION=x.y.z"
)


def _cle(instruction: str) -> str:
    """Clé de déduplication : minuscules, sans accents, espaces normalisés."""
    sans_accents = "".join(
        c for c in unicodedata.normalize("NFKD", instruction) if not unicodedata.combining(c)
    )
    return _ESPACES.sub(" ", sans_accents.lower()).strip()


class PipelineService:
    """Pilote les actions de pipeline déclenchées depuis la console."""

    def __init__(
        self,
        dataset_dir: Path,
        corpus_cure: Path,
        index_path: Path,
        jobs: JobsRegistry,
        embeddings: EmbeddingsClient,
        api_deployment: str,
        cluster_factory: Callable[[], ClusterClient],
    ) -> None:
        """Initialise le service.

        Args:
            dataset_dir: Répertoire du volume partagé.
            corpus_cure: Fichier des paires curées.
            index_path: Fichier de l'index RAG.
            jobs: Registre des jobs.
            embeddings: Client d'embeddings (vectorisation).
            api_deployment: Nom du déploiement API à redémarrer.
            cluster_factory: Fabrique du client cluster (injectable pour tests).
        """
        self._dataset_dir = dataset_dir
        self._corpus_cure = corpus_cure
        self._index_path = index_path
        self._jobs = jobs
        self._embeddings = embeddings
        self._api_deployment = api_deployment
        self._cluster_factory = cluster_factory

    @classmethod
    def from_env(cls, jobs: JobsRegistry) -> PipelineService:
        """Construit le service depuis l'environnement.

        Variables : ``DATASET_DIR``, ``CORPUS_CURE``, ``RAG_INDEX_PATH``,
        ``EMBEDDINGS_URL``, ``API_DEPLOYMENT``.
        """
        dataset = Path(os.environ.get("DATASET_DIR", "/data"))
        corpus = Path(os.environ.get("CORPUS_CURE", str(dataset / "corpus_cure.jsonl")))
        index = Path(os.environ.get("RAG_INDEX_PATH", str(dataset / "rag_index.jsonl")))
        embeddings = EmbeddingsClient(
            base_url=os.environ.get("EMBEDDINGS_URL", "http://embeddings:8001"),
            timeout_s=float(os.environ.get("EMBEDDINGS_TIMEOUT_S", "120")),
        )
        return cls(
            dataset_dir=dataset,
            corpus_cure=corpus,
            index_path=index,
            jobs=jobs,
            embeddings=embeddings,
            api_deployment=os.environ.get("API_DEPLOYMENT", "api"),
            cluster_factory=ClusterClient.from_serviceaccount,
        )

    # --- Reindex RAG (à chaud) ---

    async def demarrer_reindex(self) -> dict | None:
        """Crée un job de reindex, sauf si un est déjà en cours.

        Returns:
            Le job créé, ou ``None`` si un reindex est déjà en cours (conflit).
        """
        if await self._jobs.actif("rag_reindex"):
            return None
        return await self._jobs.creer("rag_reindex")

    async def reindexer_rag(self, job_id: str) -> None:
        """Ajoute les faits curés à l'index RAG puis redémarre l'API.

        Tâche de fond : met à jour le job au fil de l'eau. Additive — n'enlève
        jamais d'entrée existante.

        Args:
            job_id: Identifiant du job à suivre.
        """
        try:
            # Économe en mémoire : on ne charge QUE les réponses déjà indexées
            # (pas les vecteurs ni le fichier entier) pour dédupliquer, puis on
            # ajoute les nouvelles entrées en fin de fichier. Indispensable sur un
            # gros index (dizaines de Mo) avec une console à faible mémoire.
            textes_connus = lire_textes_indexes(self._index_path)
            paires = charger_paires([self._corpus_cure])
            nouvelles = filtrer_nouvelles(textes_connus, paires)
            total_actuel = len(textes_connus)
            await self._jobs.maj(
                job_id,
                log=f"{len(nouvelles)} nouveau(x) fait(s) — index actuel : {total_actuel}",
            )
            if not nouvelles:
                await self._jobs.maj(
                    job_id,
                    statut="reussi",
                    message="Index déjà à jour (aucun nouveau fait curé).",
                    details={"ajoutees": 0, "total": total_actuel},
                )
                return

            await self._jobs.maj(job_id, log="Vectorisation via le service d'embeddings…")
            vecteurs = await self._embeddings.embed([i for i, _ in nouvelles])
            if not vecteurs:
                await self._jobs.maj(
                    job_id,
                    statut="echec",
                    message="Service d'embeddings indisponible — index inchangé.",
                )
                return

            entrees = construire_entrees(nouvelles, vecteurs)
            ajouter_entrees(self._index_path, entrees)
            total = total_actuel + len(entrees)
            await self._jobs.maj(job_id, log=f"{len(entrees)} entrée(s) ajoutée(s) — total {total}")

            try:
                cluster = self._cluster_factory()
                await cluster.rollout_restart(self._api_deployment)
                await cluster.close()
            except ClusterIndisponible as exc:
                await self._jobs.maj(
                    job_id,
                    statut="echec",
                    message=(
                        f"Index reconstruit (+{len(entrees)}, {total} au total) "
                        f"mais redémarrage de l'API échoué : {exc}. "
                        "Relancez le rollout manuellement."
                    ),
                    details={"ajoutees": len(entrees), "total": total},
                )
                return

            await self._jobs.maj(
                job_id,
                statut="reussi",
                message=(
                    f"RAG reconstruit : +{len(entrees)} fait(s), "
                    f"{total} au total. API redémarrée (sans coupure)."
                ),
                details={"ajoutees": len(entrees), "total": total},
            )
        except Exception as exc:  # noqa: BLE001 - journalisé, statut d'échec propre
            # OWASP : on journalise le détail, on n'expose qu'un message générique.
            logger.error("reindex_rag_echec", job_id=job_id, error=str(exc))
            await self._jobs.maj(
                job_id, statut="echec", message="Erreur interne (voir les journaux)."
            )

    # --- Préparation fine-tuning ---

    def _valider_et_dedup(self, paires: list[tuple[str, str]]) -> tuple[list[dict], int]:
        """Valide et déduplique les paires curées pour l'entraînement.

        Applique les mêmes garde-fous que l'assemblage du corpus : bornes de
        longueur, **aucun dosage phytosanitaire**, source reconnue citée, unicité
        de l'instruction.

        Args:
            paires: Couples ``(instruction, output)`` curés.

        Returns:
            ``(gardees, rejetees)`` : paires valides uniques et nombre d'écartées.
        """
        vues: set[str] = set()
        gardees: list[dict] = []
        rejetees = 0
        for instruction, output in paires:
            valide = (
                _MIN_INSTRUCTION <= len(instruction) <= _MAX_INSTRUCTION
                and _MIN_OUTPUT <= len(output) <= _MAX_OUTPUT
                and guardrails.verifier_reponse(output) is None
                and any(s.lower() in output.lower() for s in _SOURCES)
            )
            cle = _cle(instruction)
            if not valide or cle in vues:
                rejetees += 1
                continue
            vues.add(cle)
            gardees.append({"instruction": instruction, "input": "", "output": output})
        return gardees, rejetees

    async def preparer_finetuning(self) -> dict | None:
        """Assemble le corpus curé et émet la procédure d'entraînement.

        Returns:
            Le job résultant (statut ``reussi`` ou ``echec``).
        """
        if await self._jobs.actif("finetuning_prepare"):
            return None
        job = await self._jobs.creer("finetuning_prepare")
        job_id = job["id"]
        try:
            paires = charger_paires([self._corpus_cure])
            gardees, rejetees = self._valider_et_dedup(paires)
            if not gardees:
                await self._jobs.maj(
                    job_id,
                    statut="echec",
                    message=(
                        "Aucune paire curée valide à entraîner. "
                        "Validez d'abord des réponses dans la console."
                    ),
                )
                return await self._jobs.obtenir(job_id)

            sortie = self._dataset_dir / "corpus_entrainement_cure.jsonl"
            sortie.parent.mkdir(parents=True, exist_ok=True)
            with sortie.open("w", encoding="utf-8") as handle:
                for paire in gardees:
                    handle.write(json.dumps(paire, ensure_ascii=False) + "\n")

            await self._jobs.maj(
                job_id,
                statut="reussi",
                message=(
                    f"{len(gardees)} paire(s) curée(s) prêtes "
                    f"({rejetees} écartée(s)). Lancez l'entraînement sur un pod GPU."
                ),
                details={
                    "paires": len(gardees),
                    "rejetees": rejetees,
                    "fichier": str(sortie),
                    "procedure": _PROCEDURE,
                },
            )
            return await self._jobs.obtenir(job_id)
        except Exception as exc:  # noqa: BLE001 - journalisé, statut d'échec propre
            # OWASP : on journalise le détail, on n'expose qu'un message générique.
            logger.error("preparer_finetuning_echec", job_id=job_id, error=str(exc))
            await self._jobs.maj(
                job_id, statut="echec", message="Erreur interne (voir les journaux)."
            )
            return await self._jobs.obtenir(job_id)
