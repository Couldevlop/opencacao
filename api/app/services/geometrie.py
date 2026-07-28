"""Géométrie des parcelles — bornes de la Côte d'Ivoire, superficie, validité d'anneau.

Module partagé, sans dépendance externe (ni bibliothèque géospatiale, ni ``numpy``).
Les bornes vivaient auparavant en dur dans ``services/agents/agent_satellite.py`` ;
elles sont désormais ici, comme les localités le sont dans ``services/localites.py``.

Les superficies se calculent sur une **projection équirectangulaire locale** centrée
sur le barycentre de la parcelle : à l'échelle d'une plantation (quelques hectares),
l'erreur est négligeable, et l'on évite l'absurdité d'un calcul en degrés bruts —
un degré de longitude ne vaut pas un degré de latitude.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

# Bornes de l'enveloppe de la Côte d'Ivoire, volontairement larges (le pays s'inscrit
# dans ce rectangle). Un point hors de ces bornes n'est certainement pas ivoirien ;
# un point dedans est plausible, ce qui suffit à écarter les saisies aberrantes.
LAT_MIN = 4.0
LAT_MAX = 11.0
LON_MIN = -9.0
LON_MAX = -2.0

# Rayon moyen de la Terre (IUGG), en mètres.
_RAYON_TERRE_M = 6_371_008.8

_METRES_CARRES_PAR_HECTARE = 10_000.0


def dans_cote_ivoire(latitude: float, longitude: float) -> bool:
    """Indique si un point tombe dans l'enveloppe de la Côte d'Ivoire.

    Args:
        latitude: Latitude en degrés décimaux.
        longitude: Longitude en degrés décimaux.

    Returns:
        ``True`` si le point est plausible en Côte d'Ivoire.
    """
    return LAT_MIN <= latitude <= LAT_MAX and LON_MIN <= longitude <= LON_MAX


def _projeter(points: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """Projette des points (lat, lon) en mètres, autour de leur barycentre."""
    lat_0 = sum(lat for lat, _ in points) / len(points)
    lon_0 = sum(lon for _, lon in points) / len(points)
    facteur = math.pi / 180.0 * _RAYON_TERRE_M
    cos_lat = math.cos(math.radians(lat_0))
    return [((lon - lon_0) * facteur * cos_lat, (lat - lat_0) * facteur) for lat, lon in points]


def superficie_ha(points: Sequence[tuple[float, float]]) -> float:
    """Calcule la superficie d'un anneau fermé, en hectares.

    L'anneau est implicitement fermé (le dernier point est relié au premier). Le sens
    de parcours est indifférent : on retourne une valeur absolue.

    Args:
        points: Sommets ``(latitude, longitude)``, au moins trois.

    Returns:
        La superficie en hectares, ``0.0`` si moins de trois points.
    """
    if len(points) < 3:
        return 0.0
    projetes = _projeter(points)
    somme = 0.0
    for indice, (x_1, y_1) in enumerate(projetes):
        x_2, y_2 = projetes[(indice + 1) % len(projetes)]
        somme += x_1 * y_2 - x_2 * y_1
    return abs(somme) / 2.0 / _METRES_CARRES_PAR_HECTARE


def _segments_se_croisent(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> bool:
    """Indique si les segments [a,b] et [c,d] se croisent proprement."""

    def orientation(
        p: tuple[float, float], q: tuple[float, float], r: tuple[float, float]
    ) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    d_1 = orientation(c, d, a)
    d_2 = orientation(c, d, b)
    d_3 = orientation(a, b, c)
    d_4 = orientation(a, b, d)
    return ((d_1 > 0) != (d_2 > 0)) and ((d_3 > 0) != (d_4 > 0))


def anneau_auto_intersecte(points: Sequence[tuple[float, float]]) -> bool:
    """Indique si le tracé se coupe lui-même.

    Un parcours GPS qui s'auto-intersecte ne délimite pas une parcelle : la superficie
    calculée n'aurait aucun sens. Comparaison exhaustive des paires de côtés — en
    O(n²), acceptable car un parcours de parcelle compte quelques dizaines de points.

    Args:
        points: Sommets ``(latitude, longitude)`` de l'anneau.

    Returns:
        ``True`` si deux côtés non adjacents se croisent.
    """
    if len(points) < 4:
        return False
    projetes = _projeter(points)
    nombre = len(projetes)
    for i in range(nombre):
        a, b = projetes[i], projetes[(i + 1) % nombre]
        for j in range(i + 1, nombre):
            # On saute les côtés adjacents : ils partagent un sommet par construction.
            if j == i or (j + 1) % nombre == i or j == (i + 1) % nombre:
                continue
            c, d = projetes[j], projetes[(j + 1) % nombre]
            if _segments_se_croisent(a, b, c, d):
                return True
    return False
