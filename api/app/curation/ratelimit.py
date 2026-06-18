"""Limiteur de tentatives de connexion (anti-brute-force, en mémoire).

La console est mono-réplica (Deployment ``Recreate``) : un compteur en mémoire par
IP suffit, sans dépendance à Redis. OWASP API2 (Broken Authentication) : après un
nombre d'échecs dans une fenêtre glissante, l'IP est temporairement bloquée.
"""

from __future__ import annotations

import time
from collections.abc import Callable


class LimiteurConnexion:
    """Compte les échecs de connexion par IP sur une fenêtre glissante."""

    def __init__(
        self,
        max_echecs: int = 5,
        fenetre_s: float = 300.0,
        horloge: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialise le limiteur.

        Args:
            max_echecs: Nombre d'échecs au-delà duquel l'IP est bloquée.
            fenetre_s: Durée (s) de la fenêtre glissante et du blocage effectif.
            horloge: Source de temps monotone (injectable pour les tests).
        """
        self._max = max_echecs
        self._fenetre = fenetre_s
        self._horloge = horloge
        self._echecs: dict[str, list[float]] = {}

    def _recents(self, ip: str, maintenant: float) -> list[float]:
        """Retourne les échecs encore dans la fenêtre, en purgeant les anciens."""
        recents = [t for t in self._echecs.get(ip, []) if maintenant - t < self._fenetre]
        if recents:
            self._echecs[ip] = recents
        else:
            self._echecs.pop(ip, None)
        return recents

    def bloque(self, ip: str) -> bool:
        """Indique si l'IP a atteint le seuil d'échecs (donc bloquée)."""
        return len(self._recents(ip, self._horloge())) >= self._max

    def echec(self, ip: str) -> None:
        """Enregistre un échec de connexion pour l'IP."""
        maintenant = self._horloge()
        recents = self._recents(ip, maintenant)
        recents.append(maintenant)
        self._echecs[ip] = recents

    def succes(self, ip: str) -> None:
        """Réinitialise le compteur d'échecs de l'IP (connexion réussie)."""
        self._echecs.pop(ip, None)
