"""Tests de la file de revue ANADER des constats visuels (étage 6)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.application.revue_constats as export_constats
import app.curation.main as cur
from app.core.parcelles_store import ParcelleStore
from app.curation.ratelimit import LimiteurConnexion, LimiteurDebit
from app.models.constat import Constat, NiveauConfiance, Observation, Organe

MOT_DE_PASSE = "motdepasse-de-test"


def _constat(identifiant: str = "c1", minutes: int = 0) -> Constat:
    return Constat(
        identifiant=identifiant,
        capture="cap1",
        parcelle="p1",
        proprietaire="appareil-a",
        observations=(
            Observation(
                organe=Organe.CABOSSE,
                description="Taches brunes sur un tiers de la cabosse.",
                confiance=NiveauConfiance.MOYENNE,
                empreinte_image="a" * 64,
            ),
        ),
        texte="Vos cabosses présentent des taches. Montrez-les à votre agent ANADER.",
        confiance=NiveauConfiance.MOYENNE,
        cree_le=datetime.now(UTC) + timedelta(minutes=minutes),
    )


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    """Console de curation avec authentification ACTIVE et dépôt temporaire."""
    monkeypatch.setenv("PARCELLES_ENABLED", "true")
    monkeypatch.setenv("PARCELLES_DB_PATH", str(tmp_path / "parcelles.db"))
    monkeypatch.setattr(cur, "_MOT_DE_PASSE", MOT_DE_PASSE)
    # Les limiteurs sont un état de module : sans remise à zéro, les requêtes d'un
    # test compteraient dans le suivant (et la suite deviendrait dépendante de l'ordre).
    monkeypatch.setattr(cur, "_LIMITEUR_LOGIN", LimiteurConnexion(max_echecs=10, fenetre_s=300))
    monkeypatch.setattr(cur, "_LIMITEUR_REVUE", LimiteurDebit(max_requetes=60, fenetre_s=60))
    from app.core.config import get_settings

    get_settings.cache_clear()
    with TestClient(cur.app) as client_test:
        yield client_test
    get_settings.cache_clear()


@pytest.fixture
def entetes_auth(client: TestClient) -> dict[str, str]:
    """Ouvre une session sur la console et rend le cookie de session."""
    reponse = client.post(
        "/api/login", json={"utilisateur": cur._UTILISATEUR, "mot_de_passe": MOT_DE_PASSE}
    )
    assert reponse.status_code == 200
    return {"Cookie": f"curation_session={client.cookies['curation_session']}"}


@pytest.fixture
def avec_constat(client: TestClient):
    """Insère des constats en attente dans le dépôt de la console.

    L'insertion passe par la méthode synchrone du dépôt (celle qui tourne dans un
    thread en production) : les méthodes ``async`` prennent un verrou lié à la boucle
    d'événements du client, qu'un test ne peut pas emprunter sans la casser.
    """
    depot: ParcelleStore = client.app.state.parcelles

    def _inserer(*constats: Constat) -> None:
        for constat in constats or (_constat(),):
            depot._inserer_constat(constat)

    return _inserer


# ------------------------------------------------------------ authentification


def test_la_file_est_refusee_sans_authentification(client: TestClient):
    """La revue voit les constats de TOUS les producteurs : jamais en acces libre."""
    assert client.get("/curation/constats").status_code in (401, 403)


def test_la_revue_est_refusee_sans_authentification(client: TestClient):
    reponse = client.post(
        "/curation/constats/c1/revue",
        json={"etat": "confirme", "revu_par": "x", "correction": ""},
    )
    assert reponse.status_code in (401, 403)


def test_l_export_est_refuse_sans_authentification(client: TestClient):
    assert client.get("/curation/constats/export").status_code in (401, 403)


# ------------------------------------------------------------------- la file


def test_la_file_rend_les_constats_en_attente(client, entetes_auth, avec_constat):
    avec_constat()
    reponse = client.get("/curation/constats", headers=entetes_auth)
    assert reponse.status_code == 200
    assert [c["identifiant"] for c in reponse.json()] == ["c1"]


def test_la_file_rend_les_plus_anciens_d_abord(client, entetes_auth, avec_constat):
    avec_constat(_constat("recent", minutes=10), _constat("ancien", minutes=-10))
    file_attente = client.get("/curation/constats", headers=entetes_auth).json()
    assert [c["identifiant"] for c in file_attente] == ["ancien", "recent"]


def test_confirmer_sort_le_constat_de_la_file(client, entetes_auth, avec_constat):
    avec_constat()
    client.post(
        "/curation/constats/c1/revue",
        json={"etat": "confirme", "revu_par": "agent-anader-7", "correction": ""},
        headers=entetes_auth,
    )
    assert client.get("/curation/constats", headers=entetes_auth).json() == []


def test_corriger_conserve_l_etiquette_humaine(client, entetes_auth, avec_constat):
    avec_constat()
    reponse = client.post(
        "/curation/constats/c1/revue",
        json={
            "etat": "corrige",
            "revu_par": "agent-anader-7",
            "correction": "Ombrage insuffisant, pas une atteinte.",
        },
        headers=entetes_auth,
    )
    assert reponse.status_code == 200
    assert reponse.json()["correction"].startswith("Ombrage")


def test_un_etat_de_revue_inconnu_est_refuse(client, entetes_auth, avec_constat):
    avec_constat()
    reponse = client.post(
        "/curation/constats/c1/revue",
        json={"etat": "peut-etre", "revu_par": "x", "correction": ""},
        headers=entetes_auth,
    )
    assert reponse.status_code == 422


@pytest.mark.parametrize(
    "correction",
    [
        "En fait c'est la pourriture brune.",
        "Il s'agit du swollen shoot, sans doute.",
        "Appliquez un fongicide cuprique sur les cabosses.",
        "Traiter au produit phytosanitaire adapté.",
    ],
)
def test_une_correction_qui_franchit_un_interdit_est_refusee(
    client, entetes_auth, avec_constat, correction
):
    """La correction devient une etiquette d entrainement : memes garde-fous (D3)."""
    avec_constat()
    reponse = client.post(
        "/curation/constats/c1/revue",
        json={"etat": "corrige", "revu_par": "agent-7", "correction": correction},
        headers=entetes_auth,
    )
    assert reponse.status_code == 422


def test_une_correction_refusee_ne_touche_pas_le_constat(client, entetes_auth, avec_constat):
    """On refuse, on ne nettoie pas : le constat reste en attente, intact."""
    avec_constat()
    client.post(
        "/curation/constats/c1/revue",
        json={"etat": "corrige", "revu_par": "agent-7", "correction": "C'est l anthracnose."},
        headers=entetes_auth,
    )
    file_attente = client.get("/curation/constats", headers=entetes_auth).json()
    assert [c["identifiant"] for c in file_attente] == ["c1"]


def test_une_correction_descriptive_reste_acceptee(client, entetes_auth, avec_constat):
    """Decrire ce qu on voit est le travail de l agent : cela doit passer."""
    avec_constat()
    reponse = client.post(
        "/curation/constats/c1/revue",
        json={
            "etat": "corrige",
            "revu_par": "agent-7",
            "correction": "Ombrage insuffisant, pas une atteinte.",
        },
        headers=entetes_auth,
    )
    assert reponse.status_code == 200


def test_reviser_un_constat_inconnu_renvoie_404(client, entetes_auth):
    reponse = client.post(
        "/curation/constats/fantome/revue",
        json={"etat": "confirme", "revu_par": "x", "correction": ""},
        headers=entetes_auth,
    )
    assert reponse.status_code == 404


# ----------------------------------------------------------------- l'export


def test_l_export_produit_une_ligne_par_constat_revu(client, entetes_auth, avec_constat):
    """C'est CE fichier qui devient le jeu de donnees ivoirien (spec §7.6)."""
    avec_constat()
    client.post(
        "/curation/constats/c1/revue",
        json={"etat": "corrige", "revu_par": "agent-7", "correction": "Ombrage."},
        headers=entetes_auth,
    )
    corps = client.get("/curation/constats/export", headers=entetes_auth).text
    lignes = [json.loads(ligne) for ligne in corps.strip().splitlines()]
    assert len(lignes) == 1
    assert lignes[0]["empreinte"] == "a" * 64
    assert lignes[0]["organe"] == "cabosse"
    assert lignes[0]["etat"] == "corrige"
    assert lignes[0]["correction"] == "Ombrage."


