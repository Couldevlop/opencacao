"""Tests des endpoints /v1/parcelles."""

from __future__ import annotations

import asyncio
import base64
import json
import struct
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

ENTETES = {"X-Device-Id": "appareil-a"}
AUTRES_ENTETES = {"X-Device-Id": "appareil-b"}


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


def _image_payload(**surcharges) -> dict:
    charge = {
        "contenu_base64": base64.b64encode(_jpeg()).decode("ascii"),
        "largeur": 1024,
        "hauteur": 768,
        "score_nettete": 400.0,
        "luminance_moyenne": 128.0,
    }
    charge.update(surcharges)
    return charge


def _carre() -> list[dict]:
    cote = 0.000899
    lat, lon = 6.85, -5.28
    return [
        {"latitude": a, "longitude": b}
        for a, b in [
            (lat, lon),
            (lat, lon + cote),
            (lat + cote, lon + cote),
            (lat + cote, lon),
        ]
    ]


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    """Client de test avec les parcelles activées sur un volume temporaire."""
    # Pas de pré-chauffage en test (il déclencherait de vrais appels d'inférence) et
    # aucune écriture hors du dossier temporaire (cf. conftest.py).
    monkeypatch.setenv("PREWARM_ENABLED", "false")
    monkeypatch.setenv("SESSIONS_DB_PATH", str(tmp_path / "sessions.db"))
    monkeypatch.setenv("PARCELLES_ENABLED", "true")
    monkeypatch.setenv("PARCELLES_DB_PATH", str(tmp_path / "parcelles.db"))
    monkeypatch.setenv("CAPTURES_DIR", str(tmp_path / "captures"))
    from app.core.config import get_settings
    from app.main import create_app

    get_settings.cache_clear()
    with TestClient(create_app()) as client_test:
        yield client_test
    get_settings.cache_clear()


def _creer(client: TestClient, entetes: dict = ENTETES) -> str:
    reponse = client.post(
        "/v1/parcelles", json={"nom": "Bloc Est", "localite": "Daloa"}, headers=entetes
    )
    assert reponse.status_code == 201, reponse.text
    return reponse.json()["identifiant"]


def test_creer_une_parcelle_renvoie_201(client: TestClient):
    reponse = client.post(
        "/v1/parcelles", json={"nom": "Bloc Est", "localite": "Daloa"}, headers=ENTETES
    )
    assert reponse.status_code == 201
    charge = reponse.json()
    assert charge["nom"] == "Bloc Est"
    assert charge["geometrie"] is None


def test_creer_sans_nom_renvoie_422(client: TestClient):
    reponse = client.post("/v1/parcelles", json={"localite": "Daloa"}, headers=ENTETES)
    assert reponse.status_code == 422


def test_lister_ne_montre_que_ses_propres_parcelles(client: TestClient):
    _creer(client, ENTETES)
    _creer(client, AUTRES_ENTETES)
    reponse = client.get("/v1/parcelles", headers=ENTETES)
    assert reponse.status_code == 200
    assert len(reponse.json()) == 1


def test_obtenir_une_parcelle_d_un_autre_appareil_renvoie_404(client: TestClient):
    identifiant = _creer(client, ENTETES)
    reponse = client.get(f"/v1/parcelles/{identifiant}", headers=AUTRES_ENTETES)
    assert reponse.status_code == 404


def test_enregistrer_une_geometrie_renvoie_la_superficie(client: TestClient):
    identifiant = _creer(client)
    reponse = client.put(
        f"/v1/parcelles/{identifiant}/geometrie",
        json={"points": _carre(), "source": "parcours_gps"},
        headers=ENTETES,
    )
    assert reponse.status_code == 200, reponse.text
    geometrie = reponse.json()["geometrie"]
    assert geometrie["type"] == "polygone"
    assert geometrie["superficie_ha"] == pytest.approx(1.0, abs=0.05)


def test_geometrie_hors_ci_renvoie_422_avec_motif_lisible(client: TestClient):
    identifiant = _creer(client)
    paris = [
        {"latitude": 48.86 + i * 0.001, "longitude": 2.35 + j * 0.001}
        for i, j in [(0, 0), (0, 1), (1, 1), (1, 0)]
    ]
    reponse = client.put(
        f"/v1/parcelles/{identifiant}/geometrie",
        json={"points": paris},
        headers=ENTETES,
    )
    assert reponse.status_code == 422
    assert "Côte d'Ivoire" in reponse.json()["detail"]


