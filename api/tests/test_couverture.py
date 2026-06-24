"""Tests de couverture ciblés : dépendances FastAPI, middlewares, géolocalisation."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api_deps as api_deps
from app.core.security import BodySizeLimitMiddleware, SecurityHeadersMiddleware
from app.services.geo import GeoLocalisateur

# --- api_deps : les getters lisent app.state ---


def _req(state: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(state=state))


def test_getters_app_state() -> None:
    st = SimpleNamespace(inference="INF", cache="CACHE", journal="JOURNAL", rag="RAG")
    req = _req(st)
    assert api_deps.get_inference_client(req) == "INF"
    assert api_deps.get_cache_client(req) == "CACHE"
    assert api_deps.get_journal(req) == "JOURNAL"
    service = api_deps.get_conseil_service(req)
    assert service is not None  # ConseilService construit depuis l'état
    assert api_deps.get_app_settings() is not None


# --- Middlewares de sécurité ---


def test_body_size_limit_et_entetes() -> None:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(BodySizeLimitMiddleware, max_body_bytes=10)

    @app.post("/x")
    async def _x() -> dict[str, bool]:
        return {"ok": True}

    client = TestClient(app)
    assert client.post("/x", content=b"x" * 50).status_code == 413  # trop volumineux
    petit = client.post("/x", content=b"hi")
    assert petit.status_code == 200
    assert petit.headers["x-content-type-options"] == "nosniff"
    assert petit.headers["content-security-policy"]


def test_csp_permissive_pour_html_stricte_pour_json() -> None:
    """La CSP autorise l'UI (HTML) à charger ses ressources, mais reste stricte en API.

    Sans cette distinction, « default-src 'none' » bloquerait CSS/JS/images quand
    l'API sert elle-même l'interface (mode même origine).
    """
    from fastapi.responses import HTMLResponse

    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/page", response_class=HTMLResponse)
    async def _page() -> str:
        return "<!doctype html><title>UI</title>"

    @app.get("/api")
    async def _api() -> dict[str, bool]:
        return {"ok": True}

    client = TestClient(app)
    csp_html = client.get("/page").headers["content-security-policy"]
    assert "script-src 'self'" in csp_html
    assert "style-src 'self'" in csp_html
    assert client.get("/api").headers["content-security-policy"] == (
        "default-src 'none'; frame-ancestors 'none'"
    )


# --- Géolocalisation : lecteur injecté + chargement de base ---


class _FakeReader:
    def get(self, ip: str):
        if ip == "1.2.3.4":
            return {"country": {"iso_code": "CI"}}
        if ip == "mauvaise":
            raise ValueError("ip invalide")
        return None


def test_pays_avec_lecteur(tmp_path: Path) -> None:
    geo = GeoLocalisateur(tmp_path / "x.mmdb")
    geo._reader = _FakeReader()
    geo._tente = True
    assert geo.pays("1.2.3.4") == "CI"
    assert geo.pays("5.5.5.5") == ""  # get() renvoie None
    assert geo.pays("mauvaise") == ""  # ValueError tolérée


def test_pays_charge_la_base(tmp_path: Path, monkeypatch) -> None:
    faux_module = types.SimpleNamespace(open_database=lambda p: _FakeReader())
    monkeypatch.setitem(sys.modules, "maxminddb", faux_module)
    db = tmp_path / "GeoLite2-Country.mmdb"
    db.write_bytes(b"factice")
    geo = GeoLocalisateur(db)
    assert geo.pays("1.2.3.4") == "CI"  # importe maxminddb (fictif), ouvre, interroge
