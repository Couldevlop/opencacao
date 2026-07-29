"""Tests de l'endpoint de constat visuel."""

from __future__ import annotations

import base64
import struct

import pytest
from fastapi.testclient import TestClient

ENTETES = {"X-Device-Id": "appareil-a"}


def _jpeg(largeur: int = 1024, hauteur: int = 768) -> bytes:
    soi = b"\xff\xd8"
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
    sof0 = (
        b"\xff\xc0"
        + struct.pack(">H", 17)
        + b"\x08"
        + struct.pack(">HH", hauteur, largeur)
        + b"\x03"
        + b"\x00" * 9
    )
    return soi + app0 + sof0


def _image_payload() -> dict:
    return {
        "contenu_base64": base64.b64encode(_jpeg()).decode("ascii"),
        "largeur": 1024,
        "hauteur": 768,
        "score_nettete": 400.0,
        "luminance_moyenne": 128.0,
    }


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("PARCELLES_ENABLED", "true")
    monkeypatch.setenv("PARCELLES_DB_PATH", str(tmp_path / "parcelles.db"))
    monkeypatch.setenv("CAPTURES_DIR", str(tmp_path / "captures"))
    monkeypatch.setenv("SESSIONS_DB_PATH", str(tmp_path / "sessions.db"))
    monkeypatch.setenv("PREWARM_ENABLED", "false")
    from app.core.config import get_settings
    from app.main import create_app

    get_settings.cache_clear()
    with TestClient(create_app()) as client_test:
        yield client_test
    get_settings.cache_clear()


def _parcelle_avec_capture(client: TestClient) -> tuple[str, str]:
    """Crée une parcelle et y dépose une photo. Retourne (parcelle, capture)."""
    parcelle = client.post(
        "/v1/parcelles", json={"nom": "Bloc", "localite": "Daloa"}, headers=ENTETES
    ).json()["identifiant"]
    capture = client.post(
        f"/v1/parcelles/{parcelle}/captures",
        json={"modalite": "photos", "images": [_image_payload()]},
        headers=ENTETES,
    ).json()["identifiant"]
    return parcelle, capture


def test_constat_sur_capture_inconnue_renvoie_404(client: TestClient):
    parcelle, _ = _parcelle_avec_capture(client)
    reponse = client.post(f"/v1/parcelles/{parcelle}/captures/inexistante/constat", headers=ENTETES)
    assert reponse.status_code == 404


def test_constat_sans_entete_appareil_est_refuse(client: TestClient):
    parcelle, capture = _parcelle_avec_capture(client)
    reponse = client.post(f"/v1/parcelles/{parcelle}/captures/{capture}/constat")
    assert reponse.status_code == 400


def test_constat_sur_une_capture_d_une_autre_parcelle_renvoie_404(client: TestClient):
    """Cloisonnement : une capture ne se laisse pas analyser depuis une autre parcelle."""
    _, capture = _parcelle_avec_capture(client)
    autre = client.post(
        "/v1/parcelles", json={"nom": "Autre", "localite": "Daloa"}, headers=ENTETES
    ).json()["identifiant"]
    reponse = client.post(f"/v1/parcelles/{autre}/captures/{capture}/constat", headers=ENTETES)
    assert reponse.status_code == 404


def test_vision_indisponible_renvoie_503_avec_une_consigne_lisible(client: TestClient):
    """Profil CPU (defaut des tests) : le VLM est absent, on le DIT."""
    parcelle, capture = _parcelle_avec_capture(client)
    reponse = client.post(f"/v1/parcelles/{parcelle}/captures/{capture}/constat", headers=ENTETES)
    assert reponse.status_code == 503
    assert "ANADER" in reponse.json()["detail"]


class _FauxVision:
    """Vision disponible, contrôlée par le test (aucun réseau)."""

    async def decrire(self, images, consigne):
        return "Cabosses tachetées de brun sur environ un tiers de leur surface."

    async def disponible(self) -> bool:
        return True


class _FausseInference:
    """Inférence contrôlée par le test."""

    async def generer(self, question: str, **_: object) -> str:
        return "Vos cabosses présentent des taches. Montrez ces photos à votre agent ANADER."

    def generer_stream(self, *_: object, **__: object):
        raise NotImplementedError

    async def ready(self) -> bool:
        return True


def test_avec_une_vision_disponible_le_constat_est_renvoye(client: TestClient):
    """Le constat expose ses observations, sans jamais nommer de maladie."""
    from pathlib import Path

    from app.api_deps import get_service_constats
    from app.application.constat_visuel import ServiceConstatVisuel
    from app.core.config import get_settings
    from app.services.constats import ServiceConstats

    service = ServiceConstats(
        client.app.state.parcelles,
        ServiceConstatVisuel(_FauxVision(), _FausseInference()),
        dossier_captures=Path(get_settings().captures_dir),
    )
    client.app.dependency_overrides[get_service_constats] = lambda: service
    try:
        parcelle, capture = _parcelle_avec_capture(client)
        reponse = client.post(
            f"/v1/parcelles/{parcelle}/captures/{capture}/constat", headers=ENTETES
        )
    finally:
        client.app.dependency_overrides.clear()

    assert reponse.status_code == 201
    corps = reponse.json()
    assert corps["parcelle"] == parcelle
    assert corps["capture"] == capture
    assert corps["etat_revue"] == "en_attente"
    assert len(corps["observations"]) == 1
    assert "ANADER" in corps["texte"]


def test_le_constat_porte_toujours_le_disclaimer(client: TestClient):
    """La mention ANADER est structurelle, pas laissee a la bonne volonte du modele."""
    from app.models.chat import DISCLAIMER
    from app.models.constat import ConstatReponse

    assert ConstatReponse.model_fields["disclaimer"].default == DISCLAIMER


def test_le_quota_d_analyses_est_plus_strict_que_le_debit_general(client: TestClient):
    """Une analyse coute des dizaines de secondes CPU : son budget est a part (API4)."""
    from app.api_deps import get_cache_client

    class _CacheCompteur:
        """Cache qui laisse passer le débit général et borne le quota d'analyses."""

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
        parcelle, capture = _parcelle_avec_capture(client)
        chemin = f"/v1/parcelles/{parcelle}/captures/{capture}/constat"
        statuts = [client.post(chemin, headers=ENTETES).status_code for _ in range(4)]
    finally:
        client.app.dependency_overrides.clear()

    # Les trois premières passent la garde (503 : pas de VLM en test), la quatrième non.
    assert statuts[:3] == [503, 503, 503]
    assert statuts[3] == 429
