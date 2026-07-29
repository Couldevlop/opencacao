"""Tests de la persistance des jobs de rapport."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.rapports_store import EtatRapport, RapportStore

DEVICE = "appareil-a"


@pytest.fixture
async def store(tmp_path: Path) -> RapportStore:
    depot = RapportStore(tmp_path / "rapports.db")
    await depot.initialiser()
    return depot


async def test_un_job_nait_en_attente(store: RapportStore):
    rapport = await store.creer("etude_filiere", "le cacao", DEVICE)
    assert rapport.etat is EtatRapport.EN_ATTENTE
    assert rapport.sections_faites == 0


async def test_relire_un_job_par_son_identifiant(store: RapportStore):
    cree = await store.creer("etude_filiere", "le cacao", DEVICE)
    relu = await store.obtenir(cree.identifiant, DEVICE)
    assert relu is not None
    assert relu.sujet == "le cacao"


async def test_un_autre_appareil_ne_voit_pas_le_job(store: RapportStore):
    cree = await store.creer("etude_filiere", "le cacao", DEVICE)
    assert await store.obtenir(cree.identifiant, "appareil-b") is None


async def test_lister_ne_rend_que_les_jobs_du_demandeur(store: RapportStore):
    await store.creer("etude_filiere", "a", DEVICE)
    await store.creer("etude_filiere", "b", "appareil-b")
    assert [r.sujet for r in await store.lister(DEVICE)] == ["a"]


async def test_la_liste_est_bornee(store: RapportStore):
    for indice in range(5):
        await store.creer("etude_filiere", f"sujet {indice}", DEVICE)
    assert len(await store.lister(DEVICE, limite=3)) == 3


async def test_la_progression_est_persistee(store: RapportStore):
    cree = await store.creer("etude_filiere", "le cacao", DEVICE)
    await store.avancer(cree.identifiant, 2, 6)
    relu = await store.obtenir(cree.identifiant, DEVICE)
    assert relu is not None
    assert (relu.sections_faites, relu.sections_total) == (2, 6)
    assert relu.etat is EtatRapport.EN_COURS


async def test_terminer_conserve_le_markdown(store: RapportStore):
    cree = await store.creer("etude_filiere", "le cacao", DEVICE)
    await store.terminer(cree.identifiant, "# Étude")
    relu = await store.obtenir(cree.identifiant, DEVICE)
    assert relu is not None
    assert relu.etat is EtatRapport.TERMINE
    assert relu.markdown == "# Étude"


async def test_echouer_conserve_la_raison(store: RapportStore):
    cree = await store.creer("etude_filiere", "le cacao", DEVICE)
    await store.echouer(cree.identifiant, "inférence indisponible")
    relu = await store.obtenir(cree.identifiant, DEVICE)
    assert relu is not None
    assert relu.etat is EtatRapport.ECHOUE
    assert "indisponible" in relu.erreur


async def test_un_job_survit_a_un_redemarrage(tmp_path: Path):
    """Critere d acceptation : etat persiste, reprise ou echec propre."""
    chemin = tmp_path / "rapports.db"
    premier = RapportStore(chemin)
    await premier.initialiser()
    cree = await premier.creer("etude_filiere", "le cacao", DEVICE)
    await premier.avancer(cree.identifiant, 1, 6)

    second = RapportStore(chemin)
    await second.initialiser()
    relu = await second.obtenir(cree.identifiant, DEVICE)
    assert relu is not None
    assert relu.sections_faites == 1


async def test_les_jobs_inacheves_au_demarrage_sont_marques_echoues(tmp_path: Path):
    """Un job « en cours » apres redemarrage est orphelin : personne ne le reprendra,
    et le laisser ainsi ferait attendre un client indefiniment."""
    chemin = tmp_path / "rapports.db"
    premier = RapportStore(chemin)
    await premier.initialiser()
    en_cours = await premier.creer("etude_filiere", "le cacao", DEVICE)
    await premier.avancer(en_cours.identifiant, 1, 6)
    en_attente = await premier.creer("etude_filiere", "autre", DEVICE)

    second = RapportStore(chemin)
    await second.initialiser()
    assert await second.reprendre_orphelins() == 2
    for identifiant in (en_cours.identifiant, en_attente.identifiant):
        relu = await second.obtenir(identifiant, DEVICE)
        assert relu is not None
        assert relu.etat is EtatRapport.ECHOUE


async def test_un_job_termine_survit_a_l_assainissement(tmp_path: Path):
    """On n assainit que l inachevé : un rapport termine reste consultable."""
    chemin = tmp_path / "rapports.db"
    premier = RapportStore(chemin)
    await premier.initialiser()
    cree = await premier.creer("etude_filiere", "le cacao", DEVICE)
    await premier.terminer(cree.identifiant, "# Étude")

    second = RapportStore(chemin)
    await second.initialiser()
    assert await second.reprendre_orphelins() == 0
    relu = await second.obtenir(cree.identifiant, DEVICE)
    assert relu is not None
    assert relu.etat is EtatRapport.TERMINE


async def test_les_operations_sur_un_depot_non_pret_ne_levent_pas(tmp_path: Path):
    depot = RapportStore(tmp_path / "jamais.db")
    assert await depot.obtenir("x", DEVICE) is None
    assert await depot.lister(DEVICE) == []
    assert await depot.reprendre_orphelins() == 0
    assert await depot.avancer("x", 1, 2) is None
    assert await depot.terminer("x", "#") is None
    assert await depot.echouer("x", "boum") is None
    assert (await depot.creer("etude_filiere", "le cacao", DEVICE)).identifiant


async def test_une_base_illisible_ne_fait_pas_tomber_le_demarrage(tmp_path: Path):
    """Tolerance aux pannes : le service demarre, les rapports sont indisponibles."""
    chemin = tmp_path / "rapports.db"
    chemin.write_bytes(b"ceci n est pas une base SQLite")
    depot = RapportStore(chemin)
    await depot.initialiser()
    assert depot.pret is False


async def test_une_migration_qui_echoue_ne_laisse_pas_le_schema_a_moitie(tmp_path: Path):
    """Atomicite : une migration cassee ne doit laisser aucun debris derriere elle."""
    import sqlite3
    from contextlib import closing

    chemin = tmp_path / "rapports.db"
    depot = RapportStore(chemin)
    await depot.initialiser()
    assert depot.pret is True

    casse = RapportStore(chemin)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            RapportStore,
            "_MIGRATIONS",
            (*RapportStore._MIGRATIONS, "CREATE TABLE debris (x INTEGER); CECI N EST PAS DU SQL;"),
        )
        await casse.initialiser()

    # Tolerant aux pannes : on ne leve pas, mais le depot se declare indisponible.
    assert casse.pret is False
    with closing(sqlite3.connect(chemin)) as connexion:
        tables = {ligne[0] for ligne in connexion.execute("SELECT name FROM sqlite_master")}
        version = connexion.execute("PRAGMA user_version").fetchone()[0]
    assert "debris" not in tables
    assert "rapports" in tables
    assert version == len(RapportStore._MIGRATIONS)
