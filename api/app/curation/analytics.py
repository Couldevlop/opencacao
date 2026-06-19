"""Agrégation de l'analytique des visites (anonymisée).

Lit le journal des visites (``visites.jsonl`` : horodatage + pays + canal, jamais
d'IP) et produit des compteurs par jour / semaine / mois / année et par pays, pour
le tableau de bord de la console.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path


def _chemin_visites() -> Path:
    """Chemin du journal des visites (volume partagé)."""
    return Path(os.environ.get("DATASET_DIR", "/data")) / "visites.jsonl"


def charger_visites(chemin: Path) -> list[dict]:
    """Lit les visites enregistrées (ignore les lignes vides/corrompues)."""
    if not chemin.exists():
        return []
    visites: list[dict] = []
    for ligne in chemin.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne:
            continue
        try:
            visites.append(json.loads(ligne))
        except json.JSONDecodeError:
            continue
    return visites


def _date(visite: dict) -> datetime | None:
    """Parse l'horodatage ISO d'une visite (UTC), ou None si invalide."""
    try:
        d = datetime.fromisoformat(str(visite.get("ts", "")))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def agreger(visites: list[dict], maintenant: datetime, jours_serie: int = 30) -> dict:
    """Agrège les visites en compteurs par période et par pays.

    Args:
        visites: Enregistrements ``{ts, pays, canal}``.
        maintenant: Instant de référence (UTC) pour les périodes glissantes.
        jours_serie: Profondeur de la série quotidienne (pour le graphique).

    Returns:
        Un dict de compteurs : total, aujourd'hui, semaine, mois, année,
        série par jour, et top pays.
    """
    dates = [d for v in visites if (d := _date(v)) is not None]
    total = len(dates)
    aujourdhui = maintenant.date()
    debut_semaine = maintenant - timedelta(days=7)
    cartes = {
        "total": total,
        "aujourdhui": sum(1 for d in dates if d.date() == aujourdhui),
        "semaine": sum(1 for d in dates if d >= debut_semaine),
        "mois": sum(1 for d in dates if (d.year, d.month) == (maintenant.year, maintenant.month)),
        "annee": sum(1 for d in dates if d.year == maintenant.year),
    }

    # Série quotidienne (N derniers jours), du plus ancien au plus récent.
    par_jour_compteur: Counter = Counter(d.date().isoformat() for d in dates)
    serie = []
    for i in range(jours_serie - 1, -1, -1):
        jour = (aujourdhui - timedelta(days=i)).isoformat()
        serie.append({"date": jour, "n": par_jour_compteur.get(jour, 0)})

    # Répartition par pays (top), puis par mois.
    par_pays = Counter(str(v.get("pays") or "??") for v in visites if _date(v) is not None)
    par_mois = Counter(f"{d.year:04d}-{d.month:02d}" for d in dates)

    return {
        **cartes,
        "par_jour": serie,
        "par_pays": [{"pays": p, "n": n} for p, n in par_pays.most_common(20)],
        "par_mois": [{"mois": m, "n": par_mois[m]} for m in sorted(par_mois)],
    }


def analytique(maintenant: datetime | None = None) -> dict:
    """Charge et agrège les visites (point d'entrée de l'endpoint)."""
    maintenant = maintenant or datetime.now(UTC)
    return agreger(charger_visites(_chemin_visites()), maintenant)
