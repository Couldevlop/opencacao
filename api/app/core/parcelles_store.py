"""Stockage durable des parcelles et de leurs captures terrain (SQLite, stdlib).

Même choix de conception que :mod:`app.core.sessions` et :mod:`app.core.auth_store` —
``sqlite3`` de la bibliothèque standard (aucune dépendance hors spec §2.1), fichier sur
le volume ``/data``, migrations versionnées par ``PRAGMA user_version``, accès
asynchrone par ``asyncio.to_thread``, écritures sérialisées par un verrou applicatif,
mode WAL pour les lectures concurrentes.

**Initialisation tolérante aux pannes** : si le fichier ne peut être ouvert, le service
démarre quand même et les parcelles sont marquées indisponibles. Le chat ne doit jamais
tomber à cause des parcelles.

Les **images ne sont pas en base** : seule leur empreinte SHA-256 et leurs métadonnées
le sont. Les octets vivent sur le disque, écrits par ``services/parcelles.py``. La purge
retourne les empreintes devenues inutiles, à charge de l'appelant d'effacer les fichiers.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.core.config import Settings
from app.core.logging import get_logger
from app.models.parcelle import (
    Capture,
    Coordonnee,
    Geometrie,
    Image,
    Modalite,
    MotifRecevabilite,
    Parcelle,
    Recevabilite,
    SourceGeometrie,
    TypeGeometrie,
)

logger = get_logger(__name__)

RETENTION_JOURS_DEFAUT = 90


class ParcelleStore:
    """Dépôt SQLite des parcelles cacaoyères et de leurs captures."""

    # Migrations ordonnées : l'indice (0-based) + 1 devient le ``user_version``.
    # Pour faire évoluer le schéma, AJOUTER une entrée à la fin — ne jamais modifier
    # une migration déjà publiée.
    _MIGRATIONS: tuple[str, ...] = (
        """
        CREATE TABLE IF NOT EXISTS parcelles (
            id                  TEXT PRIMARY KEY,
            proprietaire        TEXT NOT NULL,
            nom                 TEXT NOT NULL,
            localite            TEXT NOT NULL,
            direction_regionale TEXT NOT NULL,
            geometrie_json      TEXT,
            cree_le             TEXT NOT NULL,
            maj_le              TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS captures (
            id           TEXT PRIMARY KEY,
            parcelle_id  TEXT NOT NULL REFERENCES parcelles(id) ON DELETE CASCADE,
            proprietaire TEXT NOT NULL,
            modalite     TEXT NOT NULL,
            images_json  TEXT NOT NULL,
            trace_json   TEXT NOT NULL,
            cree_le      TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_parcelles_proprio
            ON parcelles(proprietaire, maj_le DESC);
        CREATE INDEX IF NOT EXISTS idx_captures_parcelle
            ON captures(parcelle_id, cree_le DESC);
        CREATE INDEX IF NOT EXISTS idx_captures_cree
            ON captures(cree_le);
        """,
    )

    def __init__(
        self, chemin: Path, captures_retention_jours: int = RETENTION_JOURS_DEFAUT
    ) -> None:
        """Initialise le dépôt.

        Args:
            chemin: Chemin du fichier SQLite (créé si besoin).
            captures_retention_jours: Rétention des captures, en jours.
        """
        self._chemin = chemin
        self._retention_jours = captures_retention_jours
        self._verrou = asyncio.Lock()
        self._pret = False

    @classmethod
    def from_settings(cls, settings: Settings) -> ParcelleStore:
        """Construit un dépôt à partir des paramètres applicatifs."""
        return cls(
            Path(settings.parcelles_db_path),
            captures_retention_jours=settings.captures_retention_jours,
        )

    @property
    def pret(self) -> bool:
        """Indique si le schéma a pu être initialisé (parcelles disponibles)."""
        return self._pret

    @property
    def retention_jours(self) -> int:
        """Rétention des captures, en jours."""
        return self._retention_jours

    async def initialiser(self) -> None:
        """Crée/migre le schéma. Tolérant aux pannes : ne lève jamais au démarrage."""
        try:
            await asyncio.to_thread(self._migrer)
            self._pret = True
            logger.info("parcelles_pretes", chemin=str(self._chemin))
        except (sqlite3.Error, OSError) as exc:
            self._pret = False
            logger.warning("parcelles_init_echouee", chemin=str(self._chemin), error=str(exc))

    # ------------------------------------------------------------------ schéma

    def _connexion(self) -> sqlite3.Connection:
        """Ouvre une connexion configurée (WAL, clés étrangères)."""
        connexion = sqlite3.connect(self._chemin, timeout=10.0)
        connexion.row_factory = sqlite3.Row
        connexion.execute("PRAGMA journal_mode=WAL")
        connexion.execute("PRAGMA foreign_keys=ON")
        return connexion

    def _migrer(self) -> None:
        """Applique les migrations manquantes, en une transaction par migration."""
        self._chemin.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connexion()) as connexion:
            version = connexion.execute("PRAGMA user_version").fetchone()[0]
            for indice in range(version, len(self._MIGRATIONS)):
                connexion.executescript(self._MIGRATIONS[indice])
                connexion.execute(f"PRAGMA user_version = {indice + 1}")
                connexion.commit()

    # ------------------------------------------------------------ sérialisation

    @staticmethod
    def _coordonnee_en_dict(point: Coordonnee) -> dict[str, object]:
        """Sérialise un point en dictionnaire JSON-compatible."""
        return {
            "latitude": point.latitude,
            "longitude": point.longitude,
            "precision_m": point.precision_m,
            "horodatage": point.horodatage.isoformat() if point.horodatage else None,
        }

    @staticmethod
    def _coordonnee_depuis_dict(brut: dict[str, object]) -> Coordonnee:
        """Reconstruit un point depuis son dictionnaire."""
        horodatage = brut.get("horodatage")
        return Coordonnee(
            latitude=float(brut["latitude"]),  # type: ignore[arg-type]
            longitude=float(brut["longitude"]),  # type: ignore[arg-type]
            precision_m=(
                float(brut["precision_m"])  # type: ignore[arg-type]
                if brut.get("precision_m") is not None
                else None
            ),
            horodatage=datetime.fromisoformat(str(horodatage)) if horodatage else None,
        )

    @classmethod
    def _geometrie_en_json(cls, geometrie: Geometrie) -> str:
        """Sérialise une géométrie."""
        return json.dumps(
            {
                "type": geometrie.type.value,
                "source": geometrie.source.value,
                "superficie_ha": geometrie.superficie_ha,
                "points": [cls._coordonnee_en_dict(p) for p in geometrie.points],
            }
        )

    @classmethod
    def _geometrie_depuis_json(cls, brut: str | None) -> Geometrie | None:
        """Reconstruit une géométrie, ou None si la colonne est vide."""
        if not brut:
            return None
        charge = json.loads(brut)
        return Geometrie(
            type=TypeGeometrie(charge["type"]),
            source=SourceGeometrie(charge["source"]),
            superficie_ha=charge.get("superficie_ha"),
            points=tuple(cls._coordonnee_depuis_dict(p) for p in charge["points"]),
        )

    @classmethod
    def _images_en_json(cls, images: tuple[Image, ...]) -> str:
        """Sérialise les images d'une capture (métadonnées seules)."""
        return json.dumps(
            [
                {
                    "empreinte_sha256": image.empreinte_sha256,
                    "largeur": image.largeur,
                    "hauteur": image.hauteur,
                    "recevabilite": {
                        "recevable": image.recevabilite.recevable,
                        "motif": image.recevabilite.motif.value,
                        "conseil": image.recevabilite.conseil,
                        "score_nettete": image.recevabilite.score_nettete,
                    },
                    "coordonnee": (
                        cls._coordonnee_en_dict(image.coordonnee) if image.coordonnee else None
                    ),
                }
                for image in images
            ]
        )

    @classmethod
    def _images_depuis_json(cls, brut: str) -> tuple[Image, ...]:
        """Reconstruit les images d'une capture."""
        return tuple(
            Image(
                empreinte_sha256=charge["empreinte_sha256"],
                largeur=int(charge["largeur"]),
                hauteur=int(charge["hauteur"]),
                recevabilite=Recevabilite(
                    recevable=bool(charge["recevabilite"]["recevable"]),
                    motif=MotifRecevabilite(charge["recevabilite"]["motif"]),
                    conseil=charge["recevabilite"]["conseil"],
                    score_nettete=float(charge["recevabilite"]["score_nettete"]),
                ),
                coordonnee=(
                    cls._coordonnee_depuis_dict(charge["coordonnee"])
                    if charge.get("coordonnee")
                    else None
                ),
            )
            for charge in json.loads(brut)
        )

    @classmethod
    def _ligne_en_parcelle(cls, ligne: sqlite3.Row) -> Parcelle:
        """Reconstruit une parcelle depuis une ligne SQL."""
        return Parcelle(
            identifiant=ligne["id"],
            proprietaire=ligne["proprietaire"],
            nom=ligne["nom"],
            localite=ligne["localite"],
            direction_regionale=ligne["direction_regionale"],
            geometrie=cls._geometrie_depuis_json(ligne["geometrie_json"]),
            cree_le=datetime.fromisoformat(ligne["cree_le"]),
            maj_le=datetime.fromisoformat(ligne["maj_le"]),
        )

    @classmethod
    def _ligne_en_capture(cls, ligne: sqlite3.Row) -> Capture:
        """Reconstruit une capture depuis une ligne SQL."""
        return Capture(
            identifiant=ligne["id"],
            parcelle=ligne["parcelle_id"],
            proprietaire=ligne["proprietaire"],
            modalite=Modalite(ligne["modalite"]),
            cree_le=datetime.fromisoformat(ligne["cree_le"]),
            images=cls._images_depuis_json(ligne["images_json"]),
            trace=tuple(cls._coordonnee_depuis_dict(p) for p in json.loads(ligne["trace_json"])),
        )

    # -------------------------------------------------------------------- CRUD

    async def creer_parcelle(
        self, proprietaire: str, nom: str, localite: str, direction_regionale: str
    ) -> Parcelle:
        """Crée une parcelle rattachée à un appareil.

        Args:
            proprietaire: Identifiant anonyme de l'appareil.
            nom: Libellé donné par le producteur.
            localite: Localité déclarée.
            direction_regionale: Direction régionale ANADER de rattachement.

        Returns:
            La parcelle créée.
        """
        maintenant = datetime.now(UTC)
        parcelle = Parcelle(
            identifiant=uuid4().hex,
            proprietaire=proprietaire,
            nom=nom,
            localite=localite,
            direction_regionale=direction_regionale,
            cree_le=maintenant,
            maj_le=maintenant,
        )
        if not self._pret:
            return parcelle
        async with self._verrou:
            await asyncio.to_thread(self._inserer_parcelle, parcelle)
        return parcelle

    def _inserer_parcelle(self, parcelle: Parcelle) -> None:
        """Insère une parcelle (appelé dans un thread)."""
        with closing(self._connexion()) as connexion:
            connexion.execute(
                "INSERT INTO parcelles (id, proprietaire, nom, localite, "
                "direction_regionale, geometrie_json, cree_le, maj_le) "
                "VALUES (?, ?, ?, ?, ?, NULL, ?, ?)",
                (
                    parcelle.identifiant,
                    parcelle.proprietaire,
                    parcelle.nom,
                    parcelle.localite,
                    parcelle.direction_regionale,
                    parcelle.cree_le.isoformat(),
                    parcelle.maj_le.isoformat(),
                ),
            )
            connexion.commit()

    async def obtenir_parcelle(self, identifiant: str, proprietaire: str) -> Parcelle | None:
        """Retourne une parcelle de cet appareil, ou None."""
        if not self._pret:
            return None
        return await asyncio.to_thread(self._lire_parcelle, identifiant, proprietaire)

    def _lire_parcelle(self, identifiant: str, proprietaire: str) -> Parcelle | None:
        """Lit une parcelle (appelé dans un thread)."""
        with closing(self._connexion()) as connexion:
            ligne = connexion.execute(
                "SELECT * FROM parcelles WHERE id = ? AND proprietaire = ?",
                (identifiant, proprietaire),
            ).fetchone()
        return self._ligne_en_parcelle(ligne) if ligne else None

    async def lister_parcelles(self, proprietaire: str, limite: int = 50) -> list[Parcelle]:
        """Liste les parcelles de cet appareil, les plus récemment modifiées d'abord."""
        if not self._pret:
            return []
        return await asyncio.to_thread(self._lire_parcelles, proprietaire, limite)

    def _lire_parcelles(self, proprietaire: str, limite: int) -> list[Parcelle]:
        """Lit les parcelles d'un appareil (appelé dans un thread)."""
        with closing(self._connexion()) as connexion:
            lignes = connexion.execute(
                "SELECT * FROM parcelles WHERE proprietaire = ? ORDER BY maj_le DESC LIMIT ?",
                (proprietaire, limite),
            ).fetchall()
        return [self._ligne_en_parcelle(ligne) for ligne in lignes]

    async def enregistrer_geometrie(
        self, identifiant: str, proprietaire: str, geometrie: Geometrie
    ) -> Parcelle | None:
        """Remplace la géométrie d'une parcelle. None si elle n'existe pas."""
        if not self._pret:
            return None
        async with self._verrou:
            await asyncio.to_thread(self._ecrire_geometrie, identifiant, proprietaire, geometrie)
        return await self.obtenir_parcelle(identifiant, proprietaire)

    def _ecrire_geometrie(self, identifiant: str, proprietaire: str, geometrie: Geometrie) -> None:
        """Écrit la géométrie (appelé dans un thread)."""
        with closing(self._connexion()) as connexion:
            connexion.execute(
                "UPDATE parcelles SET geometrie_json = ?, maj_le = ? "
                "WHERE id = ? AND proprietaire = ?",
                (
                    self._geometrie_en_json(geometrie),
                    datetime.now(UTC).isoformat(),
                    identifiant,
                    proprietaire,
                ),
            )
            connexion.commit()

    async def enregistrer_capture(self, capture: Capture) -> Capture:
        """Persiste une capture (images et/ou trace)."""
        if not self._pret:
            return capture
        async with self._verrou:
            await asyncio.to_thread(self._inserer_capture, capture)
        return capture

    def _inserer_capture(self, capture: Capture) -> None:
        """Insère une capture (appelé dans un thread)."""
        with closing(self._connexion()) as connexion:
            connexion.execute(
                "INSERT INTO captures (id, parcelle_id, proprietaire, modalite, "
                "images_json, trace_json, cree_le) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    capture.identifiant,
                    capture.parcelle,
                    capture.proprietaire,
                    capture.modalite.value,
                    self._images_en_json(capture.images),
                    json.dumps([self._coordonnee_en_dict(p) for p in capture.trace]),
                    capture.cree_le.isoformat(),
                ),
            )
            connexion.execute(
                "UPDATE parcelles SET maj_le = ? WHERE id = ?",
                (datetime.now(UTC).isoformat(), capture.parcelle),
            )
            connexion.commit()

    async def compter_captures(self, proprietaire: str) -> int:
        """Compte les captures d'un appareil (application des quotas).

        Args:
            proprietaire: Identifiant anonyme de l'appareil.

        Returns:
            Le nombre de captures persistées, ``0`` si le dépôt n'est pas prêt.
        """
        if not self._pret:
            return 0
        return await asyncio.to_thread(self._compter_captures, proprietaire)

    def _compter_captures(self, proprietaire: str) -> int:
        """Compte les captures d'un appareil (appelé dans un thread)."""
        with closing(self._connexion()) as connexion:
            ligne = connexion.execute(
                "SELECT COUNT(*) AS n FROM captures WHERE proprietaire = ?",
                (proprietaire,),
            ).fetchone()
        return int(ligne["n"])

    async def obtenir_capture(self, identifiant: str, proprietaire: str) -> Capture | None:
        """Retourne une capture de cet appareil, ou None."""
        if not self._pret:
            return None
        return await asyncio.to_thread(self._lire_capture, identifiant, proprietaire)

    def _lire_capture(self, identifiant: str, proprietaire: str) -> Capture | None:
        """Lit une capture (appelé dans un thread)."""
        with closing(self._connexion()) as connexion:
            ligne = connexion.execute(
                "SELECT * FROM captures WHERE id = ? AND proprietaire = ?",
                (identifiant, proprietaire),
            ).fetchone()
        return self._ligne_en_capture(ligne) if ligne else None

    async def lister_captures(self, parcelle: str, proprietaire: str) -> list[Capture]:
        """Liste les captures d'une parcelle, les plus récentes d'abord."""
        if not self._pret:
            return []
        return await asyncio.to_thread(self._lire_captures, parcelle, proprietaire)

    def _lire_captures(self, parcelle: str, proprietaire: str) -> list[Capture]:
        """Lit les captures d'une parcelle (appelé dans un thread)."""
        with closing(self._connexion()) as connexion:
            lignes = connexion.execute(
                "SELECT * FROM captures WHERE parcelle_id = ? AND proprietaire = ? "
                "ORDER BY cree_le DESC",
                (parcelle, proprietaire),
            ).fetchall()
        return [self._ligne_en_capture(ligne) for ligne in lignes]

    async def purger_captures(self, avant: datetime) -> list[str]:
        """Supprime les captures antérieures à une date et rend les empreintes.

        Les fichiers d'images ne sont pas effacés ici : la base ne connaît pas le
        disque. L'appelant (``services/parcelles.py``) supprime les fichiers dont les
        empreintes sont retournées.

        Args:
            avant: Date limite ; toute capture plus ancienne est supprimée.

        Returns:
            Les empreintes SHA-256 des images devenues orphelines.
        """
        if not self._pret:
            return []
        async with self._verrou:
            return await asyncio.to_thread(self._purger, avant)

    def _purger(self, avant: datetime) -> list[str]:
        """Purge et collecte les empreintes (appelé dans un thread)."""
        limite = avant.isoformat()
        with closing(self._connexion()) as connexion:
            lignes = connexion.execute(
                "SELECT images_json FROM captures WHERE cree_le < ?", (limite,)
            ).fetchall()
            empreintes = [
                charge["empreinte_sha256"]
                for ligne in lignes
                for charge in json.loads(ligne["images_json"])
            ]
            connexion.execute("DELETE FROM captures WHERE cree_le < ?", (limite,))
            connexion.commit()
        if empreintes:
            logger.info("captures_purgees", nombre=len(empreintes))
        return empreintes
