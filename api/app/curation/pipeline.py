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

import asyncio
import json
import os
import re
import unicodedata
from collections.abc import Callable
from pathlib import Path

import httpx

from app.core.logging import get_logger
from app.curation.documents import DocumentInvalide, DocumentStore
from app.curation.jobs import JobsRegistry
from app.curation.k8s import ClusterClient, ClusterIndisponible
from app.curation.sources import (
    NAVIGATEUR_UA,
    charger_sources,
    extension_pour,
    telecharger,
)
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
# Résilience de la vectorisation (constitution) : tentatives par lot + pause.
_EMBED_TENTATIVES = 3
_EMBED_DELAI_S = 5.0

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
        documents: DocumentStore,
        http_factory: Callable[[bool], httpx.AsyncClient] | None = None,
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
            documents: Store des documents sources (upload).
        """
        self._dataset_dir = dataset_dir
        self._corpus_cure = corpus_cure
        self._index_path = index_path
        self._jobs = jobs
        self._embeddings = embeddings
        self._api_deployment = api_deployment
        self._cluster_factory = cluster_factory
        self._documents = documents
        self._http_factory = http_factory or (
            lambda verifie: httpx.AsyncClient(
                verify=verifie,
                follow_redirects=True,
                timeout=60.0,
                headers={"User-Agent": NAVIGATEUR_UA},
            )
        )

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
            documents=DocumentStore.from_env(),
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
            textes_connus = await asyncio.to_thread(lire_textes_indexes, self._index_path)
            paires = await asyncio.to_thread(charger_paires, [self._corpus_cure])
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
            await asyncio.to_thread(ajouter_entrees, self._index_path, entrees)
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

    # --- Constitution RAG depuis les documents (upload) ---

    async def demarrer_constitution(self) -> dict | None:
        """Crée un job de constitution, sauf si un est déjà en cours."""
        if await self._jobs.actif("rag_constitution"):
            return None
        return await self._jobs.creer("rag_constitution")

    async def constituer_rag(self, job_id: str) -> None:
        """Découpe les documents en extraits, les vectorise et les ajoute à l'index.

        Embedding direct des extraits (souverain, sans LLM). Additif et économe :
        ne réindexe que les extraits absents, par lots, puis redémarre l'API.

        Args:
            job_id: Identifiant du job à suivre.
        """
        try:
            # Travail bloquant (extraction PDF/HTML) déporté en thread : la boucle
            # asyncio reste libre pour répondre à la sonde de vivacité (sinon le pod
            # est tué pendant les longues extractions).
            extraits = await asyncio.to_thread(self._documents.extraits)
            if not extraits:
                await self._jobs.maj(
                    job_id,
                    statut="echec",
                    message="Aucun document exploitable. Téléversez d'abord des fichiers.",
                )
                return

            connus = await asyncio.to_thread(lire_textes_indexes, self._index_path)
            total_actuel = len(connus)
            nouveaux: list[tuple[str, str]] = []  # (source, extrait)
            for source, extrait in extraits:
                cle = extrait.strip()
                if cle in connus:
                    continue
                connus.add(cle)
                nouveaux.append((source, extrait))
            await self._jobs.maj(
                job_id,
                log=f"{len(extraits)} extrait(s) dans {self._nb_documents()} doc(s) — "
                f"{len(nouveaux)} nouveau(x)",
            )
            if not nouveaux:
                await self._jobs.maj(
                    job_id,
                    statut="reussi",
                    message="Index déjà à jour (aucun nouvel extrait).",
                    details={"ajoutees": 0, "total": total_actuel},
                )
                return

            # Indexation incrémentale : chaque lot vectorisé est ajouté immédiatement.
            # Un échec d'embeddings n'efface donc pas le travail déjà fait — il suffit
            # de relancer la Constitution pour reprendre (dédup automatique).
            ajoutees = await self._indexer_par_lots(job_id, nouveaux)
            total = total_actuel + ajoutees

            if ajoutees:
                try:
                    cluster = self._cluster_factory()
                    await cluster.rollout_restart(self._api_deployment)
                    await cluster.close()
                except ClusterIndisponible as exc:
                    await self._jobs.maj(
                        job_id,
                        statut="echec",
                        message=(
                            f"Index enrichi (+{ajoutees}, {total} au total) mais "
                            f"redémarrage de l'API échoué : {exc}. Relancez le rollout."
                        ),
                        details={"ajoutees": ajoutees, "total": total},
                    )
                    return

            if ajoutees < len(nouveaux):
                await self._jobs.maj(
                    job_id,
                    statut="echec",
                    message=(
                        f"Partiel : {ajoutees}/{len(nouveaux)} extrait(s) indexé(s) "
                        f"({total} au total). Embeddings momentanément indisponible — "
                        "relancez « Constituer » pour continuer."
                    ),
                    details={
                        "ajoutees": ajoutees,
                        "total": total,
                        "restants": len(nouveaux) - ajoutees,
                    },
                )
                return

            await self._jobs.maj(
                job_id,
                statut="reussi",
                message=(
                    f"RAG enrichi : +{ajoutees} extrait(s), {total} au total. "
                    "API redémarrée (sans coupure)."
                ),
                details={"ajoutees": ajoutees, "total": total},
            )
        except Exception as exc:  # noqa: BLE001 - journalisé, statut d'échec propre
            logger.error("constitution_rag_echec", job_id=job_id, error=str(exc))
            await self._jobs.maj(
                job_id, statut="echec", message="Erreur interne (voir les journaux)."
            )

    def _nb_documents(self) -> int:
        """Nombre de documents stockés (pour les messages de progression)."""
        return len(self._documents.lister())

    async def _embed_resilient(self, textes: list[str]) -> list[list[float]] | None:
        """Vectorise un lot avec quelques tentatives (résiste aux hoquets transitoires)."""
        for essai in range(_EMBED_TENTATIVES):
            vecteurs = await self._embeddings.embed(textes)
            if vecteurs:
                return vecteurs
            if essai < _EMBED_TENTATIVES - 1:
                await asyncio.sleep(_EMBED_DELAI_S)
        return None

    async def _indexer_par_lots(
        self, job_id: str, nouveaux: list[tuple[str, str]], lot: int = 8
    ) -> int:
        """Vectorise et ajoute les extraits par lots (incrémental). Retourne le nombre ajouté.

        Chaque lot réussi est écrit immédiatement dans l'index : en cas d'échec
        d'embeddings, le travail déjà fait est conservé (reprise au prochain appel).

        Args:
            job_id: Job à informer de la progression.
            nouveaux: Couples ``(source, extrait)`` à indexer.
            lot: Taille des lots envoyés au service d'embeddings.

        Returns:
            Le nombre d'extraits effectivement ajoutés à l'index.
        """
        ajoutees = 0
        for debut in range(0, len(nouveaux), lot):
            morceau = nouveaux[debut : debut + lot]
            vecteurs = await self._embed_resilient([ex for _, ex in morceau])
            if not vecteurs:
                break  # on s'arrête mais on garde ce qui est déjà indexé
            entrees = [
                {
                    "texte": extrait,
                    "source": source,
                    "vecteur": [round(float(x), 6) for x in vecteur],
                }
                for (source, extrait), vecteur in zip(morceau, vecteurs, strict=True)
            ]
            await asyncio.to_thread(ajouter_entrees, self._index_path, entrees)
            ajoutees += len(entrees)
            await self._jobs.maj(
                job_id,
                log=f"indexation {ajoutees}/{len(nouveaux)}",
                details={"courant": ajoutees, "objectif": len(nouveaux)},
            )
        return ajoutees

    # --- Étape ① Recherche des sources officielles (téléchargement) ---

    async def demarrer_recherche(self) -> dict | None:
        """Crée un job de recherche, sauf si un est déjà en cours."""
        if await self._jobs.actif("recherche_sources"):
            return None
        return await self._jobs.creer("recherche_sources")

    async def collecter_sources(self, job_id: str) -> None:
        """Télécharge les documents officiels (manifeste) vers le store de documents.

        En flux (peu gourmand). Idempotent : saute les documents déjà présents.
        N'enrichit pas le RAG (c'est l'étape ② Constitution qui le fait ensuite).

        Args:
            job_id: Identifiant du job à suivre.
        """
        try:
            sources = charger_sources()
            if not sources:
                await self._jobs.maj(
                    job_id, statut="echec", message="Aucune source officielle configurée."
                )
                return
            telecharges = deja = echoues = 0
            # Un client par mode de vérification TLS (certaines sources publiques ont
            # un certificat cassé et sont marquées ``verify: false`` dans le manifeste).
            clients: dict[bool, httpx.AsyncClient] = {}

            def client_pour(verifie: bool) -> httpx.AsyncClient:
                if verifie not in clients:
                    clients[verifie] = self._http_factory(verifie)
                return clients[verifie]

            try:
                for i, doc in enumerate(sources, start=1):
                    if self._documents.existe_prefixe(doc["id"]):
                        deja += 1
                        continue
                    await self._jobs.maj(
                        job_id,
                        log=f"{i}/{len(sources)} {doc['titre'][:60]}",
                        details={"courant": i, "objectif": len(sources)},
                    )
                    resultat = await telecharger(
                        client_pour(bool(doc.get("verify", True))), doc["url"]
                    )
                    if not resultat:
                        echoues += 1
                        continue
                    donnees, content_type = resultat
                    # Nom final selon le type réel (PDF, HTML, texte…).
                    nom = f"{doc['id']}{extension_pour(doc['url'], content_type)}"
                    try:
                        self._documents.enregistrer(nom, donnees)
                        telecharges += 1
                    except DocumentInvalide:
                        echoues += 1
            finally:
                for client in clients.values():
                    await client.aclose()

            await self._jobs.maj(
                job_id,
                statut="reussi",
                message=(
                    f"{telecharges} document(s) téléchargé(s), {deja} déjà présent(s), "
                    f"{echoues} échec(s). Lancez la Constitution pour les indexer."
                ),
                details={"telecharges": telecharges, "deja": deja, "echoues": echoues},
            )
        except Exception as exc:  # noqa: BLE001 - journalisé, statut d'échec propre
            logger.error("recherche_sources_echec", job_id=job_id, error=str(exc))
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
