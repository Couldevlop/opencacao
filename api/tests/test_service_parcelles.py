"""Tests du service métier des parcelles."""

from __future__ import annotations

import base64
import struct
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core.parcelles_store import ParcelleStore
from app.models.parcelle import (
    CaptureRequest,
    CoordonneeRequest,
    CreerParcelleRequest,
    GeometrieRequest,
    ImageRequest,
    Modalite,
    MotifRecevabilite,
    SourceGeometrie,
)
from app.services.parcelles import (
    GeometrieInvalide,
    ParcelleIntrouvable,
    ServiceParcelles,
)

DEVICE = "appareil-a"


def _jpeg(largeur: int = 1024, hauteur: int = 768) -> bytes:
    """En-tête JPEG minimal mais valide."""
    soi = b"\xff\xd8"
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
    sof0 = (
        b"\xff\xc0"
        + struct.pack(">H", 17)
        + b"\x08"
        + struct.pack(">HH", hauteur, largeur)
        + b"\x03"
        + b"\x00" * 9
    )
    return soi + app0 + sof0


def _image_request(**surcharges) -> ImageRequest:
    defauts = {
        "contenu_base64": base64.b64encode(_jpeg()).decode("ascii"),
        "largeur": 1024,
        "hauteur": 768,
        "score_nettete": 400.0,
        "luminance_moyenne": 128.0,
    }
    return ImageRequest(**{**defauts, **surcharges})


def _carre() -> list[CoordonneeRequest]:
    cote = 0.000899
    lat, lon = 6.85, -5.28
    return [
        CoordonneeRequest(latitude=a, longitude=b)
        for a, b in [
            (lat, lon),
            (lat, lon + cote),
            (lat + cote, lon + cote),
            (lat + cote, lon),
        ]
    ]


@pytest.fixture
async def service(tmp_path: Path) -> ServiceParcelles:
    store = ParcelleStore(tmp_path / "parcelles.db")
    await store.initialiser()
    return ServiceParcelles(store, dossier_captures=tmp_path / "captures")


async def test_creer_rattache_la_direction_regionale(service: ServiceParcelles):
    parcelle = await service.creer(DEVICE, CreerParcelleRequest(nom="Bloc Est", localite="Daloa"))
    assert parcelle.direction_regionale


async def test_enregistrer_un_carre_calcule_un_hectare(service: ServiceParcelles):
    parcelle = await service.creer(DEVICE, CreerParcelleRequest(nom="Bloc", localite="Daloa"))
    maj = await service.enregistrer_geometrie(
        parcelle.identifiant,
        DEVICE,
        GeometrieRequest(points=_carre(), source=SourceGeometrie.PARCOURS_GPS),
    )
    assert maj.geometrie is not None
    assert maj.geometrie.superficie_ha == pytest.approx(1.0, abs=0.05)


async def test_geometrie_hors_cote_d_ivoire_refusee(service: ServiceParcelles):
    parcelle = await service.creer(DEVICE, CreerParcelleRequest(nom="Bloc", localite="Daloa"))
    paris = [
        CoordonneeRequest(latitude=48.86 + i * 0.001, longitude=2.35 + j * 0.001)
        for i, j in [(0, 0), (0, 1), (1, 1), (1, 0)]
    ]
    with pytest.raises(GeometrieInvalide) as info:
        await service.enregistrer_geometrie(
            parcelle.identifiant, DEVICE, GeometrieRequest(points=paris)
        )
    assert "Côte d'Ivoire" in info.value.motif


async def test_geometrie_auto_intersectee_refusee(service: ServiceParcelles):
    parcelle = await service.creer(DEVICE, CreerParcelleRequest(nom="Bloc", localite="Daloa"))
    huit = [
        CoordonneeRequest(latitude=a, longitude=b)
        for a, b in [(6.85, -5.28), (6.86, -5.27), (6.85, -5.27), (6.86, -5.28)]
    ]
    with pytest.raises(GeometrieInvalide) as info:
        await service.enregistrer_geometrie(
            parcelle.identifiant, DEVICE, GeometrieRequest(points=huit)
        )
    assert "coupe" in info.value.motif.lower()


async def test_superficie_absurde_refusee(service: ServiceParcelles):
    """Un anneau de plusieurs milliers d'hectares n'est pas une parcelle."""
    parcelle = await service.creer(DEVICE, CreerParcelleRequest(nom="Bloc", localite="Daloa"))
    enorme = [
        CoordonneeRequest(latitude=a, longitude=b)
        for a, b in [(6.0, -6.0), (6.0, -5.0), (7.0, -5.0), (7.0, -6.0)]
    ]
    with pytest.raises(GeometrieInvalide) as info:
        await service.enregistrer_geometrie(
            parcelle.identifiant, DEVICE, GeometrieRequest(points=enorme)
        )
    assert "superficie" in info.value.motif.lower()


async def test_geometrie_sur_parcelle_inconnue_leve(service: ServiceParcelles):
    with pytest.raises(ParcelleIntrouvable):
        await service.enregistrer_geometrie(
            "inexistante", DEVICE, GeometrieRequest(points=_carre())
        )


