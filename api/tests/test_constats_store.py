"""Tests de la persistance des constats visuels."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.parcelles_store import ParcelleStore
from app.models.constat import Constat, EtatRevue, NiveauConfiance, Observation, Organe

DEVICE = "appareil-a"


@pytest.fixture
async def store(tmp_path: Path) -> ParcelleStore:
    depot = ParcelleStore(tmp_path / "parcelles.db")
    await depot.initialiser()
    return depot


def _constat(identifiant: str = "c1", **surcharges) -> Constat:
    defauts = {
        "identifiant": identifiant,
        "capture": "cap1",
        "parcelle": "p1",
        "proprietaire": DEVICE,
        "observations": (
            Observation(
                organe=Organe.CABOSSE,
                description="Taches brunes sur un tiers de la cabosse.",
                confiance=NiveauConfiance.MOYENNE,
                empreinte_image="a" * 64,
            ),
        ),
        "texte": "Vos cabosses présentent des taches. Montrez-les à votre agent ANADER.",
        "confiance": NiveauConfiance.MOYENNE,
        "cree_le": datetime.now(UTC),
        "facteurs_contexte": ("Parcelle située à Daloa.",),
    }
    return Constat(**{**defauts, **surcharges})


async def test_enregistrer_puis_relire_un_constat(store: ParcelleStore):
    await store.enregistrer_constat(_constat())
    relu = await store.obtenir_constat("c1", DEVICE)
    assert relu is not None
    assert relu.texte.startswith("Vos cabosses")
    assert relu.observations[0].organe is Organe.CABOSSE
    assert relu.facteurs_contexte == ("Parcelle située à Daloa.",)


async def test_un_autre_appareil_ne_voit_pas_le_constat(store: ParcelleStore):
    await store.enregistrer_constat(_constat())
    assert await store.obtenir_constat("c1", "appareil-b") is None


async def test_un_constat_nait_en_attente_de_revue(store: ParcelleStore):
    await store.enregistrer_constat(_constat())
    en_attente = await store.lister_constats_en_attente()
    assert [c.identifiant for c in en_attente] == ["c1"]


async def test_reviser_confirme_sort_le_constat_de_la_file(store: ParcelleStore):
    await store.enregistrer_constat(_constat())
    revu = await store.reviser_constat("c1", EtatRevue.CONFIRME, "agent-anader-7", "")
    assert revu is not None
    assert revu.etat_revue is EtatRevue.CONFIRME
    assert revu.revu_par == "agent-anader-7"
    assert await store.lister_constats_en_attente() == []


async def test_reviser_corrige_conserve_la_correction(store: ParcelleStore):
    """C'est cette correction qui alimentera le jeu de donnees ivoirien (etage 6)."""
    await store.enregistrer_constat(_constat())
    revu = await store.reviser_constat(
        "c1", EtatRevue.CORRIGE, "agent-anader-7", "Ombrage insuffisant, pas une atteinte."
    )
    assert revu is not None
    assert revu.correction.startswith("Ombrage")


async def test_reviser_un_constat_absent_rend_none(store: ParcelleStore):
    assert await store.reviser_constat("inconnu", EtatRevue.CONFIRME, "x", "") is None


async def test_la_file_est_bornee(store: ParcelleStore):
    for indice in range(5):
        await store.enregistrer_constat(_constat(identifiant=f"c{indice}"))
    assert len(await store.lister_constats_en_attente(limite=3)) == 3


async def test_retrouver_le_constat_d_une_capture(store: ParcelleStore):
    """Support de l idempotence : une capture deja analysee se relit."""
    await store.enregistrer_constat(_constat())
    relu = await store.obtenir_constat_par_capture("cap1", DEVICE)
    assert relu is not None
    assert relu.identifiant == "c1"


async def test_le_constat_d_une_capture_reste_cloisonne_par_appareil(store: ParcelleStore):
    await store.enregistrer_constat(_constat())
    assert await store.obtenir_constat_par_capture("cap1", "appareil-b") is None


async def test_une_capture_sans_constat_rend_none(store: ParcelleStore):
    assert await store.obtenir_constat_par_capture("jamais-analysee", DEVICE) is None


async def test_les_operations_sur_un_depot_non_pret_ne_levent_pas(tmp_path: Path):
    depot = ParcelleStore(tmp_path / "jamais.db")
    assert await depot.obtenir_constat("c1", DEVICE) is None
    assert await depot.obtenir_constat_par_capture("cap1", DEVICE) is None
    assert await depot.lister_constats_en_attente() == []
    assert await depot.reviser_constat("c1", EtatRevue.CONFIRME, "x", "") is None
    assert await depot.enregistrer_constat(_constat()) is not None
