"""Paramètres applicatifs modifiables à chaud (clé-valeur, SQLite partagé /data).

Permet à la **console** d'écrire un réglage (ex. l'adresse d'expédition des emails)
et à l'**API** de le lire à chaque usage, sans redéploiement ni redémarrage. Les deux
processus partagent le volume ``/data`` ; SQLite (mode WAL) gère l'accès concurrent.

Même pattern tolérant aux pannes que :mod:`app.core.sessions` : si la base ne peut
être ouverte, les lectures renvoient ``None`` (l'appelant retombe sur ses défauts).
"""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Clés connues (centralisées pour éviter les fautes de frappe).
CLE_EMAIL_EXPEDITEUR = "auth_email_from"
CLE_NOM_EXPEDITEUR = "auth_email_from_name"


def brouiller_email(email: str) -> str:
    """Masque une adresse email pour l'affichage (jamais en clair dans la console).

    ``waopron@openlabconsulting.com`` -> ``w•••••n@openlabconsulting.com``. Garde la
    première et la dernière lettre de la partie locale et le domaine, masque le reste.
    """
    email = (email or "").strip()
    if "@" not in email:
        return "•••" if email else ""
    local, domaine = email.split("@", 1)
    masque = local[:1] + "•" if len(local) <= 2 else local[0] + "•" * (len(local) - 2) + local[-1]
    return f"{masque}@{domaine}"


class ParametresStore:
    """Dépôt SQLite clé-valeur des paramètres modifiables à chaud."""

    _MIGRATIONS: tuple[str, ...] = (
        """
        CREATE TABLE IF NOT EXISTS parametres (
            cle    TEXT PRIMARY KEY,
            valeur TEXT NOT NULL,
            maj_le TEXT NOT NULL
        );
        """,
    )

    def __init__(self, chemin: Path) -> None:
        """Initialise le dépôt (fichier SQLite créé si besoin)."""
        self._chemin = chemin
        self._verrou = asyncio.Lock()
        self._pret = False

    @classmethod
    def from_settings(cls, settings: Settings) -> ParametresStore:
        """Construit un dépôt à partir des paramètres applicatifs."""
        return cls(Path(settings.parametres_db_path))

    @property
    def pret(self) -> bool:
        """Indique si le schéma a pu être initialisé."""
        return self._pret

    async def initialiser(self) -> None:
        """Crée/migre le schéma. Tolérant aux pannes : ne lève jamais."""
        try:
            await asyncio.to_thread(self._migrer)
            self._pret = True
            logger.info("parametres_prets", chemin=str(self._chemin))
        except (sqlite3.Error, OSError) as exc:
            self._pret = False
            logger.warning("parametres_init_echoue", chemin=str(self._chemin), error=str(exc))

    async def obtenir(self, cle: str) -> str | None:
        """Retourne la valeur d'un paramètre, ou None (absent / base indisponible)."""
        try:
            return await asyncio.to_thread(self._obtenir, cle)
        except sqlite3.Error:
            return None

    async def definir(self, cle: str, valeur: str) -> None:
        """Écrit (ou écrase) un paramètre."""
        async with self._verrou:
            await asyncio.to_thread(self._definir, cle, valeur)

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

    def _obtenir(self, cle: str) -> str | None:
        with closing(self._connexion()) as conn:
            row = conn.execute("SELECT valeur FROM parametres WHERE cle = ?", (cle,)).fetchone()
        return str(row["valeur"]) if row is not None else None

    def _definir(self, cle: str, valeur: str) -> None:
        with closing(self._connexion()) as conn:
            conn.execute(
                "INSERT INTO parametres (cle, valeur, maj_le) VALUES (?, ?, ?) "
                "ON CONFLICT(cle) DO UPDATE SET valeur = excluded.valeur, maj_le = excluded.maj_le",
                (cle, valeur, datetime.now(UTC).isoformat()),
            )
            conn.commit()
