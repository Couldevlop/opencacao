"""Persistance des jobs de rapport (V3, chantier C3).

Même moule que :mod:`app.core.sessions` et :mod:`app.core.parcelles_store` : ``sqlite3``
stdlib, migrations versionnées par ``PRAGMA user_version`` appliquées sous
``BEGIN IMMEDIATE``, accès asynchrone par ``asyncio.to_thread``, écritures sérialisées
par un verrou applicatif, WAL, **initialisation tolérante aux pannes**.

**Pourquoi persister.** Une étude représente 10 à 30 générations : sur CPU, cela dépasse
largement toute requête HTTP raisonnable. Le job doit donc survivre à un redémarrage de
l'API — soit repris, soit **proprement déclaré échoué**. Un job resté « en cours » après
un redémarrage est orphelin : personne ne le reprendra, et le laisser dans cet état
ferait tourner un client en attente indéfiniment.
"""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from uuid import uuid4

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class EtatRapport(str, Enum):
    """Cycle de vie d'un job de rapport."""

    EN_ATTENTE = "en_attente"
    EN_COURS = "en_cours"
    TERMINE = "termine"
    ECHOUE = "echoue"


@dataclass(frozen=True)
class Rapport:
    """Un job de rapport et son état.

    Attributes:
        identifiant: Identifiant opaque du job.
        gabarit: Identifiant du gabarit demandé.
        sujet: Sujet du document.
        demandeur: Identifiant anonyme de l'appareil demandeur.
        etat: Étape du cycle de vie.
        sections_faites: Nombre de sections rédigées.
        sections_total: Nombre de sections prévues par le gabarit.
        markdown: Rendu Markdown, une fois le job terminé.
        erreur: Raison de l'échec, le cas échéant.
        cree_le: Horodatage de création (UTC).
        maj_le: Horodatage de dernière mise à jour (UTC).
    """

    identifiant: str
    gabarit: str
    sujet: str
    demandeur: str
    etat: EtatRapport
    sections_faites: int
    sections_total: int
    markdown: str
    erreur: str
    cree_le: datetime
    maj_le: datetime