def test_l_export_ignore_les_constats_non_revus(client, entetes_auth, avec_constat):
    """Un constat en attente n a pas d etiquette humaine : il ne sert pas d exemple."""
    avec_constat()
    assert client.get("/curation/constats/export", headers=entetes_auth).text.strip() == ""


def test_l_export_ne_contient_ni_identifiant_d_appareil_ni_parcelle(
    client, entetes_auth, avec_constat
):
    """Minimisation (RGPD) : le jeu d entrainement n a pas besoin de savoir QUI."""
    avec_constat()
    client.post(
        "/curation/constats/c1/revue",
        json={"etat": "confirme", "revu_par": "agent-7", "correction": ""},
        headers=entetes_auth,
    )
    corps = client.get("/curation/constats/export", headers=entetes_auth).text
    assert "appareil-a" not in corps
    assert "p1" not in corps


def test_l_export_se_telecharge_au_lieu_de_s_afficher(client, entetes_auth, avec_constat):
    """Texte saisi par un agent : il se telecharge, le navigateur ne l interprete pas."""
    avec_constat()
    reponse = client.get("/curation/constats/export", headers=entetes_auth)
    assert reponse.headers["content-type"].startswith("application/x-ndjson")
    assert reponse.headers["content-disposition"].startswith("attachment")


