"""Génère la table statique de coordonnées des localités connues (ponctuel).

Géocode via Open-Meteo (la même source que le runtime) toutes les localités de
l'annuaire ANADER (sièges + zones) et de la deny-list Nord, en ne retenant que
les résultats situés en Côte d'Ivoire (``country_code == "CI"``) — le géocodage
live prenait le premier résultat mondial, homonymes étrangers compris.

Usage : python scripts/gen_coordonnees_localites.py
Sortie : api/app/data/coordonnees_localites.json ({clé normalisée: [lat, lon]})
"""

from __future__ import annotations

import json
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE / "api"))

from app.services import localites  # noqa: E402

_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
SORTIE = RACINE / "api" / "app" / "data" / "coordonnees_localites.json"


def _normaliser(texte: str) -> str:
    sans_accent = "".join(
        c for c in unicodedata.normalize("NFD", texte) if unicodedata.category(c) != "Mn"
    )
    return sans_accent.lower()


def _noms_connus() -> list[str]:
    """Sièges + zones de l'annuaire ANADER, plus les villes du Nord."""
    noms: set[str] = set(localites.LOCALITES_NORD.values())
    for dr in localites._annuaire().get("directions_regionales", []):  # noqa: SLF001
        noms.update(n for n in (dr.get("siege", ""), *dr.get("zones", [])) if n)
    return sorted(noms)


def _geocoder_ci(nom: str) -> tuple[float, float] | None:
    """Coordonnées du premier résultat IVOIRIEN, ou None."""
    params = urllib.parse.urlencode({"name": nom, "count": 5, "language": "fr", "format": "json"})
    with urllib.request.urlopen(f"{_GEOCODING_URL}?{params}", timeout=15) as reponse:
        resultats = json.load(reponse).get("results") or []
    for lieu in resultats:
        if lieu.get("country_code") == "CI":
            return round(float(lieu["latitude"]), 4), round(float(lieu["longitude"]), 4)
    return None


def main() -> int:
    table: dict[str, list[float]] = {}
    absents: list[str] = []
    noms = _noms_connus()
    for i, nom in enumerate(noms, 1):
        try:
            point = _geocoder_ci(nom)
        except Exception as exc:  # noqa: BLE001 — script ponctuel, on continue
            print(f"  ! {nom}: {exc}")
            point = None
        if point is None:
            absents.append(nom)
        else:
            table[_normaliser(nom)] = [point[0], point[1]]
        if i % 10 == 0:
            print(f"{i}/{len(noms)}…")
        time.sleep(0.15)  # courtoisie API
    SORTIE.write_text(
        json.dumps(table, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"OK -> {SORTIE} ({len(table)} localités, {len(absents)} absentes: {absents})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
