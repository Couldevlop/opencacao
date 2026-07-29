"""File d'attente à position visible (V3, chantier C4, spec §9.2).

**Le pire scénario n'est pas la lenteur, c'est la lenteur muette.** Une génération sur
CPU prend des dizaines de secondes ; une étude en prend plusieurs minutes. Quand deux
demandes se présentent en même temps, la seconde doit savoir qu'elle est deuxième — une
attente annoncée est tolérée, une page blanche de quarante secondes est un échec public.

Ce module remplit deux rôles indissociables :

* **Borner la concurrence.** Le profil CPU ne tient pas deux générations en parallèle :
  les laisser se disputer les cœurs les ralentit toutes les deux. Une seule place par
  défaut, réglable à plusieurs sur GPU.
* **Refuser franchement au-delà d'un certain point.** Accumuler les demandes sans
  limite produirait exactement le 524 que le projet a déjà rencontré : le client
  attendrait derrière un edge qui a déjà coupé. Passé le plafond, on le dit.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from app.core.logging import get_logger

logger = get_logger(__name__)

# Durée moyenne d'une génération, servant à estimer l'attente. Grossière et assumée :
# une estimation approximative permet au lecteur de décider s'il attend, une absence
# d'estimation ne permet rien.
DUREE_MOYENNE_S = 40.0

# Places servies en parallèle. Une seule sur CPU : deux générations concurrentes se
# disputent les cœurs et finissent plus tard toutes les deux.
PLACES_DEFAUT = 1

# Au-delà, on refuse. Le plafond n'est pas une capacité, c'est le point où attendre
# n'a plus de sens : la dernière demande de la file dépasserait tout time-out utile.
ATTENTE_MAX_DEFAUT = 8

AnnonceRappel = Callable[[int, float], Awaitable[None]]


class FileSaturee(Exception):
    """La file est pleine : mieux vaut un refus lisible qu'une attente sans issue."""


def message_attente(position: int, attente_s: float) -> str:
    """Rédige l'annonce d'attente destinée au demandeur.

    Args:
        position: Nombre de demandes servies avant celle-ci.
        attente_s: Attente estimée, en secondes.

    Returns:
        Une phrase lisible par un producteur, pas par un ingénieur.
    """
    if attente_s >= 90:
        delai = f"environ {round(attente_s / 60)} minutes"
    else:
        delai = f"environ {round(attente_s)} secondes"
    if position == 1:
        return f"Une demande est en cours avant la vôtre. Position 1, {delai} d'attente."
    return f"Position {position} dans la file d'attente, {delai} d'attente."


class FileAttente:
    """Borne les générations simultanées et annonce sa position à qui attend."""

    def __init__(
        self,
        places: int = PLACES_DEFAUT,
        attente_max: int = ATTENTE_MAX_DEFAUT,
        duree_moyenne_s: float = DUREE_MOYENNE_S,
    ) -> None:
        """Initialise la file.

        Args:
            places: Nombre de générations servies en parallèle.
            attente_max: Nombre de demandes tolérées en attente au-delà des places.
            duree_moyenne_s: Durée moyenne d'une génération, pour l'estimation.
        """
        self._places = asyncio.Semaphore(places)
        self._total_places = places
        self._occupees = 0
        self._en_attente = 0
        self._attente_max = attente_max
        self._duree_moyenne_s = duree_moyenne_s

    @property
    def occupees(self) -> int:
        """Nombre de places actuellement tenues."""
        return self._occupees

    @property
    def en_attente(self) -> int:
        """Nombre de demandes en attente d'une place."""
        return self._en_attente

    def attente_estimee(self, position: int) -> float:
        """Estime l'attente d'une position donnée, en secondes.

        Args:
            position: Nombre de demandes servies avant celle-ci.

        Returns:
            L'attente estimée, ``0.0`` si la place est disponible immédiatement.
        """
        return max(position, 0) * self._duree_moyenne_s

    def position(self) -> int:
        """Retourne le nombre de demandes à servir avant une nouvelle arrivante.

        À consulter **avant** ``place()`` : c'est ce qui permet d'annoncer l'attente
        au demandeur pendant qu'il patiente, et non après. La valeur est une
        estimation — une place peut se libérer entre-temps — et c'est assumé : mieux
        vaut une position approximative annoncée tout de suite qu'une position exacte
        annoncée trop tard.

        Returns:
            ``0`` si une place est libre, sinon le rang dans la file d'attente.
        """
        if self._occupees < self._total_places:
            return 0
        return self._en_attente + 1

    def saturee(self) -> bool:
        """Indique si la file a atteint son plafond d'attente."""
        return self._en_attente >= self._attente_max

    @asynccontextmanager
    async def place(self, annoncer: AnnonceRappel | None = None) -> AsyncIterator[None]:
        """Attend puis tient une place.

        Args:
            annoncer: Rappel recevant ``(position, attente estimée)`` si la demande
                doit attendre. Utile quand l'appelant ne peut pas annoncer lui-même ;
                un flux SSE préférera consulter ``position()`` puis émettre son
                événement, pour que l'annonce parte avant l'attente.

        Yields:
            Rien : le bloc s'exécute une fois la place obtenue.

        Raises:
            FileSaturee: La file dépasse déjà son plafond d'attente.
        """
        if self.saturee():
            logger.warning("file_saturee", en_attente=self._en_attente)
            raise FileSaturee(self._attente_max)

        immediate = self._occupees < self._total_places
        if not immediate:
            self._en_attente += 1
            if annoncer is not None:
                await annoncer(self._en_attente, self.attente_estimee(self._en_attente))
        try:
            await self._places.acquire()
        finally:
            # Décrémenté DANS un finally : une annulation pendant l'attente ne doit
            # pas laisser la file croire qu'un demandeur patiente encore.
            if not immediate:
                self._en_attente -= 1
        self._occupees += 1
        try:
            yield
        finally:
            # Toujours rendue : une exception ou une annulation qui garderait la place
            # condamnerait tous les suivants à attendre indéfiniment.
            self._occupees -= 1
            self._places.release()
