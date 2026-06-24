"""Stockage durable de l'authentification par lien magique (D2, SQLite stdlib).

Même choix de conception que :mod:`app.core.sessions` — ``sqlite3`` de la bibliothèque
standard, migrations via ``PRAGMA user_version``, accès asynchrone par
``asyncio.to_thread``, initialisation tolérante aux pannes. Deux tables :

* ``comptes`` : email vérifié → identifiant de compte opaque et stable.
* ``liens``   : jetons de lien magique (stockés **hachés**), à usage unique et expirant.

Les jetons ne sont jamais stockés en clair : on conserve leur SHA-256, de sorte qu'une
fuite de la base ne révèle aucun lien exploitable.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def hacher_token(token: str) -> str:
    """Retourne le SHA-256 hexadécimal d'un jeton (jamais stocké en clair)."""
    return hashlib.sha256(token.encode()).hexdigest()


class AuthStore:
    """Dépôt SQLite des comptes et des liens magiques."""

    _MIGRATIONS: tuple[str, ...] = (
        """
        CREATE TABLE IF NOT EXISTS comptes (
            email      TEXT PRIMARY KEY,
            account_id TEXT NOT NULL UNIQUE,
            cree_le    TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS liens (
            token_hash TEXT PRIMARY KEY,
            email      TEXT NOT NULL,
            cree_le    TEXT NOT NULL,
            expire_le  TEXT NOT NULL,
            utilise    INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_liens_expire ON liens(expire_le);
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
    def from_settings(cls, settings: Settings) -> AuthStore:
        """Construit un dépôt à partir des paramètres applicatifs."""
        return cls(Path(settings.auth_db_path))

    @property
    def pret(self) -> bool:
        """Indique si le schéma a pu être initialisé."""
        return self._pret

    async def initialiser(self) -> None:
        """Crée/migre le schéma. Tolérant aux pannes : ne lève jamais au démarrage."""
        try:
            await asyncio.to_thread(self._migrer)
            self._pret = True
            logger.info("auth_prete", chemin=str(self._chemin))
        except (sqlite3.Error, OSError) as exc:
            self._pret = False
            logger.warning("auth_init_echouee", chemin=str(self._chemin), error=str(exc))

    async def creer_lien(self, email: str, token_hash: str, expire_le: datetime) -> None:
        """Enregistre un lien magique (jeton haché) pour un email."""
        async with self._verrou:
            await asyncio.to_thread(self._creer_lien, email, token_hash, expire_le)

    async def consommer_lien(self, token_hash: str) -> str | None:
        """Valide et consomme un lien (usage unique).

        Returns:
            L'email associé si le lien existe, n'est pas expiré et n'a pas déjà servi
            (il est alors marqué utilisé) ; sinon ``None``.
        """
        async with self._verrou:
            return await asyncio.to_thread(self._consommer_lien, token_hash)

    async def compte_pour(self, email: str) -> str:
        """Retourne l'identifiant de compte de l'email (créé s'il n'existe pas)."""
        async with self._verrou:
            return await asyncio.to_thread(self._compte_pour, email)

    async def purger_liens_expires(self) -> int:
        """Supprime les liens expirés ou déjà utilisés. Renvoie le nombre supprimé."""
        async with self._verrou:
            return await asyncio.to_thread(self._purger_liens_expires)

    # ----------------------------------------------------- implémentation SQL

    def _connexion(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._chemin), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _migrer(self) -> None:
        self._chemin.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connexion()) as conn:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            for indice in range(version, len(self._MIGRATIONS)):
                conn.executescript(self._MIGRATIONS[indice])
                conn.execute(f"PRAGMA user_version = {indice + 1}")
            conn.commit()

    def _creer_lien(self, email: str, token_hash: str, expire_le: datetime) -> None:
        maintenant = datetime.now(UTC).isoformat()
        with closing(self._connexion()) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO liens (token_hash, email, cree_le, expire_le, utilise) "
                "VALUES (?, ?, ?, ?, 0)",
                (token_hash, email, maintenant, expire_le.isoformat()),
            )
            conn.commit()

    def _consommer_lien(self, token_hash: str) -> str | None:
        maintenant = datetime.now(UTC).isoformat()
        with closing(self._connexion()) as conn:
            row = conn.execute(
                "SELECT email, expire_le, utilise FROM liens WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
            if row is None or int(row["utilise"]) == 1 or row["expire_le"] < maintenant:
                return None
            conn.execute("UPDATE liens SET utilise = 1 WHERE token_hash = ?", (token_hash,))
            conn.commit()
            return str(row["email"])

    def _compte_pour(self, email: str) -> str:
        with closing(self._connexion()) as conn:
            row = conn.execute(
                "SELECT account_id FROM comptes WHERE email = ?", (email,)
            ).fetchone()
            if row is not None:
                return str(row["account_id"])
            account_id = "acct_" + secrets.token_urlsafe(24)
            conn.execute(
                "INSERT INTO comptes (email, account_id, cree_le) VALUES (?, ?, ?)",
                (email, account_id, datetime.now(UTC).isoformat()),
            )
            conn.commit()
            return account_id

    def _purger_liens_expires(self) -> int:
        maintenant = datetime.now(UTC).isoformat()
        with closing(self._connexion()) as conn:
            cur = conn.execute(
                "DELETE FROM liens WHERE utilise = 1 OR expire_le < ?", (maintenant,)
            )
            conn.commit()
            return int(cur.rowcount)
