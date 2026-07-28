"""Tests des types de domaine de la parcelle."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models.parcelle import (
    CaptureRequest,
    Coordonnee,
    CreerParcelleRequest,
    Geometrie,
    ImageRequest,
    Modalite,
    MotifRecevabilite,
    Recevabilite,
    SourceGeometrie,
    TypeGeometrie,
)


def test_coordonnee_est_immuable():
    point = Coordonnee(latitude=6.85, longitude=-5.28)
    with pytest.raises(dataclasses.FrozenInstanceError):
        point.latitude = 7.0  # type: ignore[misc]


def test_geometrie_polygone_calcule_sa_superficie():
    cote = 0.000899
    lat, lon = 6.85, -5.28
    points = tuple(
        Coordonnee(latitude=a, longitude=b)
        for a, b in [
            (lat, lon),
            (lat, lon + cote),
            (lat + cote, lon + cote),
            (lat + cote, lon),
        ]
    )
    geometrie = Geometrie.depuis_points(points, source=SourceGeometrie.PARCOURS_GPS)
    assert geometrie.type is TypeGeometrie.POLYGONE
    assert geometrie.superficie_ha == pytest.approx(1.0, abs=0.05)


def test_geometrie_point_unique_na_pas_de_superficie():
    geometrie = Geometrie.depuis_points(
        (Coordonnee(latitude=6.85, longitude=-5.28),),
        source=SourceGeometrie.SAISIE_MANUELLE,
    )
    assert geometrie.type is TypeGeometrie.POINT
    assert geometrie.superficie_ha is None


def test_recevabilite_refusee_porte_un_conseil():
    verdict = Recevabilite(
        recevable=False,
        motif=MotifRecevabilite.FLOU,
        conseil="Approchez-vous de la cabosse et refaites la photo.",
        score_nettete=12.0,
    )
    assert verdict.recevable is False
    assert verdict.conseil


def test_creer_parcelle_request_exige_un_nom():
    with pytest.raises(ValidationError):
        CreerParcelleRequest(nom="", localite="Daloa")


def test_creer_parcelle_request_borne_la_longueur_du_nom():
    with pytest.raises(ValidationError):
        CreerParcelleRequest(nom="x" * 121, localite="Daloa")


def test_image_request_borne_le_score_de_nettete():
    """Le score vient du client : il doit rester dans un intervalle plausible."""
    with pytest.raises(ValidationError):
        ImageRequest(contenu_base64="AAAA", largeur=800, hauteur=600, score_nettete=-1.0)


def test_capture_request_plafonne_le_nombre_d_images():
    images = [
        ImageRequest(contenu_base64="AAAA", largeur=800, hauteur=600, score_nettete=50.0)
        for _ in range(13)
    ]
    with pytest.raises(ValidationError):
        CaptureRequest(modalite=Modalite.PHOTOS, images=images)


def test_capture_request_refuse_une_capture_totalement_vide():
    with pytest.raises(ValidationError):
        CaptureRequest(modalite=Modalite.PHOTOS, images=[], trace=[])


def test_capture_request_accepte_une_trace_seule():
    requete = CaptureRequest(
        modalite=Modalite.PARCOURS,
        images=[],
        trace=[
            {"latitude": 6.85, "longitude": -5.28},
            {"latitude": 6.85, "longitude": -5.27},
            {"latitude": 6.86, "longitude": -5.27},
            {"latitude": 6.86, "longitude": -5.28},
        ],
    )
    assert len(requete.trace) == 4


def test_modalite_couvre_les_quatre_cas():
    assert {m.value for m in Modalite} == {"photos", "video", "parcours", "parcours_video"}


def test_capture_horodatee_conserve_son_instant():
    instant = datetime(2026, 7, 28, 10, 30, tzinfo=UTC)
    point = Coordonnee(latitude=6.85, longitude=-5.28, horodatage=instant)
    assert point.horodatage == instant
