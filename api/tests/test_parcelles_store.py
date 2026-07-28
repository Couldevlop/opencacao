"""Tests du dépôt SQLite des parcelles et de leurs captures."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core.parcelles_store import ParcelleStore
from app.models.parcelle import (
    Capture,
    Coordonnee,
    Geometrie,
    Image,
    Modalite,
    MotifRecevabilite,
    Recevabilite,
    SourceGeometrie,
)

DEVICE = "appareil-a"
AUTRE_DEVICE = "appareil-b"


@pytest.fixture
async def store(tmp_path: Path) -> ParcelleStore:
    depot = ParcelleStore(tmp_path / "parcelles.db", captures_retention_jours=90)
    await depot.initialiser()
    return depot


def _recevabilite() -> Recevabilite:
    return Recevabilite(
        recevable=True,
        motif=MotifRecevabilite.OK,
        conseil="Image exploitable.",
        score_nettete=400.0,
    )


async def test_initialiser_rend_le_depot_pret(store: ParcelleStore):
    assert store.pret is True


async def test_initialisation_tolere_un_chemin_inaccessible(tmp_path: Path):
    """Si /data est cassé, le dépôt n'est pas prêt mais ne lève jamais."""
    impasse = tmp_path / "fichier"
    impasse.write_text("je ne suis pas un dossier", encoding="utf-8")
    depot = ParcelleStore(impasse / "parcelles.db")
    await depot.initialiser()
    assert depot.pret is False


async def test_creer_puis_relire_une_parcelle(store: ParcelleStore):
    parcelle = await store.creer_parcelle(DEVICE, "Bloc Est", "Daloa", "Daloa")
    relue = await store.obtenir_parcelle(parcelle.identifiant, DEVICE)
    assert relue is not None
    assert relue.nom == "Bloc Est"
    assert relue.localite == "Daloa"


async def test_un_autre_appareil_ne_voit_pas_la_parcelle(store: ParcelleStore):
    parcelle = await store.creer_parcelle(DEVICE, "Bloc Est", "Daloa", "Daloa")
    assert await store.obtenir_parcelle(parcelle.identifiant, AUTRE_DEVICE) is None


async def test_lister_ne_rend_que_les_parcelles_de_l_appareil(store: ParcelleStore):
    await store.creer_parcelle(DEVICE, "Bloc Est", "Daloa", "Daloa")
    await store.creer_parcelle(AUTRE_DEVICE, "Bloc Ouest", "Soubré", "Soubré")
    assert [p.nom for p in await store.lister_parcelles(DEVICE)] == ["Bloc Est"]


async def test_enregistrer_une_geometrie_conserve_la_superficie(store: ParcelleStore):
    parcelle = await store.creer_parcelle(DEVICE, "Bloc Est", "Daloa", "Daloa")
    cote = 0.000899
    points = tuple(
        Coordonnee(latitude=a, longitude=b)
        for a, b in [
            (6.85, -5.28),
            (6.85, -5.28 + cote),
            (6.85 + cote, -5.28 + cote),
            (6.85 + cote, -5.28),
        ]
    )
    geometrie = Geometrie.depuis_points(points, SourceGeometrie.PARCOURS_GPS)
    maj = await store.enregistrer_geometrie(parcelle.identifiant, DEVICE, geometrie)
    assert maj is not None
    assert maj.geometrie is not None
    assert maj.geometrie.superficie_ha == pytest.approx(1.0, abs=0.05)
    assert len(maj.geometrie.points) == 4


async def test_enregistrer_une_geometrie_sur_une_parcelle_absente_rend_none(
    store: ParcelleStore,
):
    geometrie = Geometrie.depuis_points(
        (Coordonnee(latitude=6.85, longitude=-5.28),), SourceGeometrie.SAISIE_MANUELLE
    )
    assert await store.enregistrer_geometrie("inexistante", DEVICE, geometrie) is None