def test_geometrie_sur_parcelle_inconnue_renvoie_404(client: TestClient):
    reponse = client.put(
        "/v1/parcelles/inexistante/geometrie",
        json={"points": _carre()},
        headers=ENTETES,
    )
    assert reponse.status_code == 404


def test_deposer_des_photos_renvoie_201_et_le_verdict(client: TestClient):
    identifiant = _creer(client)
    reponse = client.post(
        f"/v1/parcelles/{identifiant}/captures",
        json={"modalite": "photos", "images": [_image_payload()]},
        headers=ENTETES,
    )
    assert reponse.status_code == 201, reponse.text
    charge = reponse.json()
    assert charge["modalite"] == "photos"
    assert charge["images"][0]["recevabilite"]["recevable"] is True


def test_deposer_une_photo_floue_renvoie_le_conseil_de_reprise(client: TestClient):
    identifiant = _creer(client)
    reponse = client.post(
        f"/v1/parcelles/{identifiant}/captures",
        json={"modalite": "photos", "images": [_image_payload(score_nettete=3.0)]},
        headers=ENTETES,
    )
    assert reponse.status_code == 201
    recevabilite = reponse.json()["images"][0]["recevabilite"]
    assert recevabilite["recevable"] is False
    assert recevabilite["motif"] == "flou"
    assert "approchez" in recevabilite["conseil"].lower()


def test_deposer_un_parcours_video_accepte_les_deux_contrats(client: TestClient):
    identifiant = _creer(client)
    reponse = client.post(
        f"/v1/parcelles/{identifiant}/captures",
        json={
            "modalite": "parcours_video",
            "images": [_image_payload()],
            "trace": _carre(),
        },
        headers=ENTETES,
    )
    assert reponse.status_code == 201
    charge = reponse.json()
    assert len(charge["images"]) == 1
    assert len(charge["trace"]) == 4


def test_capture_vide_renvoie_422(client: TestClient):
    identifiant = _creer(client)
    reponse = client.post(
        f"/v1/parcelles/{identifiant}/captures",
        json={"modalite": "photos", "images": [], "trace": []},
        headers=ENTETES,
    )
    assert reponse.status_code == 422


def test_plus_de_douze_images_renvoie_422(client: TestClient):
    identifiant = _creer(client)
    reponse = client.post(
        f"/v1/parcelles/{identifiant}/captures",
        json={"modalite": "video", "images": [_image_payload() for _ in range(13)]},
        headers=ENTETES,
    )
    assert reponse.status_code == 422


def test_relire_une_capture(client: TestClient):
    identifiant = _creer(client)
    depot = client.post(
        f"/v1/parcelles/{identifiant}/captures",
        json={"modalite": "photos", "images": [_image_payload()]},
        headers=ENTETES,
    )
    capture_id = depot.json()["identifiant"]
    reponse = client.get(f"/v1/parcelles/{identifiant}/captures/{capture_id}", headers=ENTETES)
    assert reponse.status_code == 200
    assert reponse.json()["identifiant"] == capture_id


def test_obtenir_sa_propre_parcelle_renvoie_200(client: TestClient):
    identifiant = _creer(client)
    reponse = client.get(f"/v1/parcelles/{identifiant}", headers=ENTETES)
    assert reponse.status_code == 200
    assert reponse.json()["identifiant"] == identifiant


def test_capture_sur_parcelle_inconnue_renvoie_404(client: TestClient):
    reponse = client.post(
        "/v1/parcelles/inexistante/captures",
        json={"modalite": "photos", "images": [_image_payload()]},
        headers=ENTETES,
    )
    assert reponse.status_code == 404


def test_capture_dont_la_trace_sort_du_pays_renvoie_422(client: TestClient):
    identifiant = _creer(client)
    reponse = client.post(
        f"/v1/parcelles/{identifiant}/captures",
        json={"modalite": "parcours", "trace": [{"latitude": 48.86, "longitude": 2.35}]},
        headers=ENTETES,
    )
    assert reponse.status_code == 422
    assert "Côte d'Ivoire" in reponse.json()["detail"]


