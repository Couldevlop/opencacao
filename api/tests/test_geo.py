"""Tests de la géolocalisation IP -> pays (dégradation propre sans base)."""

from __future__ import annotations

from pathlib import Path

from app.services.geo import GeoLocalisateur


def test_pays_sans_base(tmp_path: Path) -> None:
    """Sans base GeoLite2, pays() renvoie "" sans jamais lever."""
    geo = GeoLocalisateur(tmp_path / "absente.mmdb")
    assert geo.pays("8.8.8.8") == ""
    assert geo.pays("") == ""
    assert geo.pays("testclient") == ""  # IP invalide tolérée


def test_from_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GEOIP_DB_PATH", str(tmp_path / "x.mmdb"))
    geo = GeoLocalisateur.from_env()
    assert geo.pays("1.2.3.4") == ""  # base absente -> dégradé
