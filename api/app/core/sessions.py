"""Stockage durable des sessions de conversation (SQLite, bibliothèque standard).

Choix de conception (cadrage V2, sprint 1) : la persistance s'appuie sur le module
``sqlite3`` de la bibliothèque standard Python — **aucune dépendance hors spec §2.1**.
Le fichier de base vit sur le volume persistant ``/data`` (comme l'index RAG et le
journal). Les migrations de schéma sont versionnées via ``PRAGMA user_version``
(pas d'outil externe type Alembic).

Accès asynchrone : ``sqlite3`` est synchrone, donc chaque opération est déportée
dans un thread via ``asyncio.to_thread`` pour ne jamais bloquer la boucle d'événements.
Les écritures sont sérialisées par un verrou applicatif ; le mode WAL autorise les
lectures concurrentes.

À l'image du cache et du journal, l'initialisation est **tolérante aux pannes** :
si le fichier ne peut être ouvert, le service démarre quand même (les sessions sont
alors marquées indisponibles), afin de ne jamais rendre l'API inopérante.
"""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.core.config import Settings
from app.core.logging import get_logger
from app.models.domain import Canal, Langue
from app.models.session import (
    TITRE_PAR_DEFAUT,
    ConversationMessage,
    Session,
    SessionAvecMessages,
)

logger = get_logger(__name__)