def test_relire_une_capture_inconnue_renvoie_404(client: TestClient):
    identifiant = _creer(client)
    reponse = client.get(f"/v1/parcelles/{identifiant}/captures/inconnue", headers=ENTETES)
    assert reponse.status_code == 404


def test_rate_limit_depasse_renvoie_429(client: TestClient):
    """Le dépôt de parcelles est protégé par le rate-limit par IP, comme les sessions."""
    from app.api_deps import get_cache_client

    class _CacheSature:
        async def hit_rate_limit(self, client_ip: str) -> bool:
            return True

    client.app.dependency_overrides[get_cache_client] = _CacheSature
    try:
        reponse = client.post(
            "/v1/parcelles", json={"nom": "Bloc Est", "localite": "Daloa"}, headers=ENTETES
        )
    finally:
        client.app.dependency_overrides.clear()
    assert reponse.status_code == 429


def test_get_parcelle_store_expose_le_depot_de_l_application(client: TestClient):
    """La dépendance rend bien le dépôt construit par le cycle de vie."""
    from app.api_deps import get_parcelle_store

    requete = SimpleNamespace(app=client.app)
    assert get_parcelle_store(requete) is client.app.state.parcelles  # type: ignore[arg-type]


async def test_boucle_de_purge_des_captures_journalise_et_survit_aux_erreurs(monkeypatch):
    """La purge quotidienne compte les fichiers effacés et n'est jamais tuée par une erreur."""
    from app.main import _lancer_purge_captures

    class _ServiceFactice:
        def __init__(self) -> None:
            self.tours = 0

        async def purger(self) -> int:
            self.tours += 1
            if self.tours == 1:
                return 3
            raise RuntimeError("volume /data indisponible")

    service = _ServiceFactice()
    application = SimpleNamespace(state=SimpleNamespace(service_parcelles=service))
    sommeil = asyncio.sleep
    tours: list[int] = []

    async def _sommeil_court(_duree: float) -> None:
        tours.append(1)
        if len(tours) > 2:
            raise asyncio.CancelledError
        await sommeil(0)

    monkeypatch.setattr(asyncio, "sleep", _sommeil_court)
    tache = _lancer_purge_captures(application)  # type: ignore[arg-type]
    with pytest.raises(asyncio.CancelledError):
        await tache
    assert service.tours == 2


def test_endpoints_absents_quand_le_drapeau_est_off(tmp_path, monkeypatch):
    """Parcelles désactivées : les routes répondent 404, le reste de l'API vit."""
    monkeypatch.setenv("PREWARM_ENABLED", "false")
    monkeypatch.setenv("SESSIONS_DB_PATH", str(tmp_path / "sessions.db"))
    monkeypatch.setenv("PARCELLES_ENABLED", "false")
    from app.core.config import get_settings
    from app.main import create_app

    get_settings.cache_clear()
    with TestClient(create_app()) as client_test:
        assert client_test.get("/v1/parcelles", headers=ENTETES).status_code == 404
        # Le liveness probe du dépôt est /v1/health (routeur health préfixé "/v1").
        assert client_test.get("/v1/health").status_code == 200
    get_settings.cache_clear()


# ------------------------------------------------- taille reelle des televersements
#
# Ces deux tests existent parce que le plafond global du corps de requete est de
# 16 Ko : une seule photo reelle le depasse. Sans plafond propre au prefixe
# /v1/parcelles, la fonctionnalite serait rejetee en 413 EN PRODUCTION alors que
# tous les tests unitaires passent (leurs images synthetiques font 39 octets).


def _photo_realiste() -> dict:
    """Une image dont le base64 depasse largement le plafond global de 16 Ko."""
    octets = _jpeg() + b"\x00" * 300_000
    return {
        "contenu_base64": base64.b64encode(octets).decode("ascii"),
        "largeur": 1024,
        "hauteur": 768,
        "score_nettete": 400.0,
        "luminance_moyenne": 128.0,
    }


