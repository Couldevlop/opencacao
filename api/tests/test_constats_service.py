"""Tests du service métier du constat visuel (rattachement, idempotence, persistance)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.application.constat_visuel import ServiceConstatVisuel
from app.core.parcelles_store import ParcelleStore
from app.models.parcelle import Capture, Image, Modalite, MotifRecevabilite, Recevabilite
from app.services.constats import CaptureIntrouvable, ServiceConstats, VisionIndisponibleErreur

DEVICE = "appareil-a"
EMPREINTE = "b" * 64


class FauxVision:
    """Port de vision contrôlé par le test (aucun réseau)."""

    def __init__(self, texte: str | None = "Cabosses tachetées de brun sur un tiers.") -> None:
        self.texte = texte
        self.appels = 0

    async def decrire(self, images, consigne):
        self.appels += 1
        return self.texte

    async def disponible(self) -> bool:
        return self.texte is not None


class FausseInference:
    """Port d'inférence contrôlé par le test."""

    async def generer(self, question: str, **_: object) -> str:
        return "Vos cabosses présentent des taches. Montrez ces photos à votre agent ANADER."

    def generer_stream(self, *_: object, **__: object):
        raise NotImplementedError

    async def ready(self) -> bool:
        return True


def _image(recevable: bool = True, empreinte: str = EMPREINTE) -> Image:
    return Image(
        empreinte_sha256=empreinte,
        largeur=1024,
        hauteur=768,
        recevabilite=Recevabilite(
            recevable=recevable,
            motif=MotifRecevabilite.OK if recevable else MotifRecevabilite.FLOU,
            conseil="",
            score_nettete=400.0,
        ),
    )


@pytest.fixture
async def depot(tmp_path: Path) -> ParcelleStore:
    store = ParcelleStore(tmp_path / "parcelles.db")
    await store.initialiser()
    return store


@pytest.fixture
def dossier(tmp_path: Path) -> Path:
    """Dossier des captures, contenant l'image de référence."""
    chemin = tmp_path / "captures"
    chemin.mkdir()
    (chemin / f"{EMPREINTE}.bin").write_bytes(b"\xff\xd8 image")
    return chemin


async def _preparer(depot: ParcelleStore, *images: Image) -> str:
    """Crée une parcelle et y persiste une capture. Retourne l'id de la parcelle."""
    parcelle = await depot.creer_parcelle(DEVICE, "Bloc", "Daloa", "Daloa")
    await depot.enregistrer_capture(
        Capture(
            identifiant="cap1",
            parcelle=parcelle.identifiant,
            proprietaire=DEVICE,
            modalite=Modalite.PHOTOS,
            cree_le=datetime.now(UTC),
            images=images or (_image(),),
        )
    )
    return parcelle.identifiant


def _service(
    depot: ParcelleStore, dossier: Path, vision: FauxVision | None = None
) -> ServiceConstats:
    return ServiceConstats(
        depot,
        ServiceConstatVisuel(vision or FauxVision(), FausseInference()),
        dossier_captures=dossier,
    )


async def test_le_constat_est_produit_puis_persiste(depot: ParcelleStore, dossier: Path):
    parcelle = await _preparer(depot)
    constat = await _service(depot, dossier).produire(parcelle, "cap1", DEVICE)

    assert constat.parcelle == parcelle
    assert constat.capture == "cap1"
    assert constat.proprietaire == DEVICE
    relu = await depot.obtenir_constat(constat.identifiant, DEVICE)
    assert relu is not None
    assert relu.texte == constat.texte


async def test_les_facteurs_de_contexte_sont_persistes(depot: ParcelleStore, dossier: Path):
    """Sans releve de pluie, l etage 4 ecrit pourquoi il ne conforte rien."""
    parcelle = await _preparer(depot)
    constat = await _service(depot, dossier).produire(parcelle, "cap1", DEVICE)

    assert constat.facteurs_contexte
    assert any("Daloa" in facteur for facteur in constat.facteurs_contexte)