class RapportStore:
    """Dépôt SQLite des jobs de rapport."""

    _MIGRATIONS: tuple[str, ...] = (
        """
        CREATE TABLE IF NOT EXISTS rapports (
            id              TEXT PRIMARY KEY,
            gabarit         TEXT NOT NULL,
            sujet           TEXT NOT NULL,
            demandeur       TEXT NOT NULL,
            etat            TEXT NOT NULL,
            sections_faites INTEGER NOT NULL DEFAULT 0,
            sections_total  INTEGER NOT NULL DEFAULT 0,
            markdown        TEXT NOT NULL DEFAULT '',
            erreur          TEXT NOT NULL DEFAULT '',
            cree_le         TEXT NOT NULL,
            maj_le          TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_rapports_demandeur
            ON rapports(demandeur, cree_le DESC);
        CREATE INDEX IF NOT EXISTS idx_rapports_etat ON rapports(etat);
        """,
    )

    def __init__(self, chemin: Path) -> None:
        """Initialise le dépôt.

        Args:
            chemin: Chemin du fichier SQLite (créé si besoin).
        """
        self._chemin = chemin
        self._verrou = asyncio.Lock()
        self._pret = False

    @classmethod
    def from_settings(cls, settings: Settings) -> RapportStore:
        """Construit un dépôt à partir des paramètres applicatifs."""
        return cls(Path(settings.rapports_db_path))

    @property
    def pret(self) -> bool:
        """Indique si le schéma a pu être initialisé."""
        return self._pret

    async def initialiser(self) -> None:
        """Crée/migre le schéma. Tolérant aux pannes : ne lève jamais au démarrage."""
        try:
            await asyncio.to_thread(self._migrer)
            self._pret = True
            logger.info("rapports_prets", chemin=str(self._chemin))
        except (sqlite3.Error, OSError) as exc:
            self._pret = False
            logger.warning("rapports_init_echouee", chemin=str(self._chemin), error=str(exc))

    def _connexion(self) -> sqlite3.Connection:
        """Ouvre une connexion configurée (WAL)."""
        connexion = sqlite3.connect(self._chemin, timeout=10.0)
        connexion.row_factory = sqlite3.Row
        connexion.execute("PRAGMA journal_mode=WAL")
        return connexion

    @staticmethod
    def _instructions(script: str) -> tuple[str, ...]:
        """Découpe un script de migration en instructions exécutables une à une."""
        return tuple(
            instruction.strip() for instruction in script.split(";") if instruction.strip()
        )

    def _migrer(self) -> None:
        """Applique les migrations manquantes, sous verrou d'écriture exclusif.

        ``executescript`` validerait implicitement la transaction et relâcherait le
        verrou pris juste avant — leçon acquise sur ``parcelles_store``.
        """
        self._chemin.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connexion()) as connexion:
            connexion.isolation_level = None
            connexion.execute("BEGIN IMMEDIATE")
            try:
                version = int(connexion.execute("PRAGMA user_version").fetchone()[0])
                for indice in range(version, len(self._MIGRATIONS)):
                    for instruction in self._instructions(self._MIGRATIONS[indice]):
                        connexion.execute(instruction)
                    connexion.execute(f"PRAGMA user_version = {indice + 1}")
                connexion.execute("COMMIT")
            except sqlite3.Error:
                connexion.execute("ROLLBACK")
                raise

    @staticmethod
    def _ligne_en_rapport(ligne: sqlite3.Row) -> Rapport:
        """Reconstruit un rapport depuis une ligne SQL."""
        return Rapport(
            identifiant=ligne["id"],
            gabarit=ligne["gabarit"],
            sujet=ligne["sujet"],
            demandeur=ligne["demandeur"],
            etat=EtatRapport(ligne["etat"]),
            sections_faites=int(ligne["sections_faites"]),
            sections_total=int(ligne["sections_total"]),
            markdown=ligne["markdown"],
            erreur=ligne["erreur"],
            cree_le=datetime.fromisoformat(ligne["cree_le"]),
            maj_le=datetime.fromisoformat(ligne["maj_le"]),
        )

    async def creer(self, gabarit: str, sujet: str, demandeur: str) -> Rapport:
        """Crée un job en attente.

        Args:
            gabarit: Identifiant du gabarit demandé.
            sujet: Sujet du document.
            demandeur: Identifiant anonyme du demandeur.

        Returns:
            Le job créé.
        """
        horodatage = datetime.now(UTC)
        rapport = Rapport(
            identifiant=uuid4().hex,
            gabarit=gabarit,
            sujet=sujet,
            demandeur=demandeur,
            etat=EtatRapport.EN_ATTENTE,
            sections_faites=0,
            sections_total=0,
            markdown="",
            erreur="",
            cree_le=horodatage,
            maj_le=horodatage,
        )
        if not self._pret:
            return rapport
        async with self._verrou:
            await asyncio.to_thread(self._inserer, rapport)
        return rapport

    def _inserer(self, rapport: Rapport) -> None:
        """Insère un job (appelé dans un thread)."""
        with closing(self._connexion()) as connexion:
            connexion.execute(
                "INSERT INTO rapports (id, gabarit, sujet, demandeur, etat, sections_faites, "
                "sections_total, markdown, erreur, cree_le, maj_le) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    rapport.identifiant,
                    rapport.gabarit,
                    rapport.sujet,
                    rapport.demandeur,
                    rapport.etat.value,
                    rapport.sections_faites,
                    rapport.sections_total,
                    rapport.markdown,
                    rapport.erreur,
                    rapport.cree_le.isoformat(),
                    rapport.maj_le.isoformat(),
                ),
            )
            connexion.commit()

    async def obtenir(self, identifiant: str, demandeur: str) -> Rapport | None:
        """Retourne un job de ce demandeur, ou None."""
        if not self._pret:
            return None
        return await asyncio.to_thread(self._lire, identifiant, demandeur)

    def _lire(self, identifiant: str, demandeur: str) -> Rapport | None:
        """Lit un job (appelé dans un thread)."""
        with closing(self._connexion()) as connexion:
            ligne = connexion.execute(
                "SELECT * FROM rapports WHERE id = ? AND demandeur = ?", (identifiant, demandeur)
            ).fetchone()
        return self._ligne_en_rapport(ligne) if ligne else None

    async def lister(self, demandeur: str, limite: int = 50) -> list[Rapport]:
        """Liste les jobs d'un demandeur, les plus récents d'abord.

        Args:
            demandeur: Identifiant anonyme du demandeur.
            limite: Nombre maximal de jobs retournés.

        Returns:
            Les jobs, ``[]`` si le dépôt n'est pas prêt.
        """
        if not self._pret:
            return []
        return await asyncio.to_thread(self._lire_liste, demandeur, limite)

    def _lire_liste(self, demandeur: str, limite: int) -> list[Rapport]:
        """Lit les jobs d'un demandeur (appelé dans un thread)."""
        with closing(self._connexion()) as connexion:
            lignes = connexion.execute(
                "SELECT * FROM rapports WHERE demandeur = ? ORDER BY cree_le DESC LIMIT ?",
                (demandeur, limite),
            ).fetchall()
        return [self._ligne_en_rapport(ligne) for ligne in lignes]

    async def _majorer(self, identifiant: str, colonnes: dict[str, object]) -> None:
        """Applique une mise à jour partielle sur un job.

        Args:
            identifiant: Identifiant du job.
            colonnes: Colonnes à écrire, dont les NOMS sont des littéraux du module.
        """
        if not self._pret:
            return None
        colonnes["maj_le"] = datetime.now(UTC).isoformat()
        assignations = ", ".join(f"{cle} = ?" for cle in colonnes)
        valeurs = (*colonnes.values(), identifiant)
        async with self._verrou:
            await asyncio.to_thread(self._ecrire, assignations, valeurs)
        return None

    def _ecrire(self, assignations: str, valeurs: tuple) -> None:
        """Écrit une mise à jour (appelé dans un thread).

        ``assignations`` est construit à partir de noms de colonnes **littéraux** du
        module, jamais d'une donnée client ; les valeurs restent paramétrées.
        """
        with closing(self._connexion()) as connexion:
            connexion.execute(f"UPDATE rapports SET {assignations} WHERE id = ?", valeurs)
            connexion.commit()

    async def avancer(self, identifiant: str, faites: int, total: int) -> None:
        """Enregistre la progression d'un job."""
        return await self._majorer(
            identifiant,
            {
                "etat": EtatRapport.EN_COURS.value,
                "sections_faites": faites,
                "sections_total": total,
            },
        )

    async def terminer(self, identifiant: str, markdown: str) -> None:
        """Marque un job terminé et conserve son rendu Markdown."""
        return await self._majorer(
            identifiant, {"etat": EtatRapport.TERMINE.value, "markdown": markdown}
        )

    async def echouer(self, identifiant: str, erreur: str) -> None:
        """Marque un job échoué et conserve la raison."""
        return await self._majorer(
            identifiant, {"etat": EtatRapport.ECHOUE.value, "erreur": erreur}
        )

    async def reprendre_orphelins(self) -> int:
        """Marque échoués les jobs restés inachevés après un redémarrage.

        Returns:
            Le nombre de jobs assainis.
        """
        if not self._pret:
            return 0
        async with self._verrou:
            return await asyncio.to_thread(self._assainir)

    def _assainir(self) -> int:
        """Assainit les jobs orphelins (appelé dans un thread)."""
        with closing(self._connexion()) as connexion:
            curseur = connexion.execute(
                "UPDATE rapports SET etat = ?, erreur = ?, maj_le = ? WHERE etat IN (?, ?)",
                (
                    EtatRapport.ECHOUE.value,
                    "Interrompu par un redémarrage du service.",
                    datetime.now(UTC).isoformat(),
                    EtatRapport.EN_COURS.value,
                    EtatRapport.EN_ATTENTE.value,
                ),
            )
            connexion.commit()
            nombre = curseur.rowcount
        if nombre:
            logger.info("rapports_orphelins_assainis", nombre=nombre)
        return nombre
