"""Tests de la géométrie partagée (bornes CI, superficie, auto-intersection)."""

from __future__ import annotations

import pytest

from app.services.geometrie import (
    anneau_auto_intersecte,
    dans_cote_ivoire,
    superficie_ha,
)


@pytest.mark.parametrize(
    "latitude,longitude",
    [
        (6.85, -5.28),  # Yamoussoukro
        (5.35, -4.02),  # Abidjan
        (6.13, -6.60),  # Daloa, zone cacaoyère
    ],
)
def test_dans_cote_ivoire_accepte_les_villes_ivoiriennes(latitude, longitude):
    assert dans_cote_ivoire(latitude, longitude) is True


@pytest.mark.parametrize(
    "latitude,longitude",
    [
        (48.86, 2.35),  # Paris
        (5.56, 0.20),  # Accra, Ghana — juste à l'est
        (12.37, -1.52),  # Ouagadougou — au nord
        (0.0, 0.0),  # golfe de Guinée
    ],
)
def test_dans_cote_ivoire_refuse_hors_bornes(latitude, longitude):
    assert dans_cote_ivoire(latitude, longitude) is False


def test_superficie_un_hectare_environ():
    """Un carré de 100 m de côté vaut ~1 ha. 100 m ≈ 0,000899° de latitude."""
    cote = 0.000899
    lat, lon = 6.85, -5.28
    carre = [
        (lat, lon),
        (lat, lon + cote),
        (lat + cote, lon + cote),
        (lat + cote, lon),
    ]
    assert superficie_ha(carre) == pytest.approx(1.0, abs=0.05)


def test_superficie_ignore_le_sens_de_parcours():
    """Un anneau parcouru en sens inverse a la même superficie (valeur absolue)."""
    cote = 0.000899
    lat, lon = 6.85, -5.28
    carre = [
        (lat, lon),
        (lat, lon + cote),
        (lat + cote, lon + cote),
        (lat + cote, lon),
    ]
    assert superficie_ha(carre) == pytest.approx(superficie_ha(list(reversed(carre))))


def test_superficie_nulle_si_moins_de_trois_points():
    assert superficie_ha([(6.85, -5.28), (6.86, -5.28)]) == 0.0


def test_anneau_simple_ne_s_auto_intersecte_pas():
    carre = [(6.85, -5.28), (6.85, -5.27), (6.86, -5.27), (6.86, -5.28)]
    assert anneau_auto_intersecte(carre) is False


def test_anneau_en_huit_s_auto_intersecte():
    """Deux côtés opposés croisés : le tracé se coupe lui-même."""
    huit = [(6.85, -5.28), (6.86, -5.27), (6.85, -5.27), (6.86, -5.28)]
    assert anneau_auto_intersecte(huit) is True


def test_anneau_de_moins_de_quatre_points_ne_s_auto_intersecte_pas():
    assert anneau_auto_intersecte([(6.85, -5.28), (6.86, -5.27)]) is False
