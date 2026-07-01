"""Détection de localités ivoiriennes dans un texte libre.

Brique à responsabilité unique, partagée par plusieurs services :

- l'agent Météo géocode la localité CACAOYÈRE détectée (``detecter``) ;
- l'agent Météo signale une localité NON cacaoyère du Nord (``detecter_nord``) ;
- ``contacts.py`` retrouve la Direction Régionale d'une zone (``chercher_zone``,
  TOUTES zones — un producteur du Nord garde droit au contact ANADER) ;
- ``guardrails.py`` importe la deny-list ``LOCALITES_NORD``.

Source de vérité des zones : ``app/data/contacts_zones.yaml`` (10 DR / 60 zones). La
connaissance « cacaoyère ou non » repose sur la deny-list curée ``LOCALITES_NORD``
(décision métier Waopron) — volontairement NON élargie.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path

import yaml

from app.core.logging import get_logger

logger = get_logger(__name__)

_CHEMIN = Path(__file__).resolve().parent.parent / "data" / "contacts_zones.yaml"

# Villes de savane du Nord, non cacaoyères (climat trop sec / saison des pluies trop
# courte). Deny-list curée — décision métier Waopron, NON élargie. Clé normalisée
# (minuscule sans accent) -> nom d'affichage.
LOCALITES_NORD: dict[str, str] = {
    "korhogo": "Korhogo",
    "katiola": "Katiola",
    "ferkessedougou": "Ferkessédougou",
    "ferke": "Ferké",
    "boundiali": "Boundiali",
    "odienne": "Odienné",
    "tengrela": "Tengréla",
    "bouna": "Bouna",
    "dabakala": "Dabakala",
    "niakaramandougou": "Niakaramandougou",
    "kong": "Kong",
    "minignan": "Minignan",
    "ouangolodougou": "Ouangolodougou",
    "sinematiali": "Sinématiali",
    "kouto": "Kouto",
}


def _normaliser(texte: str) -> str:
    """Minuscule + suppression des accents, pour une comparaison robuste."""
    sans_accent = "".join(
        c for c in unicodedata.normalize("NFD", texte) if unicodedata.category(c) != "Mn"
    )
    return sans_accent.lower()


@lru_cache(maxsize=1)
def _annuaire() -> dict:
    """Charge l'annuaire YAML (mémoïsé). Renvoie {} si absent/illisible."""
    try:
        return yaml.safe_load(_CHEMIN.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("annuaire_localites_illisible", error=str(exc))
        return {}


@lru_cache(maxsize=1)
def _index() -> list[tuple[re.Pattern, str, dict]]:
    """Index ``(regex sur libellé normalisé, nom canonique, DR)``, du plus long au plus court.

    Trié par longueur de libellé décroissante pour qu'un libellé long (« san pedro »)
    prime sur un court. Le mot-frontière évite les correspondances partielles.
    """
    paires: list[tuple[str, str, dict]] = []
    for dr in _annuaire().get("directions_regionales", []):
        libelles = {dr.get("siege", ""), *dr.get("zones", [])}
        for libelle in libelles:
            if libelle:
                paires.append((_normaliser(libelle), libelle, dr))
    paires.sort(key=lambda p: len(p[0]), reverse=True)
    return [(re.compile(rf"\b{re.escape(n)}\b"), canon, dr) for n, canon, dr in paires]


def detecter(texte: str) -> str | None:
    """Nom canonique de la localité CACAOYÈRE la plus RÉCEMMENT citée, ou ``None``.

    Sur un fil de conversation, on privilégie la DERNIÈRE ville citée (le contexte
    courant du producteur) plutôt que le libellé le plus long. Exclut les villes de
    ``LOCALITES_NORD`` (non cacaoyères) : leur prévision n'a pas de sens agronomique.
    À position égale (libellés qui se chevauchent), le libellé le plus long prime.

    Args:
        texte: Texte libre (idéalement tout le fil de conversation).

    Returns:
        Le nom canonique (casse d'origine du YAML), ou ``None``.
    """
    norm = _normaliser(texte)
    meilleur: tuple[int, int] | None = None  # (dernière position, longueur du libellé)
    resultat: str | None = None
    for motif, canon, _dr in _index():
        if _normaliser(canon) in LOCALITES_NORD:
            continue
        positions = [m.start() for m in motif.finditer(norm)]
        if not positions:
            continue
        cle = (max(positions), len(canon))
        if meilleur is None or cle > meilleur:
            meilleur, resultat = cle, canon
    return resultat


def detecter_nord(texte: str) -> str | None:
    """Nom d'affichage de la première ville NON cacaoyère du Nord citée, ou ``None``."""
    norm = _normaliser(texte)
    for cle, nom in LOCALITES_NORD.items():
        if re.search(rf"\b{re.escape(cle)}\b", norm):
            return nom
    return None


def chercher_zone(texte: str) -> tuple[dict, str] | None:
    """``(dict_DR, libellé normalisé matché)`` de la première zone citée, ou ``None``.

    Inclut TOUTES les zones (Nord compris) : un producteur du Nord garde droit au
    contact ANADER.
    """
    norm = _normaliser(texte)
    for motif, _canon, dr in _index():
        m = motif.search(norm)
        if m:
            return dr, m.group(0)
    return None
