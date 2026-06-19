"""Tests unitaires de get_client_ip (résolution de l'IP cliente, anti-spoofing)."""

from __future__ import annotations

from dataclasses import dataclass

import app.api_deps as api_deps
from app.api_deps import get_client_ip
from app.core.config import Settings


@dataclass
class _FakeAddr:
    host: str


class _FakeRequest:
    """Requête minimale exposant headers et client."""

    def __init__(self, headers: dict[str, str], host: str | None) -> None:
        self.headers = headers
        self.client = _FakeAddr(host) if host is not None else None


def _patch_settings(monkeypatch, **kwargs) -> None:
    settings = Settings(**kwargs)
    monkeypatch.setattr(api_deps, "get_settings", lambda: settings)


def test_ip_tcp_par_defaut(monkeypatch) -> None:
    """Sans proxy de confiance, on prend l'IP TCP réelle."""
    _patch_settings(monkeypatch, trust_forwarded_for=False)
    req = _FakeRequest({"x-forwarded-for": "9.9.9.9"}, host="10.0.0.1")
    assert get_client_ip(req) == "10.0.0.1"


def test_cf_connecting_ip_prioritaire(monkeypatch) -> None:
    """Derrière Cloudflare, CF-Connecting-IP (IP réelle) prime sur X-Forwarded-For."""
    _patch_settings(monkeypatch, trust_forwarded_for=True)
    req = _FakeRequest(
        {"cf-connecting-ip": "102.0.0.7", "x-forwarded-for": "10.42.0.1"}, host="10.42.0.1"
    )
    assert get_client_ip(req) == "102.0.0.7"


def test_forwarded_for_pris_si_proxy_de_confiance(monkeypatch) -> None:
    """Derrière un proxy de confiance, on lit le premier X-Forwarded-For."""
    _patch_settings(monkeypatch, trust_forwarded_for=True)
    req = _FakeRequest({"x-forwarded-for": "1.1.1.1, 2.2.2.2"}, host="10.0.0.1")
    assert get_client_ip(req) == "1.1.1.1"


def test_forwarded_for_absent_retombe_sur_ip_tcp(monkeypatch) -> None:
    """Proxy de confiance mais en-tête absent : on retombe sur l'IP TCP."""
    _patch_settings(monkeypatch, trust_forwarded_for=True)
    req = _FakeRequest({}, host="10.0.0.1")
    assert get_client_ip(req) == "10.0.0.1"


def test_ip_inconnue_si_pas_de_client(monkeypatch) -> None:
    """Sans information de client TCP, on renvoie 'unknown'."""
    _patch_settings(monkeypatch, trust_forwarded_for=False)
    req = _FakeRequest({}, host=None)
    assert get_client_ip(req) == "unknown"