async def test_une_seconde_demande_relit_au_lieu_de_recalculer(depot: ParcelleStore, dossier: Path):
    """Idempotence : analyser coute deux generations, on ne les refait pas."""
    parcelle = await _preparer(depot)
    vision = FauxVision()
    service = _service(depot, dossier, vision)

    premier = await service.produire(parcelle, "cap1", DEVICE)
    second = await service.produire(parcelle, "cap1", DEVICE)

    assert second.identifiant == premier.identifiant
    assert vision.appels == 1


async def test_une_capture_d_une_autre_parcelle_est_introuvable(
    depot: ParcelleStore, dossier: Path
):
    await _preparer(depot)
    autre = await depot.creer_parcelle(DEVICE, "Autre", "Daloa", "Daloa")
    with pytest.raises(CaptureIntrouvable):
        await _service(depot, dossier).produire(autre.identifiant, "cap1", DEVICE)


async def test_une_capture_d_un_autre_appareil_est_introuvable(depot: ParcelleStore, dossier: Path):
    parcelle = await _preparer(depot)
    with pytest.raises(CaptureIntrouvable):
        await _service(depot, dossier).produire(parcelle, "cap1", "appareil-b")


async def test_une_capture_sans_image_recevable_est_introuvable(
    depot: ParcelleStore, dossier: Path
):
    """Analyser une image refusee a l etage 0 reviendrait a interpreter du bruit."""
    parcelle = await _preparer(depot, _image(recevable=False))
    with pytest.raises(CaptureIntrouvable):
        await _service(depot, dossier).produire(parcelle, "cap1", DEVICE)


async def test_une_image_absente_du_disque_est_ignoree(depot: ParcelleStore, tmp_path: Path):
    """Le fichier a pu etre purge : on ne fabrique rien a partir de rien."""
    parcelle = await _preparer(depot)
    with pytest.raises(CaptureIntrouvable):
        await _service(depot, tmp_path / "vide").produire(parcelle, "cap1", DEVICE)


async def test_vision_indisponible_leve_une_erreur_orientant_vers_l_anader(
    depot: ParcelleStore, dossier: Path
):
    parcelle = await _preparer(depot)
    service = _service(depot, dossier, FauxVision(texte=None))
    with pytest.raises(VisionIndisponibleErreur) as erreur:
        await service.produire(parcelle, "cap1", DEVICE)
    assert "ANADER" in str(erreur.value)


async def test_un_constat_trop_long_est_abandonne_avant_la_coupure_du_bord(
    depot: ParcelleStore, dossier: Path
):
    """Le constat est SYNCHRONE : il doit promettre moins que ce que l edge tolere.

    REQUEST_TIMEOUT_S vaut 300 s en production (aligne sur l ingress, pour le flux du
    chat), et VISION_TIMEOUT_S 30 : le pire cas atteignait 330 s la ou Cloudflare coupe
    vers 100. Le client voyait un 524 et la generation continuait pour rien. On borne
    donc le constat lui-meme, et on rend la consigne ANADER — le repli deja prevu.
    """
    import asyncio

    class VisionLente:
        async def decrire(self, images, consigne):
            await asyncio.sleep(5)
            return "Une description qui arrive trop tard."

        async def disponible(self) -> bool:
            return True

    parcelle = await _preparer(depot)
    service = ServiceConstats(
        depot,
        ServiceConstatVisuel(VisionLente(), FausseInference()),
        dossier_captures=dossier,
        budget_s=0.2,
    )
    with pytest.raises(VisionIndisponibleErreur) as erreur:
        await service.produire(parcelle, "cap1", DEVICE)
    assert "ANADER" in str(erreur.value)


async def test_un_constat_dans_les_temps_n_est_pas_affecte(depot: ParcelleStore, dossier: Path):
    parcelle = await _preparer(depot)
    service = ServiceConstats(
        depot,
        ServiceConstatVisuel(FauxVision(), FausseInference()),
        dossier_captures=dossier,
        budget_s=30.0,
    )
    constat = await service.produire(parcelle, "cap1", DEVICE)
    assert constat.texte
