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

import json
import re
import unicodedata
from enum import Enum
from functools import lru_cache
from pathlib import Path

import yaml

from app.core.logging import get_logger

logger = get_logger(__name__)

_CHEMIN = Path(__file__).resolve().parent.parent / "data" / "contacts_zones.yaml"
_CHEMIN_COORDONNEES = Path(__file__).resolve().parent.parent / "data" / "coordonnees_localites.json"

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

# Directions Régionales ANADER dont les zones sont cacaoyères. C'est une décision
# MÉTIER, écrite ici pour être relue : les cinq DR ci-dessous couvrent la « boucle du
# cacao ». Les DR Nord, Nord-Est et Nord-Ouest en sont exclues (savane), et les DR
# Centre et Centre-Est ne figurent NI ici NI dans la deny-list parce qu'elles sont
# réellement mixtes — Katiola et Dabakala y sont refusées de longue date, quand
# Bongouanou et Daoukro sont bien cacaoyères. Une localité de ces DR est donc
# INDÉTERMINÉE : on ne l'affirme pas, on le dit.
DR_CACAOYERES = frozenset(
    {
        "Direction Régionale Centre-Ouest",
        "Direction Régionale Est",
        "Direction Régionale Ouest",
        "Direction Régionale Sud",
        "Direction Régionale Sud-Ouest",
    }
)


class Aptitude(str, Enum):
    """Ce que le dépôt sait de l'aptitude cacaoyère d'une localité citée.

    Trois états et non deux. L'écart du 19/08/2026 vient précisément de ce qu'il n'y
    en avait que deux : une deny-list du Nord, et tout le reste présumé cacaoyer. Le
    modèle affirmait donc que Ouangolo était « dans la zone forestière du Sud ».
    """

    CACAO = "cacao"
    NORD = "nord"
    INDETERMINE = "indetermine"


# Longueur minimale d'un préfixe accepté comme forme courte d'une localité.
# « Ouangolo » (8) pour Ouangolodougou, « Ferkesse » pour Ferkessédougou. En dessous
# de 6, le risque de faux positif sur un mot courant devient réel.
_LONGUEUR_MIN_PREFIXE = 6


def _normaliser(texte: str) -> str:
    """Minuscule + suppression des accents, pour une comparaison robuste."""
    sans_accent = "".join(
        c for c in unicodedata.normalize("NFD", texte) if unicodedata.category(c) != "Mn"
    )
    return sans_accent.lower()


# Tolérance aux fautes de frappe (saisie mobile) : en dessous de 5 lettres, le flou
# créerait des faux positifs (« mon » -> « Man ») — les noms courts restent exacts.
_LONGUEUR_MIN_FLOU = 5


def _quasi_egal(a: str, b: str) -> bool:
    """Distance de Damerau-Levenshtein ≤ 1 (échange, ajout, retrait ou substitution).

    Vécu prod (06/07) : « korohgo » (lettres inversées) échappait à la détection
    exacte — le garde-fou « zone non cacaoyère » ne se déclenchait pas et le modèle
    donnait un conseil cacao pour une ville de savane.
    """
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        differences = [i for i in range(la) if a[i] != b[i]]
        if len(differences) == 1:
            return True  # substitution unique
        return (  # transposition adjacente (korohgo <-> korhogo)
            len(differences) == 2
            and differences[1] == differences[0] + 1
            and a[differences[0]] == b[differences[1]]
            and a[differences[1]] == b[differences[0]]
        )
    court, long_ = (a, b) if la < lb else (b, a)
    i = j = ecarts = 0
    while i < len(court) and j < len(long_):
        if court[i] == long_[j]:
            i += 1
        else:
            ecarts += 1
            if ecarts > 1:
                return False
        j += 1
    return True  # ajout/retrait unique


def _positions_flou(norm: str, cible: str) -> list[int]:
    """Positions des mots du texte quasi-égaux à la cible (mono-mot, ≥ 5 lettres)."""
    if len(cible) < _LONGUEUR_MIN_FLOU or " " in cible:
        return []
    return [m.start() for m in re.finditer(r"\w+", norm) if _quasi_egal(m.group(0), cible)]


