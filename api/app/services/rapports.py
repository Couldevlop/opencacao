"""Service des rapports — exécution asynchrone et export (spec §8.6).

**Le synchrone est exclu.** Une étude représente 10 à 30 générations, et le time-out
edge Cloudflare (~100 s) l'interdirait de toute façon : la leçon des 524 de juin est
acquise. Le job est donc persisté, exécuté en flux, et **le premier événement part
avant toute génération** — un premier octet rapide est ce qui empêche la coupure.

Le document complet n'est **pas** re-généré à l'export : il est rendu une fois en
Markdown à la fin de l'exécution, et les formats binaires sont produits à la demande
depuis le document conservé en mémoire. Après un redémarrage, seul le Markdown
subsiste : on le dit, plutôt que de re-générer un document *différent* sous le même
identifiant — un manifeste doit désigner le document qu'il accompagne.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import AsyncIterator, Callable

from app.application.redaction import MoteurRedaction, SujetRefuse
from app.core.logging import get_logger
from app.core.rapports_store import EtatRapport, Rapport, RapportStore
from app.models.rapport import Document
from app.services.gabarits import charger_gabarit
from app.services.rendu.diapositives import rendu_pptx
from app.services.rendu.markdown import rendu_markdown
from app.services.rendu.tableur import rendu_excel
from app.services.rendu.word import rendu_word

logger = get_logger(__name__)

TYPES_MIME: dict[str, tuple[str, str]] = {
    "md": ("text/markdown; charset=utf-8", "md"),
    "docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "docx",
    ),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
    "pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "pptx",
    ),
}

_RENDUS_BINAIRES = {"docx": rendu_word, "xlsx": rendu_excel, "pptx": rendu_pptx}

# Message rendu au client en cas d'échec. Le détail va au journal : une trace
# d'inférence ou un motif de garde-fou n'apprend rien d'utile au demandeur.
_ECHEC = "La génération n'a pas abouti."

# Ce document est déjà pris : rechargement d'onglet, ou deux clients sur le même lien.
_DEJA_PRIS = "Ce document est déjà en cours de génération ou terminé."

# Documents gardés en mémoire pour l'export binaire. Un document au plafond de gabarit
# pèse ~400 Kio : sans borne, le pod (1 Gio, partagé avec l'index RAG) finit par tomber.
# L'éviction rend l'export binaire indisponible exactement comme après un redémarrage —
# un cas déjà géré, et déjà testé.
MAX_DOCUMENTS_EN_MEMOIRE = 32


class RapportIntrouvable(Exception):
    """Le rapport visé n'existe pas, n'appartient pas au demandeur, ou n'est pas prêt."""


