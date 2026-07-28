"""Service métier des parcelles — validation, écriture disque, recevabilité.

Le routeur ne décide de rien : toutes les règles vivent ici, conformément à la
séparation imposée par ``CLAUDE.md`` (aucune logique métier dans les routers).

Trois responsabilités, dans cet ordre :

1. **Valider la géographie** — un point hors de l'enveloppe ivoirienne, un anneau qui
   se coupe, une superficie absurde : refus motivé, en français.
2. **Écrire les images sur disque** — nom de fichier dérivé de l'empreinte SHA-256 du
   contenu, **jamais d'une donnée fournie par le client** : aucune traversée de chemin
   n'est possible, et l'empreinte déduplique naturellement.
3. **Rendre un verdict de recevabilité** par image, persisté avec la capture.

Une image refusée est **quand même enregistrée** en métadonnées, avec son motif : le
producteur doit voir ce qui a été rejeté et pourquoi. En revanche, ses octets ne
touchent pas le disque si le format n'est pas reconnu.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from app.core.logging import get_logger
from app.domain.ports import ParcelleStorePort
from app.models.parcelle import (
    POINTS_MIN_POLYGONE,
    SUPERFICIE_MAX_HA,
    SUPERFICIE_MIN_HA,
    Capture,
    CaptureRequest,
    Coordonnee,
    CoordonneeRequest,
    CreerParcelleRequest,
    Geometrie,
    GeometrieRequest,
    Image,
    ImageRequest,
    MotifRecevabilite,
    Parcelle,
    Recevabilite,
)
from app.services.contacts import chercher as chercher_direction_regionale
from app.services.geometrie import anneau_auto_intersecte, dans_cote_ivoire
from app.services.vision.recevabilite import TAILLE_MAX_OCTETS, evaluer

logger = get_logger(__name__)

_CONSEIL_FORMAT = "Ce fichier n'est pas une photo reconnue. Envoyez une image JPEG ou PNG."


class GeometrieInvalide(Exception):
    """Une géométrie soumise ne peut pas décrire une parcelle cacaoyère ivoirienne."""

    def __init__(self, motif: str) -> None:
        """Initialise l'exception.

        Args:
            motif: Explication en français, destinée à être affichée au producteur.
        """
        super().__init__(motif)
        self.motif = motif


class ParcelleIntrouvable(Exception):
    """La parcelle visée n'existe pas, ou n'appartient pas à cet appareil."""


class ServiceParcelles:
    """Orchestration métier des parcelles et de leurs captures terrain."""

    def __init__(
        self,
        store: ParcelleStorePort,
        dossier_captures: Path,
        retention_jours: int = 90,
        taille_max_octets: int = TAILLE_MAX_OCTETS,
    ) -> None:
        """Initialise le service.

        Args:
            store: Dépôt de persistance des parcelles.
            dossier_captures: Dossier où écrire les images (volume ``/data``).
            retention_jours: Rétention des captures avant purge, en jours.
            taille_max_octets: Plafond de taille par image, après décodage.
        """
        self._store = store
        self._dossier = dossier_captures
        self._retention_jours = retention_jours
        self._taille_max = taille_max_octets

    # ------------------------------------------------------------- parcelles

    async def creer(self, proprietaire: str, requete: CreerParcelleRequest) -> Parcelle:
        """Crée une parcelle et la rattache à sa direction régionale ANADER.

        Le rattachement réutilise l'annuaire de mise en relation ANADER déjà en place
        (``services/contacts.py``) : une localité inconnue donne une chaîne vide, jamais
        une direction inventée.

        Args:
            proprietaire: Identifiant anonyme de l'appareil.
            requete: Nom et localité déclarés par le producteur.

        Returns:
            La parcelle créée.
        """
        contact = chercher_direction_regionale(requete.localite)
        return await self._store.creer_parcelle(
            proprietaire,
            requete.nom,
            requete.localite,
            contact.nom if contact else "",
        )

    async def lister(self, proprietaire: str) -> list[Parcelle]:
        """Liste les parcelles de cet appareil.

        Args:
            proprietaire: Identifiant anonyme de l'appareil.

        Returns:
            Les parcelles de cet appareil, les plus récemment modifiées d'abord.
        """
        return await self._store.lister_parcelles(proprietaire)

    async def obtenir(self, identifiant: str, proprietaire: str) -> Parcelle | None:
        """Retourne une parcelle de cet appareil, ou None.

        Args:
            identifiant: Identifiant de la parcelle.
            proprietaire: Identifiant anonyme de l'appareil.

        Returns:
            La parcelle, ou ``None`` si elle n'existe pas pour cet appareil.
        """
        return await self._store.obtenir_parcelle(identifiant, proprietaire)

    async def obtenir_capture(self, identifiant: str, proprietaire: str) -> Capture | None:
        """Retourne une capture de cet appareil, ou None.

        Args:
            identifiant: Identifiant de la capture.
            proprietaire: Identifiant anonyme de l'appareil.

        Returns:
            La capture, ou ``None`` si elle n'existe pas pour cet appareil.
        """
        return await self._store.obtenir_capture(identifiant, proprietaire)

    # ------------------------------------------------------------- géométrie

    @staticmethod
    def _en_coordonnees(points: list[CoordonneeRequest]) -> tuple[Coordonnee, ...]:
        """Convertit des points d'API en points de domaine."""
        return tuple(
            Coordonnee(
                latitude=p.latitude,
                longitude=p.longitude,
                precision_m=p.precision_m,
                horodatage=p.horodatage,
            )
            for p in points
        )

    def _valider_points(self, points: tuple[Coordonnee, ...]) -> None:
        """Vérifie que des points peuvent décrire le contour d'une parcelle ivoirienne.

        Ne contrôle **pas** la superficie : celle-ci n'existe qu'une fois la géométrie
        construite, et c'est ``enregistrer_geometrie`` qui s'en charge.

        Args:
            points: Sommets relevés, dans l'ordre du parcours.

        Raises:
            GeometrieInvalide: Si un point sort du pays, ou si l'anneau se coupe.
        """
        for point in points:
            if not dans_cote_ivoire(point.latitude, point.longitude):
                raise GeometrieInvalide(
                    "Un des points relevés se trouve hors de la Côte d'Ivoire. "
                    "Vérifiez que la géolocalisation du téléphone est active."
                )
        if len(points) < POINTS_MIN_POLYGONE:
            return
        if anneau_auto_intersecte([(p.latitude, p.longitude) for p in points]):
            raise GeometrieInvalide(
                "Le tracé se coupe lui-même : refaites le tour de la parcelle sans "
                "revenir en arrière."
            )

    async def enregistrer_geometrie(
        self, identifiant: str, proprietaire: str, requete: GeometrieRequest
    ) -> Parcelle:
        """Valide puis enregistre la géométrie d'une parcelle.

        Args:
            identifiant: Identifiant de la parcelle.
            proprietaire: Identifiant anonyme de l'appareil.
            requete: Points relevés et provenance de la géométrie.

        Returns:
            La parcelle mise à jour.

        Raises:
            ParcelleIntrouvable: Si la parcelle n'existe pas pour cet appareil.
            GeometrieInvalide: Si la géométrie n'est pas plausible.
        """
        if await self._store.obtenir_parcelle(identifiant, proprietaire) is None:
            raise ParcelleIntrouvable(identifiant)
        points = self._en_coordonnees(requete.points)
        self._valider_points(points)
        geometrie = Geometrie.depuis_points(points, source=requete.source)
        if geometrie.superficie_ha is not None and not (
            SUPERFICIE_MIN_HA <= geometrie.superficie_ha <= SUPERFICIE_MAX_HA
        ):
            raise GeometrieInvalide(
                f"La superficie calculée ({geometrie.superficie_ha:.2f} ha) sort des "
                f"bornes attendues pour une parcelle ({SUPERFICIE_MIN_HA} à "
                f"{SUPERFICIE_MAX_HA} ha). Refaites le tour de la parcelle."
            )
        maj = await self._store.enregistrer_geometrie(identifiant, proprietaire, geometrie)
        if maj is None:
            raise ParcelleIntrouvable(identifiant)
        logger.info(
            "parcelle_geometrie_enregistree",
            parcelle=identifiant,
            points=len(points),
            superficie_ha=geometrie.superficie_ha,
        )
        return maj

    # --------------------------------------------------------------- captures

    def _ecrire_image(self, donnees: bytes) -> str:
        """Écrit les octets d'une image et retourne son empreinte SHA-256.

        Le nom de fichier dérive de l'empreinte du contenu, jamais d'une donnée du
        client : aucune traversée de chemin n'est possible, et deux téléversements
        identiques ne consomment qu'un fichier.

        Args:
            donnees: Contenu binaire décodé de l'image.

        Returns:
            L'empreinte SHA-256 hexadécimale du contenu.
        """
        empreinte = hashlib.sha256(donnees).hexdigest()
        self._dossier.mkdir(parents=True, exist_ok=True)
        chemin = self._dossier / f"{empreinte}.bin"
        if not chemin.exists():
            chemin.write_bytes(donnees)
        return empreinte

    def _traiter_image(self, requete: ImageRequest) -> Image:
        """Décode, évalue et persiste une image ; rend son enregistrement de domaine.

        Args:
            requete: Image téléversée et métriques déclarées par le client.

        Returns:
            L'image de domaine, avec son verdict de recevabilité.
        """
        try:
            donnees = base64.b64decode(requete.contenu_base64, validate=True)
        except (binascii.Error, ValueError):
            return self._image_refusee(requete, "base64_invalide")
        if len(donnees) > self._taille_max:
            return self._image_refusee(requete, "trop_lourde")

        verdict = evaluer(requete, donnees)
        if verdict.motif is MotifRecevabilite.FORMAT_REFUSE:
            return self._image_refusee(requete, "format_inconnu")

        empreinte = self._ecrire_image(donnees)
        return Image(
            empreinte_sha256=empreinte,
            largeur=requete.largeur,
            hauteur=requete.hauteur,
            recevabilite=verdict,
            coordonnee=(
                self._en_coordonnees([requete.coordonnee])[0] if requete.coordonnee else None
            ),
        )

    def _image_refusee(self, requete: ImageRequest, cause: str) -> Image:
        """Fabrique l'enregistrement d'une image rejetée, sans écrire sur disque.

        Args:
            requete: Image téléversée telle que reçue.
            cause: Cause technique du rejet, pour la journalisation.

        Returns:
            Une image de domaine sans empreinte, porteuse du motif de refus.
        """
        logger.info("capture_image_refusee", cause=cause)
        return Image(
            empreinte_sha256="",
            largeur=requete.largeur,
            hauteur=requete.hauteur,
            recevabilite=Recevabilite(
                recevable=False,
                motif=MotifRecevabilite.FORMAT_REFUSE,
                conseil=_CONSEIL_FORMAT,
                score_nettete=requete.score_nettete,
            ),
        )

    async def deposer_capture(
        self, identifiant: str, proprietaire: str, requete: CaptureRequest
    ) -> Capture:
        """Traite et persiste une capture terrain.

        Args:
            identifiant: Identifiant de la parcelle visée.
            proprietaire: Identifiant anonyme de l'appareil.
            requete: Images et/ou trace relevées sur le terrain.

        Returns:
            La capture persistée, verdicts de recevabilité compris.

        Raises:
            ParcelleIntrouvable: Si la parcelle n'existe pas pour cet appareil.
            GeometrieInvalide: Si un point de la trace sort de la Côte d'Ivoire.
        """
        if await self._store.obtenir_parcelle(identifiant, proprietaire) is None:
            raise ParcelleIntrouvable(identifiant)
        trace = self._en_coordonnees(requete.trace)
        for point in trace:
            if not dans_cote_ivoire(point.latitude, point.longitude):
                raise GeometrieInvalide(
                    "Un des points du parcours se trouve hors de la Côte d'Ivoire."
                )
        images = tuple(self._traiter_image(image) for image in requete.images)
        capture = Capture(
            identifiant=uuid4().hex,
            parcelle=identifiant,
            proprietaire=proprietaire,
            modalite=requete.modalite,
            cree_le=datetime.now(UTC),
            images=images,
            trace=trace,
        )
        await self._store.enregistrer_capture(capture)
        logger.info(
            "capture_deposee",
            parcelle=identifiant,
            modalite=requete.modalite.value,
            images=len(images),
            points=len(trace),
            refusees=sum(1 for i in images if not i.recevabilite.recevable),
        )
        return capture

    # ------------------------------------------------------------------ purge

    async def purger(self, maintenant: datetime | None = None) -> int:
        """Supprime les captures expirées et leurs fichiers.

        Args:
            maintenant: Instant de référence (injecté par les tests).

        Returns:
            Le nombre de fichiers effectivement supprimés du disque.
        """
        reference = maintenant or datetime.now(UTC)
        empreintes = await self._store.purger_captures(
            reference - timedelta(days=self._retention_jours)
        )
        supprimes = 0
        for empreinte in empreintes:
            if not empreinte:
                continue
            chemin = self._dossier / f"{empreinte}.bin"
            if chemin.exists():
                chemin.unlink()
                supprimes += 1
        return supprimes