def test_l_export_lit_par_pages_et_les_rend_toutes(
    client, entetes_auth, avec_constat, monkeypatch: pytest.MonkeyPatch
):
    """Pagination : la crete memoire ne doit plus suivre la taille du jeu de donnees.

    On abaisse la page a 2 pour trois constats : si le service ne bouclait pas, ou
    bouclait sans avancer le decalage, l export serait tronque ou repeterait la
    premiere page.
    """
    monkeypatch.setattr(export_constats, "TAILLE_PAGE_EXPORT", 2)
    avec_constat(*(_constat(f"c{indice}", minutes=indice) for indice in range(3)))
    for indice in range(3):
        client.post(
            f"/curation/constats/c{indice}/revue",
            json={"etat": "confirme", "revu_par": "agent-7", "correction": ""},
            headers=entetes_auth,
        )

    corps = client.get("/curation/constats/export", headers=entetes_auth).text
    empreintes = [json.loads(ligne)["cree_le"] for ligne in corps.strip().splitlines()]
    assert len(empreintes) == 3
    assert len(set(empreintes)) == 3  # aucune page rejouee


# --------------------------------------------------------- debit de la console


def test_la_file_de_revue_est_bornee_en_debit(client, entetes_auth, monkeypatch):
    """Une session valide n est pas un blanc-seing : ces routes lisent tout le depot."""
    monkeypatch.setattr(cur, "_LIMITEUR_REVUE", LimiteurDebit(max_requetes=2, fenetre_s=60))
    assert client.get("/curation/constats", headers=entetes_auth).status_code == 200
    assert client.get("/curation/constats", headers=entetes_auth).status_code == 200
    assert client.get("/curation/constats", headers=entetes_auth).status_code == 429


def test_le_debit_est_compte_par_ip_cliente(client, entetes_auth, monkeypatch):
    """Derriere l ingress, toutes les requetes ont la meme IP TCP : on lit le 1er hop."""
    monkeypatch.setattr(cur, "_LIMITEUR_REVUE", LimiteurDebit(max_requetes=1, fenetre_s=60))
    saturant = {**entetes_auth, "X-Forwarded-For": "203.0.113.7"}
    autre = {**entetes_auth, "X-Forwarded-For": "203.0.113.8"}
    assert client.get("/curation/constats", headers=saturant).status_code == 200
    assert client.get("/curation/constats", headers=saturant).status_code == 429
    # Un curateur voisin ne doit pas payer pour l'autre.
    assert client.get("/curation/constats", headers=autre).status_code == 200


# ------------------------------------------------- fail-closed de la console


def test_une_console_sans_mot_de_passe_refuse_au_lieu_de_tout_ouvrir(
    client, monkeypatch: pytest.MonkeyPatch
):
    """Un secret vide n ouvre plus la console : elle est publiee sur Internet."""
    monkeypatch.setattr(cur, "_MOT_DE_PASSE", "")
    monkeypatch.setattr(cur, "_AUTH_DESACTIVEE", False)
    assert client.get("/curation/constats").status_code == 503


def test_le_bypass_d_authentification_doit_etre_demande_explicitement(
    client, monkeypatch: pytest.MonkeyPatch
):
    """Mode port-forward : possible, mais jamais par accident."""
    monkeypatch.setattr(cur, "_MOT_DE_PASSE", "")
    monkeypatch.setattr(cur, "_AUTH_DESACTIVEE", True)
    assert client.get("/curation/constats").status_code == 200
