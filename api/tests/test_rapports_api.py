"""Tests des endpoints de l'atelier de livrables."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

ENTETES = {"X-Device-Id": "appareil-a"}


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("RAPPORTS_ENABLED", "true")
    monkeypatch.setenv("RAPPORTS_DB_PATH", str(tmp_path / "rapports.db"))
    monkeypatch.setenv("SESSIONS_DB_PATH", str(tmp_path / "sessions.db"))
    monkeypatch.setenv("PREWARM_ENABLED", "false")
    from app.core.config import get_settings
    from app.main import create_app

    get_settings.cache_clear()
    with TestClient(create_app()) as client_test:
        yield client_test
    get_settings.cache_clear()


def _creer(client: TestClient, gabarit: str = "bulletin_regional", sujet: str = "Daloa"):
    return client.post("/v1/rapports", json={"gabarit": gabarit, "sujet": sujet}, headers=ENTETES)


def test_les_gabarits_sont_listes(client: TestClient):
    reponse = client.get("/v1/rapports/gabarits")
    assert reponse.status_code == 200
    assert "bulletin_regional" in reponse.json()


def test_creer_sans_entete_appareil_est_refuse(client: TestClient):
    reponse = client.post("/v1/rapports", json={"gabarit": "bulletin_regional", "sujet": "Daloa"})
    assert reponse.status_code == 400


def test_creer_avec_un_gabarit_inconnu_renvoie_404(client: TestClient):
    assert _creer(client, gabarit="fantome").status_code == 404


def test_un_sujet_vide_est_refuse(client: TestClient):
    reponse = client.post(
        "/v1/rapports", json={"gabarit": "bulletin_regional", "sujet": ""}, headers=ENTETES
    )
    assert reponse.status_code == 422


def test_creer_rend_un_job_en_attente(client: TestClient):
    reponse = _creer(client)
    assert reponse.status_code == 201
    assert reponse.json()["etat"] == "en_attente"


def test_lister_ne_rend_que_les_jobs_de_l_appareil(client: TestClient):
    _creer(client)
    assert client.get("/v1/rapports", headers={"X-Device-Id": "appareil-b"}).json() == []
    assert len(client.get("/v1/rapports", headers=ENTETES).json()) == 1


def test_obtenir_un_job_d_un_autre_appareil_renvoie_404(client: TestClient):
    identifiant = _creer(client).json()["identifiant"]
    reponse = client.get(f"/v1/rapports/{identifiant}", headers={"X-Device-Id": "appareil-b"})
    assert reponse.status_code == 404


def test_le_flux_emet_un_premier_evenement_de_progression(client: TestClient):
    """Critere d acceptation : premier octet avant toute generation."""
    identifiant = _creer(client).json()["identifiant"]
    with client.stream("GET", f"/v1/rapports/{identifiant}/stream", headers=ENTETES) as flux:
        assert flux.status_code == 200
        premiere = next(flux.iter_lines())
        charge = json.loads(premiere.removeprefix("data: "))
    assert charge["type"] == "progress"


def test_le_flux_d_un_job_inconnu_renvoie_404(client: TestClient):
    assert client.get("/v1/rapports/inexistant/stream", headers=ENTETES).status_code == 404


def test_le_flux_d_un_job_d_un_autre_appareil_renvoie_404(client: TestClient):
    identifiant = _creer(client).json()["identifiant"]
    reponse = client.get(
        f"/v1/rapports/{identifiant}/stream", headers={"X-Device-Id": "appareil-b"}
    )
    assert reponse.status_code == 404


def test_exporter_un_job_non_termine_renvoie_404(client: TestClient):
    identifiant = _creer(client).json()["identifiant"]
    reponse = client.get(f"/v1/rapports/{identifiant}/export?format=md", headers=ENTETES)
    assert reponse.status_code == 404


def test_exporter_dans_un_format_inconnu_renvoie_422(client: TestClient):
    identifiant = _creer(client).json()["identifiant"]
    reponse = client.get(f"/v1/rapports/{identifiant}/export?format=pdf", headers=ENTETES)
    assert reponse.status_code == 422


def test_le_quota_de_generation_est_distinct_du_debit_general(client: TestClient):
    """Une etude enchaine une generation PAR SECTION : son budget est a part (API4)."""
    from app.api_deps import get_cache_client

    class _CacheCompteur:
        def __init__(self) -> None:
            self.compteurs: dict[str, int] = {}

        async def hit_rate_limit(self, client_ip: str) -> bool:
            return False

        async def hit_quota(self, cle: str, limite: int, fenetre_s: int) -> bool:
            self.compteurs[cle] = self.compteurs.get(cle, 0) + 1
            return self.compteurs[cle] > limite

    cache = _CacheCompteur()
    client.app.dependency_overrides[get_cache_client] = lambda: cache
    try:
        statuts = [_creer(client).status_code for _ in range(4)]
    finally:
        client.app.dependency_overrides.clear()

    assert statuts[:3] == [201, 201, 201]
    assert statuts[3] == 429
    assert list(cache.compteurs) == ["rapport:appareil-a"]


class _FausseInference:
    """Inférence contrôlée par le test (aucun réseau)."""

    async def generer(self, question: str, **_: object) -> str:
        return "Un paragraphe analytique documenté."

    def generer_stream(self, *_: object, **__: object):
        raise NotImplementedError

    async def ready(self) -> bool:
        return True


class _FauxCollecteur:
    async def collecter(self, sujet: str):
        from app.models.constat import NiveauConfiance
        from app.models.rapport import Affirmation

        return (
            Affirmation(
                texte="La production avoisine 2,2 millions de tonnes.",
                source="CNRA",
                date="2025-10-01",
                methode="rag",
                confiance=NiveauConfiance.MOYENNE,
                empreinte="e3b0c44298fc",
            ),
        )


def _service_mocke(client: TestClient):
    """Remplace le service par un identique, monte sur une inférence contrôlée."""
    from app.api_deps import get_service_rapports
    from app.application.redaction import ContexteGeneration, MoteurRedaction
    from app.services.rapports import ServiceRapports

    contexte = ContexteGeneration("opencacao-8b", "1.1.0", "0.6.75", "cpu")
    collecteurs = {
        nom: _FauxCollecteur()
        for nom in ("rag", "prix", "meteo", "satellite", "parcelle", "constats")
    }
    service = ServiceRapports(
        client.app.state.rapports,
        lambda: MoteurRedaction(_FausseInference(), collecteurs, contexte),
    )
    client.app.dependency_overrides[get_service_rapports] = lambda: service
    return service


def test_obtenir_un_job_rend_son_etat(client: TestClient):
    identifiant = _creer(client).json()["identifiant"]
    reponse = client.get(f"/v1/rapports/{identifiant}", headers=ENTETES)
    assert reponse.status_code == 200
    assert reponse.json()["identifiant"] == identifiant
    assert reponse.json()["gabarit"] == "bulletin_regional"


def test_le_parcours_complet_aboutit_a_un_fichier_telechargeable(client: TestClient):
    """Creer, diffuser jusqu au bout, exporter : le chemin que suivra la demonstration."""
    _service_mocke(client)
    try:
        identifiant = _creer(client).json()["identifiant"]
        with client.stream("GET", f"/v1/rapports/{identifiant}/stream", headers=ENTETES) as flux:
            evenements = [
                json.loads(ligne.removeprefix("data: "))
                for ligne in flux.iter_lines()
                if ligne.startswith("data: ")
            ]
        assert evenements[-1]["type"] == "final"

        export = client.get(f"/v1/rapports/{identifiant}/export?format=md", headers=ENTETES)
        binaire = client.get(f"/v1/rapports/{identifiant}/export?format=docx", headers=ENTETES)
    finally:
        client.app.dependency_overrides.clear()

    assert export.status_code == 200
    assert export.content.startswith(b"# Bulletin r")
    assert "attachment" in export.headers["content-disposition"]
    assert "bulletin_regional-" in export.headers["content-disposition"]
    assert binaire.status_code == 200
    assert binaire.content.startswith(b"PK")


def test_le_flux_consomme_le_quota_de_generation(client: TestClient):
    """C est le FLUX qui coute, pas le POST : sans cela, 3 jobs suffisaient a boucler."""
    from app.api_deps import get_cache_client

    class _CacheCompteur:
        def __init__(self) -> None:
            self.compteurs: dict[str, int] = {}

        async def hit_rate_limit(self, client_ip: str) -> bool:
            return False

        async def hit_quota(self, cle: str, limite: int, fenetre_s: int) -> bool:
            self.compteurs[cle] = self.compteurs.get(cle, 0) + 1
            return self.compteurs[cle] > limite

    cache = _CacheCompteur()
    identifiant = _creer(client).json()["identifiant"]
    client.app.dependency_overrides[get_cache_client] = lambda: cache
    try:
        statuts = [
            client.get(f"/v1/rapports/{identifiant}/stream", headers=ENTETES).status_code
            for _ in range(4)
        ]
    finally:
        client.app.dependency_overrides.clear()

    assert cache.compteurs["rapport:appareil-a"] == 4
    assert statuts[3] == 429


def test_un_gabarit_malforme_donne_un_503_lisible(client: TestClient):
    """Montage ConfigMap rate : erreur d exploitation, pas faute du client — et
    surtout pas une trace."""
    from app.api_deps import get_service_rapports
    from app.services.gabarits import GabaritInvalide

    class _ServiceCasse:
        async def creer(self, gabarit: str, sujet: str, demandeur: str):
            raise GabaritInvalide("sections mal formées")

    client.app.dependency_overrides[get_service_rapports] = _ServiceCasse
    try:
        reponse = _creer(client)
    finally:
        client.app.dependency_overrides.clear()
    assert reponse.status_code == 503
    assert "indisponible" in reponse.json()["detail"]


def test_l_export_a_son_propre_quota(client: TestClient):
    """Rendre un document coute de 0,3 a 1,3 s de CPU, et la route est rappelable en
    boucle sur un rapport deja produit. Moins cher qu une generation, pas gratuit."""
    from app.api_deps import get_cache_client
    from app.routers.rapports import _EXPORTS_PAR_FENETRE

    class _CacheCompteur:
        def __init__(self) -> None:
            self.compteurs: dict[str, int] = {}

        async def hit_rate_limit(self, client_ip: str) -> bool:
            return False

        async def hit_quota(self, cle: str, limite: int, fenetre_s: int) -> bool:
            self.compteurs[cle] = self.compteurs.get(cle, 0) + 1
            return self.compteurs[cle] > limite

    cache = _CacheCompteur()
    _service_mocke(client)
    try:
        identifiant = _creer(client).json()["identifiant"]
        with client.stream("GET", f"/v1/rapports/{identifiant}/stream", headers=ENTETES) as flux:
            list(flux.iter_lines())
        client.app.dependency_overrides[get_cache_client] = lambda: cache
        statuts = [
            client.get(f"/v1/rapports/{identifiant}/export?format=md", headers=ENTETES).status_code
            for _ in range(_EXPORTS_PAR_FENETRE + 1)
        ]
    finally:
        client.app.dependency_overrides.clear()

    assert statuts[:_EXPORTS_PAR_FENETRE] == [200] * _EXPORTS_PAR_FENETRE
    assert statuts[-1] == 429
    assert list(cache.compteurs) == ["export:appareil-a"]
