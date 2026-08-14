"""Tests de la file d'attente à position visible (C4, §9.2).

« Le pire scénario n'est pas la lenteur, c'est la lenteur muette. » Une attente
annoncée est tolérée ; une page blanche de quarante secondes est un échec public.
"""

from __future__ import annotations

import asyncio

import pytest

from app.application.file_attente import FileAttente, FileSaturee


async def test_sans_concurrence_on_passe_sans_annonce():
    """Le cas courant ne doit rien coûter : aucune annonce, aucune attente."""
    annonces: list[tuple[int, float]] = []
    file = FileAttente(places=1)

    async def _annoncer(position: int, attente_s: float) -> None:
        annonces.append((position, attente_s))

    async with file.place(_annoncer):
        pass

    assert annonces == []


async def test_un_second_demandeur_recoit_sa_position():
    """C est tout l objet : savoir qu on est deuxieme, pas regarder une page blanche."""
    annonces: list[tuple[int, float]] = []
    file = FileAttente(places=1, duree_moyenne_s=40.0)
    premier_tient = asyncio.Event()
    liberer = asyncio.Event()

    async def _annoncer(position: int, attente_s: float) -> None:
        annonces.append((position, attente_s))

    async def _occuper() -> None:
        async with file.place():
            premier_tient.set()
            await liberer.wait()

    async def _suivre() -> None:
        await premier_tient.wait()
        async with file.place(_annoncer):
            pass

    tache_premier = asyncio.create_task(_occuper())
    tache_second = asyncio.create_task(_suivre())
    await premier_tient.wait()
    await asyncio.sleep(0.05)  # laisse le second entrer dans la file

    assert annonces, "le demandeur en attente n a rien recu"
    assert annonces[0][0] == 1  # une personne devant
    assert annonces[0][1] == pytest.approx(40.0)

    liberer.set()
    await asyncio.gather(tache_premier, tache_second)


async def test_la_place_est_liberee_meme_en_cas_d_erreur():
    """Une exception ne doit pas condamner la place : le suivant attendrait a jamais."""
    file = FileAttente(places=1)
    with pytest.raises(RuntimeError):
        async with file.place():
            raise RuntimeError("la generation a echoue")
    async with file.place():
        pass  # la place est de nouveau prenable
    assert file.occupees == 0


async def test_une_annulation_libere_aussi_la_place():
    """Un client qui raccroche est le cas le plus courant, pas le plus rare."""
    file = FileAttente(places=1)
    demarre = asyncio.Event()

    async def _occuper() -> None:
        async with file.place():
            demarre.set()
            await asyncio.sleep(10)

    tache = asyncio.create_task(_occuper())
    await demarre.wait()
    tache.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tache
    assert file.occupees == 0


async def test_au_dela_de_l_attente_maximale_on_refuse_franchement():
    """Accumuler indefiniment produirait un 524 : mieux vaut un refus lisible."""
    file = FileAttente(places=1, attente_max=1)
    demarre = asyncio.Event()
    liberer = asyncio.Event()

    async def _occuper() -> None:
        async with file.place():
            demarre.set()
            await liberer.wait()

    async def _attendre() -> None:
        async with file.place():
            pass

    tache_premier = asyncio.create_task(_occuper())
    await demarre.wait()
    tache_attente = asyncio.create_task(_attendre())
    await asyncio.sleep(0.05)

    with pytest.raises(FileSaturee):
        async with file.place():
            pass

    liberer.set()
    await asyncio.gather(tache_premier, tache_attente)


async def test_plusieurs_places_servent_en_parallele():
    """Sur GPU, plusieurs generations tiennent : le plafond est un reglage."""
    file = FileAttente(places=2)
    simultanees = 0
    maximum = 0

    async def _travailler() -> None:
        nonlocal simultanees, maximum
        async with file.place():
            simultanees += 1
            maximum = max(maximum, simultanees)
            await asyncio.sleep(0.05)
            simultanees -= 1

    await asyncio.gather(*(_travailler() for _ in range(4)))
    assert maximum == 2


async def test_l_attente_estimee_croit_avec_la_position():
    """Une estimation grossiere vaut mieux qu aucune : le lecteur decide d attendre."""
    file = FileAttente(places=1, duree_moyenne_s=30.0)
    assert file.attente_estimee(1) == pytest.approx(30.0)
    assert file.attente_estimee(3) == pytest.approx(90.0)
    assert file.attente_estimee(0) == 0.0


async def test_le_message_d_attente_est_lisible_par_un_producteur():
    """Il sera lu par un producteur, pas par un ingenieur."""
    from app.application.file_attente import message_attente

    assert "1" in message_attente(1, 40.0)
    minuscules = message_attente(2, 80.0).lower()
    assert "position" in minuscules or "avant vous" in minuscules
    assert "minute" in minuscules or "seconde" in minuscules


def test_l_attente_longue_est_annoncee_en_minutes():
    """« 180 secondes » ne se lit pas ; « 3 minutes » se decide."""
    from app.application.file_attente import message_attente

    assert "minutes" in message_attente(3, 180.0)
    assert "secondes" in message_attente(1, 40.0)


async def test_la_file_compte_ceux_qui_attendent():
    """Le compteur sert au diagnostic autant qu a la position annoncee."""
    file = FileAttente(places=1)
    demarre = asyncio.Event()
    liberer = asyncio.Event()

    async def _occuper() -> None:
        async with file.place():
            demarre.set()
            await liberer.wait()

    async def _attendre() -> None:
        async with file.place():
            pass

    tache_premier = asyncio.create_task(_occuper())
    await demarre.wait()
    assert file.en_attente == 0
    assert file.position() == 1

    tache_second = asyncio.create_task(_attendre())
    await asyncio.sleep(0.05)
    assert file.en_attente == 1
    assert file.position() == 2  # le troisieme arrivant serait en deuxieme position

    liberer.set()
    await asyncio.gather(tache_premier, tache_second)
    assert file.en_attente == 0
    assert file.position() == 0
