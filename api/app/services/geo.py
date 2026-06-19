"""Géolocalisation IP → pays, EN LOCAL (base GeoLite2 MaxMind).

Sert à l'analytique des visites. **Souverain** (aucun appel externe) et **respectueux
de la vie privée** : on résout le pays à la volée et on ne conserve **que** le code
pays — jamais l'IP.

Dégradé proprement : si la lib ou la base sont absentes, ``pays()`` renvoie "" (le
service n'est jamais interrompu). La base ``GeoLite2-Country.mmdb`` se dépose sur le
nœud (comme les modèles) ; chemin via ``GEOIP_DB_PATH``.
"""

from __future__ import annotations

import os
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger(__name__)


class GeoLocalisateur:
    """Résout une IP en code pays ISO (ex. ``CI``, ``FR``) via GeoLite2."""

    def __init__(self, db_path: Path) -> None:
        """Initialise le localisateur.

        Args:
            db_path: Chemin de la base GeoLite2-Country (.mmdb).
        """
        self._db_path = db_path
        self._reader = None
        self._tente = False

    @classmethod
    def from_env(cls) -> GeoLocalisateur:
        """Construit le localisateur depuis l'environnement (``GEOIP_DB_PATH``)."""
        return cls(Path(os.environ.get("GEOIP_DB_PATH", "/models/GeoLite2-Country.mmdb")))

    def _lecteur(self):
        """Ouvre la base à la demande (une seule tentative). None si indisponible."""
        if self._tente:
            return self._reader
        self._tente = True
        try:
            import maxminddb

            if self._db_path.exists():
                self._reader = maxminddb.open_database(str(self._db_path))
                logger.info("geoip_charge", chemin=str(self._db_path))
            else:
                logger.warning("geoip_base_absente", chemin=str(self._db_path))
        except Exception as exc:  # noqa: BLE001 - dégradation propre, jamais bloquant
            logger.warning("geoip_indisponible", error=str(exc))
        return self._reader

    def _enregistrement(self, ip: str) -> dict:
        """Retourne l'enregistrement GeoLite2 de l'IP, ou {} si indisponible/invalide."""
        lecteur = self._lecteur()
        if lecteur is None or not ip:
            return {}
        try:
            enr = lecteur.get(ip)
        except (ValueError, TypeError):
            return {}  # IP invalide (ex. "testclient", IP interne)
        return enr if isinstance(enr, dict) else {}

    def pays(self, ip: str) -> str:
        """Retourne le code pays ISO de l'IP, ou "" si inconnu/indisponible."""
        return str(self._enregistrement(ip).get("country", {}).get("iso_code", "") or "")

    def localiser(self, ip: str) -> tuple[str, str]:
        """Retourne (code pays ISO, code continent) de l'IP, "" si inconnu.

        Args:
            ip: Adresse IP du visiteur (jamais stockée).

        Returns:
            Un couple ``(pays, continent)`` — codes ISO pays + continent (AF, EU…).
        """
        enr = self._enregistrement(ip)
        pays = str(enr.get("country", {}).get("iso_code", "") or "")
        continent = str(enr.get("continent", {}).get("code", "") or "")
        return pays, continent
