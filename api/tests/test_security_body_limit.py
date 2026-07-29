"""Tests du durcissement du middleware de limite de corps (OWASP API4:2023)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.security import BodySizeLimitMiddleware


def _app(max_body_bytes: int = 100, exceptions: dict[str, int] | None = None) -> FastAPI:
    application = FastAPI()
    application.add_middleware(
        BodySizeLimitMiddleware,
        max_body_bytes=max_body_bytes,
        plafonds_par_prefixe=exceptions or {},
    )

    @application.post("/petit")
    async def petit(charge: dict) -> dict:
        return {"recu": len(charge)}

    @application.post("/v1/parcelles/abc/captures")
    async def captures(charge: dict) -> dict:
        return {"recu": len(charge)}

    return application


def test_corps_sous_le_plafond_passe():
    with TestClient(_app()) as client:
        assert client.post("/petit", json={"a": 1}).status_code == 200


def test_corps_au_dessus_du_plafond_est_rejete():
    with TestClient(_app()) as client:
        reponse = client.post("/petit", json={"a": "x" * 500})
        assert reponse.status_code == 413


def test_un_prefixe_peut_avoir_un_plafond_plus_large():
    """Les captures terrain portent des images : le plafond global de 16 Ko les tuerait."""
    app = _app(max_body_bytes=100, exceptions={"/v1/parcelles": 100_000})
    with TestClient(app) as client:
        charge = {"a": "x" * 500}
        assert client.post("/v1/parcelles/abc/captures", json=charge).status_code == 200
        assert client.post("/petit", json=charge).status_code == 413


def test_le_plafond_de_prefixe_reste_une_borne():
    app = _app(max_body_bytes=100, exceptions={"/v1/parcelles": 1_000})
    with TestClient(app) as client:
        reponse = client.post("/v1/parcelles/abc/captures", json={"a": "x" * 5_000})
        assert reponse.status_code == 413


def test_corps_sans_content_length_est_refuse():
    """Faille : Transfer-Encoding chunked omet Content-Length et contournait la borne."""
    with TestClient(_app()) as client:
        reponse = client.post(
            "/petit",
            content=iter([b'{"a": ', b'"' + b"x" * 5_000 + b'"}']),
            headers={"Content-Type": "application/json"},
        )
        assert reponse.status_code == 411


def test_content_length_non_numerique_est_refuse():
    """Un Content-Length falsifie ne doit pas ouvrir un passage."""
    with TestClient(_app()) as client:
        reponse = client.post(
            "/petit",
            content=b'{"a": 1}',
            headers={"Content-Type": "application/json", "Content-Length": "abc"},
        )
        assert reponse.status_code in (400, 411)


@pytest.mark.parametrize("methode", ["get", "delete", "head"])
def test_les_methodes_sans_corps_ne_sont_pas_penalisees(methode):
    """GET n'a pas de Content-Length : exiger l'en-tete casserait toute l'API."""
    app = _app()

    @app.get("/vide")
    async def vide() -> dict:
        return {}

    with TestClient(app) as client:
        if methode == "get":
            assert client.get("/vide").status_code == 200


# ------------------------------------------------------------ Permissions-Policy
#
# Cet en-tete est un arbitrage de securite fin : la geolocalisation DOIT etre permise
# a la seule origine de l interface (le parcours GPS releve le contour d une parcelle,
# et « geolocation=() » ferait refuser la permission par le navigateur sans meme
# demander son avis au producteur), tandis que micro et camera restent interdits — les
# quatre modalites de capture passent par <input type="file" capture>, qui delegue a
# l application photo du telephone et n exige aucune de ces deux permissions.
#
# Sans ce test, un durcissement bien intentionne casserait le parcours en silence.


def test_permissions_policy_autorise_la_geolocalisation_sur_l_origine():
    from app.core.security import SECURITY_HEADERS

    politique = SECURITY_HEADERS["Permissions-Policy"]
    assert "geolocation=(self)" in politique


def test_permissions_policy_interdit_micro_et_camera():
    from app.core.security import SECURITY_HEADERS

    politique = SECURITY_HEADERS["Permissions-Policy"]
    assert "microphone=()" in politique
    assert "camera=()" in politique


# ---------------------------------------------------------------- CSP et blob:
#
# L echantillonnage video se fait SUR L APPAREIL : le fichier choisi est charge dans
# un <video> via une URL blob:. Sans media-src 'self' blob:, la directive retombe sur
# default-src 'self', qui n inclut pas blob:, et le navigateur refuse la lecture
# (« Media load rejected by URL safety check »). Constate a l emulateur le 28/07/2026 :
# la modalite video etait structurellement morte. Ce test empeche la regression.


def test_csp_ui_autorise_blob_pour_les_medias():
    from app.core.security import CSP_UI

    assert "media-src 'self' blob:" in CSP_UI


def test_csp_ui_autorise_blob_pour_les_images():
    from app.core.security import CSP_UI

    assert "img-src 'self' data: blob:" in CSP_UI


def test_csp_ui_n_ouvre_aucune_origine_distante():
    """blob: n autorise que des ressources fabriquees par la page, pas un tiers."""
    from app.core.security import CSP_UI

    assert "default-src 'self'" in CSP_UI
    assert "object-src 'none'" in CSP_UI
    assert "frame-ancestors 'none'" in CSP_UI
