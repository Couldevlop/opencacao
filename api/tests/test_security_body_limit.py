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
