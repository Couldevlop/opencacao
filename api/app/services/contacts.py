"""Annuaire ANADER : mise en relation locale du producteur avec sa zone.

Charge ``app/data/contacts_zones.yaml`` (10 Directions Régionales / 60 zones) et
permet, à partir d'un texte libre (la conversation), de retrouver la Direction
Régionale compétente pour la localité citée, afin d'injecter son **contact exact**.

Principe de vérité (CLAUDE §1.3) : le modèle ne produit JAMAIS un numéro lui-même ;
il demande la ville, et ce module fournit la coordonnée vérifiée correspondante.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from app.core.logging import get_logger

logger = get_logger(__name__)

_CHEMIN = Path(__file__).resolve().parent.parent / "data" / "contacts_zones.yaml"

# Intentions de mise en relation : si l'utilisateur veut un contact/une adresse.
_INTENT_CONTACT = re.compile(
    r"\b(contact(?:er)?|joindre|num[ée]ro|t[ée]l[ée]phone|appeler|adresse|"
    r"o[uù]\s+(?:m['e]\s*adresser|aller|me\s+rendre)|qui\s+contacter|agent\s+anader)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ContactDR:
    """Contact d'une Direction Régionale ANADER (ou du siège)."""

    nom: str
    siege: str
    tel: str
    email: str
    verifie: bool
    zone: str = ""  # zone qui a déclenché la correspondance


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
        logger.warning("annuaire_contacts_illisible", error=str(exc))
        return {}


@lru_cache(maxsize=1)
def _index_zones() -> list[tuple[re.Pattern, dict]]:
    """Construit l'index (regex zone/siège normalisé -> DR), du plus long au plus court.

    Trié par longueur décroissante pour qu'un libellé long (« san pedro ») prime sur
    un libellé court. Le mot-frontière évite les correspondances partielles.
    """
    paires: list[tuple[str, dict]] = []
    for dr in _annuaire().get("directions_regionales", []):
        libelles = {dr.get("siege", ""), *dr.get("zones", [])}
        for libelle in libelles:
            if libelle:
                paires.append((_normaliser(libelle), dr))
    paires.sort(key=lambda p: len(p[0]), reverse=True)
    return [(re.compile(rf"\b{re.escape(norm)}\b"), dr) for norm, dr in paires]


def intention_contact(texte: str) -> bool:
    """Indique si le texte exprime une demande de mise en relation/contact."""
    return _INTENT_CONTACT.search(texte) is not None


def chercher(texte: str) -> ContactDR | None:
    """Retrouve la Direction Régionale compétente pour la localité citée, ou None.

    Args:
        texte: Texte libre (idéalement toute la conversation) où chercher une ville.

    Returns:
        Le contact de la DR correspondante, ou None si aucune localité connue n'apparaît.
    """
    norm = _normaliser(texte)
    for motif, dr in _index_zones():
        m = motif.search(norm)
        if m:
            return ContactDR(
                nom=dr.get("nom", "ANADER"),
                siege=dr.get("siege", ""),
                tel=dr.get("tel", ""),
                email=dr.get("email", ""),
                verifie=bool(dr.get("verifie", False)),
                zone=m.group(0),
            )
    return None


def siege() -> ContactDR | None:
    """Contact du siège national ANADER (repli quand la zone est inconnue)."""
    s = _annuaire().get("siege")
    if not s:
        return None
    return ContactDR(
        nom=s.get("nom", "ANADER — Siège"),
        siege=s.get("adresse", ""),
        tel=s.get("tel", ""),
        email=s.get("email", ""),
        verifie=bool(s.get("verifie", False)),
    )


def formater(contact: ContactDR) -> str:
    """Met en forme un contact pour l'ajouter à une réponse (texte lisible).

    Tant qu'un contact n'est pas vérifié (``verifie: false``), une mention explicite
    l'indique : on ne présente jamais une coordonnée non confirmée comme certaine
    (principe de vérité).
    """
    parties = [f"📍 {contact.nom}"]
    if contact.siege:
        parties[0] += f" (siège : {contact.siege})"
    coordonnees = [c for c in (contact.tel, contact.email) if c]
    if coordonnees:
        parties.append(" · ".join(coordonnees))
    if not contact.verifie:
        parties.append("coordonnées à confirmer auprès de l'ANADER")
    return " — ".join(parties)