async def test_deposer_une_photo_ecrit_le_fichier_sur_disque(
    service: ServiceParcelles, tmp_path: Path
):
    parcelle = await service.creer(DEVICE, CreerParcelleRequest(nom="Bloc", localite="Daloa"))
    capture = await service.deposer_capture(
        parcelle.identifiant,
        DEVICE,
        CaptureRequest(modalite=Modalite.PHOTOS, images=[_image_request()]),
    )
    assert len(capture.images) == 1
    empreinte = capture.images[0].empreinte_sha256
    assert (tmp_path / "captures" / f"{empreinte}.bin").exists()


async def test_le_nom_de_fichier_vient_de_l_empreinte_jamais_du_client(
    service: ServiceParcelles, tmp_path: Path
):
    """Aucune traversée de chemin possible : le client ne nomme pas le fichier."""
    parcelle = await service.creer(DEVICE, CreerParcelleRequest(nom="Bloc", localite="Daloa"))
    capture = await service.deposer_capture(
        parcelle.identifiant,
        DEVICE,
        CaptureRequest(modalite=Modalite.PHOTOS, images=[_image_request()]),
    )
    empreinte = capture.images[0].empreinte_sha256
    assert len(empreinte) == 64
    fichiers = list((tmp_path / "captures").iterdir())
    assert [f.name for f in fichiers] == [f"{empreinte}.bin"]


async def test_image_floue_persistee_avec_son_verdict(service: ServiceParcelles):
    parcelle = await service.creer(DEVICE, CreerParcelleRequest(nom="Bloc", localite="Daloa"))
    capture = await service.deposer_capture(
        parcelle.identifiant,
        DEVICE,
        CaptureRequest(modalite=Modalite.PHOTOS, images=[_image_request(score_nettete=5.0)]),
    )
    assert capture.images[0].recevabilite.recevable is False
    assert capture.images[0].recevabilite.motif is MotifRecevabilite.FLOU
    assert capture.images[0].recevabilite.conseil


async def test_base64_invalide_refuse_sans_ecrire_de_fichier(
    service: ServiceParcelles, tmp_path: Path
):
    parcelle = await service.creer(DEVICE, CreerParcelleRequest(nom="Bloc", localite="Daloa"))
    capture = await service.deposer_capture(
        parcelle.identifiant,
        DEVICE,
        CaptureRequest(
            modalite=Modalite.PHOTOS,
            images=[_image_request(contenu_base64="!!! pas du base64 !!!")],
        ),
    )
    assert capture.images[0].recevabilite.motif is MotifRecevabilite.FORMAT_REFUSE
    assert not (tmp_path / "captures").exists() or not list((tmp_path / "captures").iterdir())


async def test_image_trop_lourde_refusee(service: ServiceParcelles):
    parcelle = await service.creer(DEVICE, CreerParcelleRequest(nom="Bloc", localite="Daloa"))
    gros = base64.b64encode(_jpeg() + b"\x00" * 4_000_000).decode("ascii")
    capture = await service.deposer_capture(
        parcelle.identifiant,
        DEVICE,
        CaptureRequest(modalite=Modalite.PHOTOS, images=[_image_request(contenu_base64=gros)]),
    )
    assert capture.images[0].recevabilite.motif is MotifRecevabilite.FORMAT_REFUSE


async def test_deposer_une_trace_hors_ci_est_refuse(service: ServiceParcelles):
    parcelle = await service.creer(DEVICE, CreerParcelleRequest(nom="Bloc", localite="Daloa"))
    with pytest.raises(GeometrieInvalide):
        await service.deposer_capture(
            parcelle.identifiant,
            DEVICE,
            CaptureRequest(
                modalite=Modalite.PARCOURS,
                trace=[CoordonneeRequest(latitude=48.86, longitude=2.35)],
            ),
        )


async def test_deposer_sur_parcelle_inconnue_leve(service: ServiceParcelles):
    with pytest.raises(ParcelleIntrouvable):
        await service.deposer_capture(
            "inexistante",
            DEVICE,
            CaptureRequest(modalite=Modalite.PHOTOS, images=[_image_request()]),
        )


async def test_purger_supprime_les_fichiers_des_captures_expirees(
    service: ServiceParcelles, tmp_path: Path
):
    parcelle = await service.creer(DEVICE, CreerParcelleRequest(nom="Bloc", localite="Daloa"))
    capture = await service.deposer_capture(
        parcelle.identifiant,
        DEVICE,
        CaptureRequest(modalite=Modalite.PHOTOS, images=[_image_request()]),
    )
    fichier = tmp_path / "captures" / f"{capture.images[0].empreinte_sha256}.bin"
    assert fichier.exists()
    # On purge « depuis le futur » pour que la capture du jour soit expirée.
    supprimes = await service.purger(maintenant=datetime.now(UTC) + timedelta(days=400))
    assert supprimes == 1
    assert not fichier.exists()