def test_une_photo_de_taille_reelle_est_acceptee(client: TestClient):
    identifiant = _creer(client)
    charge = {"modalite": "photos", "images": [_photo_realiste()]}
    assert len(json.dumps(charge)) > 16_384
    reponse = client.post(f"/v1/parcelles/{identifiant}/captures", json=charge, headers=ENTETES)
    assert reponse.status_code == 201, reponse.text


def test_le_plafond_elargi_ne_vaut_que_pour_les_parcelles(client: TestClient):
    """Le reste de l API garde la borne stricte : on ouvre une porte, pas un boulevard."""
    reponse = client.post(
        "/v1/chat", json={"question": "x" * 20_000, "langue": "fr"}, headers=ENTETES
    )
    assert reponse.status_code == 413


# ------------------------------------------------- frontiere d autorisation
#
# get_device_id rend "" quand l en-tete X-Device-Id est absent : c est l espace
# « herite » partage, un choix de compatibilite assume pour les sessions V2. Applique
# aux PARCELLES, il devient une fuite : tout appelant omettant l en-tete voit — et
# peut modifier — les parcelles de tous les autres appelants sans en-tete, alors
# qu une parcelle porte le POLYGONE GPS EXACT de la plantation d un producteur.
#
# Les parcelles sont neuves : aucun client herite a menager. On exige l identifiant.


def test_lister_sans_entete_appareil_est_refuse(client: TestClient):
    reponse = client.get("/v1/parcelles")
    assert reponse.status_code == 400


def test_creer_sans_entete_appareil_est_refuse(client: TestClient):
    reponse = client.post("/v1/parcelles", json={"nom": "X", "localite": "Daloa"})
    assert reponse.status_code == 400


def test_obtenir_sans_entete_appareil_est_refuse(client: TestClient):
    identifiant = _creer(client)
    assert client.get(f"/v1/parcelles/{identifiant}").status_code == 400


def test_geometrie_sans_entete_appareil_est_refusee(client: TestClient):
    identifiant = _creer(client)
    reponse = client.put(f"/v1/parcelles/{identifiant}/geometrie", json={"points": _carre()})
    assert reponse.status_code == 400


def test_capture_sans_entete_appareil_est_refusee(client: TestClient):
    identifiant = _creer(client)
    reponse = client.post(
        f"/v1/parcelles/{identifiant}/captures",
        json={"modalite": "photos", "images": [_image_payload()]},
    )
    assert reponse.status_code == 400


def test_un_entete_vide_vaut_un_entete_absent(client: TestClient):
    """Envoyer X-Device-Id: '' ne doit pas ouvrir l espace partage par la bande."""
    assert client.get("/v1/parcelles", headers={"X-Device-Id": "   "}).status_code == 400


def test_les_sessions_gardent_leur_espace_herite(client: TestClient):
    """La compatibilite V2 n est pas touchee : seules les parcelles se durcissent."""
    assert client.get("/v1/sessions").status_code == 200


def test_le_parcours_gps_s_enregistre_aussi_en_post(client: TestClient) -> None:
    """Le WAF du contrôleur d'ingress applique le jeu de règles OWASP CRS, qui
    n'autorise que GET/HEAD/POST/OPTIONS. Tout PUT partant d'un navigateur était donc
    rejeté en 403 AVANT d'atteindre l'API : le parcours GPS — fonction phare de « Ma
    parcelle » — n'avait jamais fonctionné en production, alors que le dépôt de photos
    (POST) passait, ce qui rendait la panne d'autant plus discrète (19/08/2026).

    Sans ce test, un nettoyage de routes retirerait le POST et la panne reviendrait,
    tout aussi silencieuse."""
    entetes = {"X-Device-Id": "appareil-recette"}
    parcelle = client.post(
        "/v1/parcelles", json={"nom": "Recette", "localite": "Soubré"}, headers=entetes
    ).json()

    reponse = client.post(
        f"/v1/parcelles/{parcelle['identifiant']}/geometrie",
        json={
            "points": [
                {"latitude": 5.7853, "longitude": -6.5936},
                {"latitude": 5.7853, "longitude": -6.5923},
                {"latitude": 5.7840, "longitude": -6.5923},
                {"latitude": 5.7853, "longitude": -6.5936},
            ],
            "source": "parcours_gps",
        },
        headers=entetes,
    )

    assert reponse.status_code == 200
    assert reponse.json()["geometrie"] is not None