async def test_enregistrer_puis_relire_une_capture(store: ParcelleStore):
    parcelle = await store.creer_parcelle(DEVICE, "Bloc Est", "Daloa", "Daloa")
    capture = Capture(
        identifiant="cap-1",
        parcelle=parcelle.identifiant,
        proprietaire=DEVICE,
        modalite=Modalite.PHOTOS,
        cree_le=datetime.now(UTC),
        images=(
            Image(
                empreinte_sha256="a" * 64,
                largeur=1024,
                hauteur=768,
                recevabilite=_recevabilite(),
                coordonnee=Coordonnee(latitude=6.85, longitude=-5.28),
            ),
        ),
    )
    await store.enregistrer_capture(capture)
    relue = await store.obtenir_capture("cap-1", DEVICE)
    assert relue is not None
    assert relue.modalite is Modalite.PHOTOS
    assert len(relue.images) == 1
    assert relue.images[0].empreinte_sha256 == "a" * 64
    assert relue.images[0].recevabilite.motif is MotifRecevabilite.OK


async def test_capture_avec_trace_conserve_l_ordre_des_points(store: ParcelleStore):
    parcelle = await store.creer_parcelle(DEVICE, "Bloc Est", "Daloa", "Daloa")
    trace = tuple(Coordonnee(latitude=6.85 + i * 0.001, longitude=-5.28) for i in range(5))
    capture = Capture(
        identifiant="cap-2",
        parcelle=parcelle.identifiant,
        proprietaire=DEVICE,
        modalite=Modalite.PARCOURS,
        cree_le=datetime.now(UTC),
        trace=trace,
    )
    await store.enregistrer_capture(capture)
    relue = await store.obtenir_capture("cap-2", DEVICE)
    assert relue is not None
    assert [round(p.latitude, 3) for p in relue.trace] == [6.850, 6.851, 6.852, 6.853, 6.854]


async def test_lister_les_captures_d_une_parcelle(store: ParcelleStore):
    parcelle = await store.creer_parcelle(DEVICE, "Bloc Est", "Daloa", "Daloa")
    for indice in range(3):
        await store.enregistrer_capture(
            Capture(
                identifiant=f"cap-{indice}",
                parcelle=parcelle.identifiant,
                proprietaire=DEVICE,
                modalite=Modalite.PHOTOS,
                cree_le=datetime.now(UTC),
                images=(
                    Image(
                        empreinte_sha256=str(indice) * 64,
                        largeur=1024,
                        hauteur=768,
                        recevabilite=_recevabilite(),
                    ),
                ),
            )
        )
    assert len(await store.lister_captures(parcelle.identifiant, DEVICE)) == 3


async def test_purger_rend_les_empreintes_des_captures_expirees(store: ParcelleStore):
    parcelle = await store.creer_parcelle(DEVICE, "Bloc Est", "Daloa", "Daloa")
    ancienne = datetime.now(UTC) - timedelta(days=200)
    await store.enregistrer_capture(
        Capture(
            identifiant="cap-vieille",
            parcelle=parcelle.identifiant,
            proprietaire=DEVICE,
            modalite=Modalite.PHOTOS,
            cree_le=ancienne,
            images=(
                Image(
                    empreinte_sha256="f" * 64,
                    largeur=1024,
                    hauteur=768,
                    recevabilite=_recevabilite(),
                ),
            ),
        )
    )
    empreintes = await store.purger_captures(datetime.now(UTC) - timedelta(days=90))
    assert empreintes == ["f" * 64]
    assert await store.obtenir_capture("cap-vieille", DEVICE) is None


async def test_les_operations_sur_un_depot_non_pret_ne_levent_pas(tmp_path: Path):
    depot = ParcelleStore(tmp_path / "jamais-initialise.db")
    assert await depot.lister_parcelles(DEVICE) == []
    assert await depot.obtenir_parcelle("x", DEVICE) is None
