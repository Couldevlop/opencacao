"""Service métier du constat visuel : relit les images, contextualise, persiste.

Fait le pont entre la capture stockée par C1 et la cascade d'analyse de C2. Le
constat produit par ``ServiceConstatVisuel`` est **anonyme** (il ne connaît ni sa
capture ni sa parcelle) : c'est ici qu'il est rattaché, par ``dataclasses.replace``
puisque ``Constat`` est immuable.
"""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path

from app.application.constat_visuel import ServiceConstatVisuel
from app.application.fusion_contextuelle import ContexteParcelle
from app.core.logging import get_logger
from app.domain.ports import ParcelleStorePort
from app.models.constat import Constat
from app.models.parcelle import Capture
from app.services.vision.indisponible import CONSIGNE_INDISPONIBLE

logger = get_logger(__name__)


class CaptureIntrouvable(Exception):
    """La capture visée n'existe pas, ou n'appartient pas à cet appareil."""


class VisionIndisponibleErreur(Exception):
    """L'analyse visuelle n'est pas disponible (profil CPU, ou VLM injoignable)."""


class ServiceConstats:
    """Produit et persiste le constat visuel d'une capture."""

    def __init__(
        self,
        store: ParcelleStorePort,
        constat_visuel: ServiceConstatVisuel,
        dossier_captures: Path,
    ) -> None:
        """Initialise le service.

        Args:
            store: Dépôt de persistance des parcelles et constats.
            constat_visuel: Cascade d'analyse (étages 1, 4 et 5).
            dossier_captures: Dossier où C1 a écrit les images.
        """
        self._store = store
        self._analyse = constat_visuel
        self._dossier = dossier_captures

    def _lire_images(self, capture: Capture) -> tuple[tuple[bytes, str], ...]:
        """Relit sur disque les octets des images RECEVABLES d'une capture.

        Les images refusées à l'étage 0 sont écartées : les analyser reviendrait à
        interpréter du bruit, ce que la cascade est faite pour éviter.

        Args:
            capture: Capture dont les images sont relues.

        Returns:
            Les couples ``(octets, empreinte_sha256)`` effectivement présents sur disque.
        """
        lues: list[tuple[bytes, str]] = []
        for image in capture.images:
            if not image.recevabilite.recevable or not image.empreinte_sha256:
                continue
            chemin = self._dossier / f"{image.empreinte_sha256}.bin"
            if chemin.exists():
                lues.append((chemin.read_bytes(), image.empreinte_sha256))
        return tuple(lues)

    async def _contexte(self, parcelle_id: str, proprietaire: str) -> ContexteParcelle:
        """Rassemble ce que la plateforme sait déjà de la parcelle.

        La météo n'est pas branchée dans ce chantier : ``pluie_mm_14j`` vaut ``None``,
        ce que l'étage 4 traduit par une dégradation de confiance et un facteur
        explicite (« relevé de pluie indisponible »). **On ne conforte rien sans
        donnée** — brancher l'outil Open-Meteo existant est une évolution ultérieure,
        et elle ne changera pas ce contrat.

        Args:
            parcelle_id: Identifiant de la parcelle.
            proprietaire: Identifiant anonyme de l'appareil.

        Returns:
            Le contexte connu, aux champs neutres quand la donnée manque.
        """
        parcelle = await self._store.obtenir_parcelle(parcelle_id, proprietaire)
        return ContexteParcelle(
            pluie_mm_14j=None,
            saison="",
            localite=parcelle.localite if parcelle else "",
            alertes_deforestation=None,
        )

    async def produire(self, parcelle_id: str, capture_id: str, proprietaire: str) -> Constat:
        """Produit et persiste le constat visuel d'une capture.

        Args:
            parcelle_id: Identifiant de la parcelle.
            capture_id: Identifiant de la capture à analyser.
            proprietaire: Identifiant anonyme de l'appareil.

        Returns:
            Le constat persisté.

        Raises:
            CaptureIntrouvable: Capture inconnue, d'un autre appareil, d'une autre
                parcelle, ou dépourvue d'image recevable.
            VisionIndisponibleErreur: Vision absente, ou sortie compromise.
        """
        capture = await self._store.obtenir_capture(capture_id, proprietaire)
        if capture is None or capture.parcelle != parcelle_id:
            raise CaptureIntrouvable(capture_id)

        # Idempotence : analyser coûte une génération de vision ET une génération de
        # conseil. Un second appel sur la même capture relit, il ne recalcule pas —
        # c'est aussi ce qui empêche un POST répété de saturer l'inférence.
        deja = await self._store.obtenir_constat_par_capture(capture_id, proprietaire)
        if deja is not None:
            logger.info("constat_deja_produit", capture=capture_id)
            return deja

        # Lecture disque déportée : jusqu'à douze images de plusieurs mégaoctets ne
        # doivent pas bloquer la boucle d'événements, donc toutes les autres requêtes.
        images = await asyncio.to_thread(self._lire_images, capture)
        if not images:
            raise CaptureIntrouvable(capture_id)

        contexte = await self._contexte(parcelle_id, proprietaire)
        constat = await self._analyse.analyser(images, contexte)
        if constat is None:
            raise VisionIndisponibleErreur(CONSIGNE_INDISPONIBLE)

        # Constat immuable : on le rattache par recopie, jamais par mutation.
        rattache = dataclasses.replace(
            constat, capture=capture_id, parcelle=parcelle_id, proprietaire=proprietaire
        )
        await self._store.enregistrer_constat(rattache)
        logger.info(
            "constat_produit",
            parcelle=parcelle_id,
            capture=capture_id,
            images=len(images),
            confiance=rattache.confiance.value,
        )
        return rattache