class ServiceRapports:
    """Crée, exécute et exporte les rapports."""

    def __init__(self, store: RapportStore, moteur: Callable[[], MoteurRedaction]) -> None:
        """Initialise le service.

        Args:
            store: Dépôt de persistance des jobs.
            moteur: Fabrique du moteur de rédaction (une instance par exécution).
        """
        self._store = store
        self._moteur = moteur
        self._documents: OrderedDict[str, Document] = OrderedDict()

    async def creer(self, gabarit: str, sujet: str, demandeur: str) -> Rapport:
        """Crée un job de rapport.

        Args:
            gabarit: Identifiant du gabarit.
            sujet: Sujet du document.
            demandeur: Identifiant anonyme du demandeur.

        Returns:
            Le job créé, en attente.

        Raises:
            GabaritInconnu: Le gabarit demandé n'existe pas.
        """
        charger_gabarit(gabarit)  # valide tôt : inutile de persister un job impossible
        return await self._store.creer(gabarit, sujet, demandeur)

    async def obtenir(self, identifiant: str, demandeur: str) -> Rapport | None:
        """Retourne un job de ce demandeur, ou None."""
        return await self._store.obtenir(identifiant, demandeur)

    async def lister(self, demandeur: str) -> list[Rapport]:
        """Liste les jobs d'un demandeur, les plus récents d'abord."""
        return await self._store.lister(demandeur)

    async def executer(self, identifiant: str, demandeur: str) -> AsyncIterator[dict]:
        """Exécute un job et émet ses événements au fil de la rédaction.

        Args:
            identifiant: Identifiant du job.
            demandeur: Identifiant anonyme du demandeur.

        Yields:
            Événements ``progress``, ``section``, puis ``final`` ou ``error``.

        Raises:
            RapportIntrouvable: Job inconnu ou appartenant à un autre appareil.
        """
        rapport = await self._store.obtenir(identifiant, demandeur)
        if rapport is None:
            raise RapportIntrouvable(identifiant)

        # Premier octet immédiat : c'est ce qui évite la coupure edge (524 de juin).
        yield {"type": "progress", "message": "Préparation du document…"}

        # Le flux est un GET : un rechargement d'onglet le rappelle. Sans cette
        # prise atomique, la génération repartait — et si l'inférence était tombée
        # entre-temps, un livrable DÉJÀ RENDU basculait en échec. Un GET ne détruit rien.
        if not await self._store.demarrer(identifiant, demandeur):
            logger.info("rapport_deja_pris", rapport=identifiant)
            yield {"type": "error", "message": _DEJA_PRIS}
            return

        evenements: list[dict] = []

        async def _progression(faites: int, total: int, titre: str) -> None:
            await self._store.avancer(identifiant, faites, total)
            evenements.append({"type": "section", "titre": titre, "faites": faites, "total": total})

        try:
            gabarit = charger_gabarit(rapport.gabarit)
            document = await self._moteur().rediger(
                gabarit, rapport.sujet, demandeur, progression=_progression
            )
        except SujetRefuse as refus:
            await self._store.echouer(identifiant, refus.message)
            logger.info("rapport_sujet_refuse", rapport=identifiant)
            yield {"type": "error", "message": refus.message}
            return
        except Exception as exc:  # noqa: BLE001 — un job échoue PROPREMENT, il ne pend pas
            await self._store.echouer(identifiant, type(exc).__name__)
            logger.warning("rapport_echoue", rapport=identifiant, error=type(exc).__name__)
            yield {"type": "error", "message": _ECHEC}
            return

        for evenement in evenements:
            yield evenement

        markdown = rendu_markdown(document)
        self._retenir(identifiant, document)
        await self._store.terminer(identifiant, markdown)
        logger.info("rapport_termine", rapport=identifiant, sections=len(document.sections))
        yield {"type": "final", "titre": document.titre, "sections": len(document.sections)}

    def _retenir(self, identifiant: str, document: Document) -> None:
        """Garde le document pour l'export binaire, sans laisser la mémoire croître.

        Args:
            identifiant: Identifiant du job.
            document: Document produit.
        """
        self._documents[identifiant] = document
        self._documents.move_to_end(identifiant)
        while len(self._documents) > MAX_DOCUMENTS_EN_MEMOIRE:
            evince, _ = self._documents.popitem(last=False)
            logger.info("rapport_document_evince", rapport=evince)

    async def exporter(
        self, identifiant: str, demandeur: str, format_demande: str
    ) -> tuple[bytes, str, str]:
        """Exporte un rapport terminé dans l'un des quatre formats.

        Args:
            identifiant: Identifiant du job.
            demandeur: Identifiant anonyme du demandeur.
            format_demande: ``md``, ``docx``, ``xlsx`` ou ``pptx``.

        Returns:
            Le triplet ``(octets, nom de fichier, type MIME)``.

        Raises:
            RapportIntrouvable: Job inconnu, d'un autre appareil, non terminé, ou dont
                le document n'est plus en mémoire (format binaire après redémarrage).
            ValueError: Format demandé hors des quatre pris en charge.
        """
        if format_demande not in TYPES_MIME:
            raise ValueError(format_demande)
        rapport = await self._store.obtenir(identifiant, demandeur)
        if rapport is None or rapport.etat is not EtatRapport.TERMINE:
            raise RapportIntrouvable(identifiant)

        type_mime, extension = TYPES_MIME[format_demande]
        # Le nom de fichier part dans un en-tête HTTP : il ne reprend QUE des valeurs
        # maîtrisées — un identifiant de gabarit issu d'une liste blanche et un
        # identifiant hexadécimal. Jamais le sujet, qui vient du client.
        nom = f"{rapport.gabarit}-{identifiant[:8]}.{extension}"
        if format_demande == "md":
            return rapport.markdown.encode("utf-8"), nom, type_mime

        document = self._documents.get(identifiant)
        if document is None:
            # Seul le Markdown est persisté. Re-générer produirait un AUTRE document
            # sous le même identifiant, que son manifeste ne décrirait plus.
            logger.info("rapport_document_absent", rapport=identifiant, format=format_demande)
            raise RapportIntrouvable(identifiant)
        return _RENDUS_BINAIRES[format_demande](document), nom, type_mime