@lru_cache(maxsize=1)
def _annuaire() -> dict:
    """Charge l'annuaire YAML (mémoïsé). Renvoie {} si absent/illisible."""
    try:
        return yaml.safe_load(_CHEMIN.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("annuaire_localites_illisible", error=str(exc))
        return {}


@lru_cache(maxsize=1)
def _index() -> list[tuple[re.Pattern, str, str, dict]]:
    """Index ``(regex, libellé normalisé, nom canonique, DR)``, du plus long au plus court.

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
    return [(re.compile(rf"\b{re.escape(n)}\b"), n, canon, dr) for n, canon, dr in paires]


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
    for motif, libelle, canon, _dr in _index():
        if libelle in LOCALITES_NORD:
            continue
        positions = [m.start() for m in motif.finditer(norm)] or _positions_flou(norm, libelle)
        if not positions:
            continue
        cle = (max(positions), len(canon))
        if meilleur is None or cle > meilleur:
            meilleur, resultat = cle, canon
    return resultat


def _prefixe_cite(norm: str, cle: str) -> bool:
    """Vrai si un mot du texte est une forme COURTE de la localité.

    Les producteurs disent « Ouangolo », pas « Ouangolodougou » ; « Ferké » est déjà
    une entrée à part. Sans cette tolérance, la correction « zone non cacaoyère » ne
    se déclenchait pas sur la forme réellement employée — et le modèle affirmait que
    Ouangolo était en zone forestière du Sud (écart du 19/08/2026).

    Args:
        norm: Texte déjà normalisé (minuscule sans accent).
        cle: Clé normalisée de la localité.

    Returns:
        True si un mot du texte, d'au moins six lettres, préfixe cette clé.
    """
    if len(cle) <= _LONGUEUR_MIN_PREFIXE:
        return False
    return any(
        len(mot) >= _LONGUEUR_MIN_PREFIXE and cle.startswith(mot)
        for mot in re.findall(r"\w+", norm)
    )


def detecter_nord(texte: str) -> str | None:
    """Nom d'affichage de la première ville NON cacaoyère du Nord citée, ou ``None``."""
    norm = _normaliser(texte)
    for cle, nom in LOCALITES_NORD.items():
        if (
            re.search(rf"\b{re.escape(cle)}\b", norm)
            or _positions_flou(norm, cle)
            or _prefixe_cite(norm, cle)
        ):
            return nom
    return None


@lru_cache(maxsize=1)
def localites_cacao() -> frozenset[str]:
    """Clés normalisées des localités appartenant à une DR cacaoyère.

    Dérivée de ``contacts_zones.yaml``, seule source de vérité du dépôt sur les zones.
    Une liste saisie à la main dériverait de l'annuaire au premier redécoupage ANADER.

    Returns:
        L'ensemble des clés normalisées (zones et sièges des DR cacaoyères).
    """
    cles: set[str] = set()
    for dr in _annuaire().get("directions_regionales", []):
        if dr.get("nom") not in DR_CACAOYERES:
            continue
        for libelle in {dr.get("siege", ""), *dr.get("zones", [])}:
            if libelle:
                cles.add(_normaliser(libelle))
    return frozenset(cles)


def aptitude_cacao(texte: str) -> tuple[Aptitude, str] | None:
    """Ce que le dépôt sait de l'aptitude cacaoyère de la localité citée.

    L'ordre d'examen n'est pas indifférent : la deny-list du Nord est **curée à la
    main** et prime donc sur le découpage administratif, qui la contredirait parfois
    (Katiola est en DR Centre).

    **Limite assumée** : un village absent du découpage ANADER n'est pas détecté comme
    lieu. Le reconnaître exigerait de la reconnaissance d'entités nommées, que ce
    projet n'embarque pas ; ces cas relèvent de la consigne donnée au modèle.

    Args:
        texte: Texte libre (question, ou fil de conversation).

    Returns:
        ``(Aptitude, nom d'affichage)``, ou ``None`` si aucune localité connue n'est
        citée.
    """
    nord = detecter_nord(texte)
    if nord is not None:
        return (Aptitude.NORD, nord)

    norm = _normaliser(texte)
    cacaoyeres = localites_cacao()
    for motif, libelle, canon, _dr in _index():
        if motif.search(norm) or _positions_flou(norm, libelle):
            return (
                (Aptitude.CACAO, canon) if libelle in cacaoyeres else (Aptitude.INDETERMINE, canon)
            )
    return None


@lru_cache(maxsize=1)
def _coordonnees_table() -> dict[str, tuple[float, float]]:
    """Table statique ``{clé normalisée: (lat, lon)}``. ``{}`` si absente/illisible."""
    try:
        brut = json.loads(_CHEMIN_COORDONNEES.read_text(encoding="utf-8"))
        return {cle: (float(v[0]), float(v[1])) for cle, v in brut.items()}
    except (OSError, ValueError) as exc:
        logger.warning("coordonnees_localites_illisibles", error=str(exc))
        return {}


def coordonnees(nom: str) -> tuple[float, float] | None:
    """Coordonnées ``(lat, lon)`` d'une localité connue, ou ``None``.

    Table générée par ``scripts/gen_coordonnees_localites.py`` (résultats
    IVOIRIENS uniquement — le géocodage live prenait le premier résultat
    mondial). Évite un appel HTTP de géocodage par question météo/satellite.
    """
    return _coordonnees_table().get(_normaliser(nom))


def chercher_zone(texte: str) -> tuple[dict, str] | None:
    """``(dict_DR, libellé normalisé matché)`` de la première zone citée, ou ``None``.

    Inclut TOUTES les zones (Nord compris) : un producteur du Nord garde droit au
    contact ANADER.
    """
    norm = _normaliser(texte)
    for motif, _libelle, _canon, dr in _index():
        m = motif.search(norm)
        if m:
            return dr, m.group(0)
    # Repli flou (faute de frappe) : on renvoie le libellé CANONIQUE, pas la coquille.
    for _motif, libelle, _canon, dr in _index():
        if _positions_flou(norm, libelle):
            return dr, libelle
    return None
