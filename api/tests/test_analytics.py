"""Tests de l'agrégation analytique des visites."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.curation.analytics import agreger, charger_visites


def test_agreger_par_periode_et_pays() -> None:
    maintenant = datetime(2026, 6, 19, 12, 0, tzinfo=UTC)
    visites = [
        {"ts": "2026-06-19T08:00:00+00:00", "pays": "CI"},
        {"ts": "2026-06-19T09:00:00+00:00", "pays": "CI"},
        {"ts": "2026-06-15T09:00:00+00:00", "pays": "FR"},  # semaine + mois + année
        {"ts": "2026-01-01T00:00:00+00:00", "pays": ""},  # année seulement
        {"ts": "2025-12-31T00:00:00+00:00", "pays": "FR"},  # année précédente
        {"ts": "pas une date"},  # ignoré
    ]
    a = agreger(visites, maintenant)
    assert a["total"] == 5  # la ligne sans date valide est ignorée
    assert a["aujourdhui"] == 2
    assert a["semaine"] == 3  # 12 -> 19 juin
    assert a["mois"] == 3  # juin
    assert a["annee"] == 4  # 2026
    pays = {p["pays"]: p["n"] for p in a["par_pays"]}
    assert pays["CI"] == 2
    assert pays["FR"] == 2
    assert pays["??"] == 1  # pays vide -> ??
    assert len(a["par_jour"]) == 30
    assert a["par_jour"][-1] == {"date": "2026-06-19", "n": 2}  # dernier jour = aujourd'hui


def test_charger_visites(tmp_path: Path) -> None:
    f = tmp_path / "visites.jsonl"
    f.write_text('{"ts":"x","pays":"CI"}\n\nligne cassée\n', encoding="utf-8")
    assert len(charger_visites(f)) == 1
    assert charger_visites(tmp_path / "absent.jsonl") == []