class SessionStore:
    """Dépôt SQLite des sessions de conversation et de leurs messages."""

    # Migrations ordonnées : l'indice (0-based) + 1 devient le ``user_version``.
    # Pour faire évoluer le schéma, AJOUTER une nouvelle entrée à la fin — ne jamais
    # modifier une migration déjà publiée.
    _MIGRATIONS: tuple[str, ...] = (
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id      TEXT PRIMARY KEY,
            titre   TEXT NOT NULL,
            langue  TEXT NOT NULL,
            canal   TEXT NOT NULL,
            cree_le TEXT NOT NULL,
            maj_le  TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
            id         TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            role       TEXT NOT NULL,
            contenu    TEXT NOT NULL,
            cree_le    TEXT NOT NULL,
            ordre      INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, ordre);
        CREATE INDEX IF NOT EXISTS idx_sessions_maj ON sessions(maj_le DESC);
        """,
    )

    def __init__(self, chemin: Path, max_messages: int = 200) -> None:
        """Initialise le dépôt.

        Args:
            chemin: Chemin du fichier SQLite (créé si besoin).
            max_messages: Plafond de messages par session (garde-fou anti-abus,
                appliqué par la couche service en V2).
        """
        self._chemin = chemin
        self._max_messages = max_messages
        self._verrou = asyncio.Lock()
        self._pret = False

    @classmethod
    def from_settings(cls, settings: Settings) -> SessionStore:
        """Construit un dépôt à partir des paramètres applicatifs."""
        return cls(Path(settings.sessions_db_path), max_messages=settings.sessions_max_messages)

    @property
    def pret(self) -> bool:
        """Indique si le schéma a pu être initialisé (sessions disponibles)."""
        return self._pret

    @property
    def max_messages(self) -> int:
        """Plafond de messages par session."""
        return self._max_messages

    async def initialiser(self) -> None:
        """Crée/migre le schéma. Tolérant aux pannes : ne lève jamais au démarrage."""
        try:
            await asyncio.to_thread(self._migrer)
            self._pret = True
            logger.info("sessions_pretes", chemin=str(self._chemin))
        except (sqlite3.Error, OSError) as exc:
            self._pret = False
            logger.warning("sessions_init_echouee", chemin=str(self._chemin), error=str(exc))

    # ------------------------------------------------------------------ CRUD

    async def creer_session(
        self,
        langue: Langue = Langue.FR,
        canal: Canal = Canal.WEB,
        titre: str = TITRE_PAR_DEFAUT,
    ) -> Session:
        """Crée une session vide et retourne ses métadonnées."""
        maintenant = datetime.now(UTC)
        session = Session(
            id=uuid4().hex,
            titre=titre,
            langue=langue,
            canal=canal,
            cree_le=maintenant,
            maj_le=maintenant,
        )
        async with self._verrou:
            await asyncio.to_thread(self._inserer_session, session)
        return session

    async def obtenir_session(self, session_id: str) -> Session | None:
        """Retourne les métadonnées d'une session, ou None si inconnue."""
        return await asyncio.to_thread(self._lire_session, session_id)

    async def obtenir_session_avec_messages(self, session_id: str) -> SessionAvecMessages | None:
        """Retourne une session et tous ses messages, ou None si inconnue."""
        session = await self.obtenir_session(session_id)
        if session is None:
            return None
        messages = await self.lister_messages(session_id)
        return SessionAvecMessages(session=session, messages=messages)

    async def lister_sessions(self, limite: int = 50, decalage: int = 0) -> list[Session]:
        """Liste les sessions, de la plus récemment active à la plus ancienne."""
        return await asyncio.to_thread(self._lister_sessions, limite, decalage)

    async def renommer_session(self, session_id: str, titre: str) -> bool:
        """Renomme une session. Retourne True si la session existait."""
        titre = titre.strip()[:200] or TITRE_PAR_DEFAUT
        async with self._verrou:
            return await asyncio.to_thread(self._renommer_session, session_id, titre)

    async def supprimer_session(self, session_id: str) -> bool:
        """Supprime une session et ses messages (cascade). True si elle existait."""
        async with self._verrou:
            return await asyncio.to_thread(self._supprimer_session, session_id)

    async def ajouter_message(
        self, session_id: str, role: str, content: str
    ) -> ConversationMessage | None:
        """Ajoute un message à une session et met à jour son horodatage.

        Returns:
            Le message créé, ou None si la session n'existe pas.
        """
        message = ConversationMessage(role=role, content=content, cree_le=datetime.now(UTC))
        async with self._verrou:
            ok = await asyncio.to_thread(self._inserer_message, session_id, message)
        return message if ok else None

    async def lister_messages(self, session_id: str) -> list[ConversationMessage]:
        """Retourne les messages d'une session, du plus ancien au plus récent."""
        return await asyncio.to_thread(self._lister_messages, session_id)

    async def compter_messages(self, session_id: str) -> int:
        """Compte les messages d'une session."""
        return await asyncio.to_thread(self._compter_messages, session_id)

    # ----------------------------------------------------- implémentation SQL

    def _connexion(self) -> sqlite3.Connection:
        """Ouvre une connexion configurée (WAL, clés étrangères, ligne-dict)."""
        conn = sqlite3.connect(str(self._chemin), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _migrer(self) -> None:
        """Applique les migrations manquantes en s'appuyant sur PRAGMA user_version."""
        self._chemin.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connexion()) as conn:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            for indice in range(version, len(self._MIGRATIONS)):
                conn.executescript(self._MIGRATIONS[indice])
                # user_version n'accepte pas de paramètre lié ; l'indice est un entier maîtrisé.
                conn.execute(f"PRAGMA user_version = {indice + 1}")
            conn.commit()

    def _inserer_session(self, session: Session) -> None:
        with closing(self._connexion()) as conn:
            conn.execute(
                "INSERT INTO sessions (id, titre, langue, canal, cree_le, maj_le) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    session.id,
                    session.titre,
                    session.langue.value,
                    session.canal.value,
                    session.cree_le.isoformat(),
                    session.maj_le.isoformat(),
                ),
            )
            conn.commit()

    def _lire_session(self, session_id: str) -> Session | None:
        with closing(self._connexion()) as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return _ligne_vers_session(row) if row is not None else None

    def _lister_sessions(self, limite: int, decalage: int) -> list[Session]:
        with closing(self._connexion()) as conn:
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY maj_le DESC LIMIT ? OFFSET ?",
                (limite, decalage),
            ).fetchall()
        return [_ligne_vers_session(row) for row in rows]

    def _renommer_session(self, session_id: str, titre: str) -> bool:
        with closing(self._connexion()) as conn:
            cur = conn.execute(
                "UPDATE sessions SET titre = ? WHERE id = ?", (titre, session_id)
            )
            conn.commit()
            return cur.rowcount > 0

    def _supprimer_session(self, session_id: str) -> bool:
        with closing(self._connexion()) as conn:
            cur = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            conn.commit()
            return cur.rowcount > 0

    def _inserer_message(self, session_id: str, message: ConversationMessage) -> bool:
        with closing(self._connexion()) as conn:
            existe = conn.execute(
                "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if existe is None:
                return False
            ordre = int(
                conn.execute(
                    "SELECT COALESCE(MAX(ordre), 0) + 1 FROM messages WHERE session_id = ?",
                    (session_id,),
                ).fetchone()[0]
            )
            horodatage = message.cree_le.isoformat()
            conn.execute(
                "INSERT INTO messages (id, session_id, role, contenu, cree_le, ordre) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (uuid4().hex, session_id, message.role, message.content, horodatage, ordre),
            )
            conn.execute(
                "UPDATE sessions SET maj_le = ? WHERE id = ?", (horodatage, session_id)
            )
            conn.commit()
            return True

    def _lister_messages(self, session_id: str) -> list[ConversationMessage]:
        with closing(self._connexion()) as conn:
            rows = conn.execute(
                "SELECT role, contenu, cree_le FROM messages WHERE session_id = ? ORDER BY ordre",
                (session_id,),
            ).fetchall()
        return [
            ConversationMessage(role=row["role"], content=row["contenu"], cree_le=row["cree_le"])
            for row in rows
        ]

    def _compter_messages(self, session_id: str) -> int:
        with closing(self._connexion()) as conn:
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,)
                ).fetchone()[0]
            )


def _ligne_vers_session(row: sqlite3.Row) -> Session:
    """Convertit une ligne SQLite en métadonnées de session (Pydantic coerce les ISO)."""
    return Session(
        id=row["id"],
        titre=row["titre"],
        langue=row["langue"],
        canal=row["canal"],
        cree_le=row["cree_le"],
        maj_le=row["maj_le"],
    )
