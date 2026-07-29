"""Chemins de dégradation des parcelles et des outils d'agents.

Ce module verrouille les branches que les tests fonctionnels ne traversent pas :
les **replis**. Deux exigences y sont vérifiées de bout en bout.

1. **Souveraineté des outils** (Météo, Prix, Satellite) — sans donnée, un outil ne
   fabrique rien. Une source injoignable, une clé absente, une réponse d'API
   inexploitable donnent ``{}`` : l'agent dira l'indisponibilité, jamais un statut
   inventé. C'est le pattern « contexte vide -> fabrication » corrigé en v0.6.48.
2. **Tolérance aux pannes des parcelles** — une sonde disque impossible, un dépôt
   non initialisé ou une parcelle disparue en cours d'écriture ne font tomber ni
   l'API ni le chat : on dégrade, ou on refuse avec le bon code HTTP.
"""

from __future__ import annotations

import base64
import shutil
import struct
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api_deps import get_service_parcelles
from app.core.config import Settings, get_settings
from app.core.parcelles_store import ParcelleStore
from app.models.parcelle import (
    CaptureRequest,
    CoordonneeRequest,
    CreerParcelleRequest,
    Geometrie,
    GeometrieRequest,
    ImageRequest,
    Modalite,
    MotifRecevabilite,
    Parcelle,
    SourceGeometrie,
    TypeGeometrie,
)
from app.services import localites
from app.services.outils.meteo_openmeteo import MeteoOpenMeteo
from app.services.outils.prix import OutilPrix
from app.services.outils.satellite import OutilSatellite
from app.services.outils.satellite_gfw import SatelliteGfw
from app.services.parcelles import (
    ParcelleIntrouvable,
    QuotaAppareilDepasse,
    ServiceParcelles,
    StockageIndisponible,
)
from app.services.vision.vlm import ClientVLM

DEVICE = "appareil-a"

_API_GFW = "https://gfw.test/dataset/gfw_integrated_alerts/latest/query/json"
_GEO = "https://geo.test/v1/search"


def _jpeg(largeur: int = 1024, hauteur: int = 768) -> bytes:
    """En-tête JPEG minimal mais valide (mêmes octets que les tests fonctionnels)."""
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


def _image_payload(**surcharges: object) -> dict[str, object]:
    """Charge utile d'une image téléversée, surchargée au besoin."""
    charge: dict[str, object] = {
        "contenu_base64": base64.b64encode(_jpeg()).decode("ascii"),
        "largeur": 1024,
        "hauteur": 768,
        "score_nettete": 400.0,
        "luminance_moyenne": 128.0,
    }
    charge.update(surcharges)
    return charge


def _image_request(**surcharges: object) -> ImageRequest:
    """Image téléversée sous forme de modèle d'entrée, surchargée au besoin."""
    return ImageRequest(**_image_payload(**surcharges))  # type: ignore[arg-type]


@pytest.fixture
async def service(tmp_path: Path) -> ServiceParcelles:
    """Service des parcelles adossé à un dépôt SQLite temporaire."""
    store = ParcelleStore(tmp_path / "parcelles.db")
    await store.initialiser()
    return ServiceParcelles(store, dossier_captures=tmp_path / "captures")


# --------------------------------------------------------------- parcelles (service)


async def test_un_point_unique_est_accepte_sans_controle_d_anneau(service: ServiceParcelles):
    """Un producteur qui pointe sa parcelle à la main n'a pas d'anneau à valider.

    Sous le seuil de quatre sommets, il n'y a pas de contour : la validation ne
    cherche pas d'auto-intersection et la géométrie est enregistrée comme un point,
    sans superficie calculée (on n'invente pas d'hectares).
    """
    parcelle = await service.creer(DEVICE, CreerParcelleRequest(nom="Bloc", localite="Daloa"))
    maj = await service.enregistrer_geometrie(
        parcelle.identifiant,
        DEVICE,
        GeometrieRequest(
            points=[CoordonneeRequest(latitude=6.85, longitude=-5.28)],
            source=SourceGeometrie.SAISIE_MANUELLE,
        ),
    )
    assert maj.geometrie is not None
    assert maj.geometrie.type is TypeGeometrie.POINT
    assert maj.geometrie.superficie_ha is None


async def test_parcelle_disparue_pendant_l_enregistrement_leve_introuvable(tmp_path: Path):
    """Course entre la vérification et l'écriture : on refuse, on ne rend pas ``None``.

    La parcelle existe au contrôle d'appartenance puis disparaît (purge, autre
    appareil) avant l'écriture. Le service doit lever ``ParcelleIntrouvable`` — que
    le routeur traduit en 404 — plutôt que de laisser filer une parcelle absente.
    """

    class DepotVolatil:
        """Dépôt qui perd la parcelle juste après l'avoir montrée."""

        def __init__(self) -> None:
            self.pret = True

        async def obtenir_parcelle(self, identifiant: str, proprietaire: str) -> Parcelle:
            maintenant = datetime.now(UTC)
            return Parcelle(
                identifiant=identifiant,
                proprietaire=proprietaire,
                nom="Bloc",
                localite="Daloa",
                direction_regionale="Daloa",
                cree_le=maintenant,
                maj_le=maintenant,
            )

        async def enregistrer_geometrie(
            self, identifiant: str, proprietaire: str, geometrie: Geometrie
        ) -> Parcelle | None:
            return None

    service_volatil = ServiceParcelles(
        DepotVolatil(),  # type: ignore[arg-type]
        dossier_captures=tmp_path / "captures",
    )
    with pytest.raises(ParcelleIntrouvable):
        await service_volatil.enregistrer_geometrie(
            "p-1",
            DEVICE,
            GeometrieRequest(points=[CoordonneeRequest(latitude=6.85, longitude=-5.28)]),
        )


async def test_fichier_non_image_refuse_sans_ecrire_sur_disque(
    service: ServiceParcelles, tmp_path: Path
):
    """Un fichier au format inconnu est tracé en métadonnées, jamais écrit sur disque.

    Base64 valide, taille raisonnable, mais ce ne sont pas des octets d'image : le
    verdict de recevabilité refuse le format et le contenu ne touche pas le volume.
    Le producteur voit quand même le refus et son conseil de reprise.
    """
    gif = base64.b64encode(b"GIF89a" + b"\x00" * 64).decode("ascii")
    parcelle = await service.creer(DEVICE, CreerParcelleRequest(nom="Bloc", localite="Daloa"))
    capture = await service.deposer_capture(
        parcelle.identifiant,
        DEVICE,
        CaptureRequest(modalite=Modalite.PHOTOS, images=[_image_request(contenu_base64=gif)]),
    )
    image = capture.images[0]
    assert image.recevabilite.motif is MotifRecevabilite.FORMAT_REFUSE
    assert image.empreinte_sha256 == ""
    assert image.recevabilite.conseil
    dossier = tmp_path / "captures"
    assert not dossier.exists() or not list(dossier.iterdir())


async def test_sonde_disque_impossible_ne_bloque_pas_le_depot(
    service: ServiceParcelles, monkeypatch: pytest.MonkeyPatch
):
    """Un volume qui ne répond pas à la sonde ne doit pas refuser la capture.

    L'espace libre est une précaution, pas une condition d'acceptation : si
    ``disk_usage`` échoue (volume absent, montage exotique), on journalise et on
    laisse passer plutôt que de bloquer un producteur sur une sonde.
    """

    def _sonde_cassee(_: object) -> None:
        raise OSError("volume introuvable")

    monkeypatch.setattr(shutil, "disk_usage", _sonde_cassee)
    parcelle = await service.creer(DEVICE, CreerParcelleRequest(nom="Bloc", localite="Daloa"))
    capture = await service.deposer_capture(
        parcelle.identifiant,
        DEVICE,
        CaptureRequest(modalite=Modalite.PHOTOS, images=[_image_request()]),
    )
    assert capture.images[0].recevabilite.recevable is True


async def test_purge_ignore_les_images_refusees_sans_empreinte(
    service: ServiceParcelles, tmp_path: Path
):
    """Une image refusée n'a pas de fichier : la purge ne doit pas tenter de l'effacer.

    Son enregistrement porte une empreinte vide ; sans garde, la purge viserait un
    fichier « .bin » à la racine du dossier de captures.
    """
    parcelle = await service.creer(DEVICE, CreerParcelleRequest(nom="Bloc", localite="Daloa"))
    await service.deposer_capture(
        parcelle.identifiant,
        DEVICE,
        CaptureRequest(
            modalite=Modalite.PHOTOS,
            images=[_image_request(contenu_base64="!!! pas du base64 !!!")],
        ),
    )
    supprimes = await service.purger(maintenant=datetime.now(UTC) + timedelta(days=400))
    assert supprimes == 0
    assert not (tmp_path / "captures" / ".bin").exists()


# ------------------------------------------------------------------ parcelles (dépôt)


async def test_compter_captures_sur_depot_non_pret_rend_zero(tmp_path: Path):
    """Dépôt non initialisé : le comptage rend 0 au lieu de lever.

    Contrat de tolérance aux pannes : ``/data`` inaccessible rend les parcelles
    indisponibles, il ne fait pas tomber l'API. Le quota est alors permissif — un
    dépôt en panne n'écrira de toute façon rien.
    """
    impasse = tmp_path / "fichier"
    impasse.write_text("je ne suis pas un dossier", encoding="utf-8")
    depot = ParcelleStore(impasse / "parcelles.db")
    await depot.initialiser()
    assert depot.pret is False
    assert await depot.compter_captures(DEVICE) == 0


# ---------------------------------------------------------------- parcelles (routeur)


@pytest.fixture
def client_parcelles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Application de test avec les parcelles activées sur un volume temporaire."""
    monkeypatch.setenv("PREWARM_ENABLED", "false")
    monkeypatch.setenv("SESSIONS_DB_PATH", str(tmp_path / "sessions.db"))
    monkeypatch.setenv("PARCELLES_ENABLED", "true")
    monkeypatch.setenv("PARCELLES_DB_PATH", str(tmp_path / "parcelles.db"))
    monkeypatch.setenv("CAPTURES_DIR", str(tmp_path / "captures"))
    from app.main import create_app

    get_settings.cache_clear()
    app = create_app()
    with TestClient(app) as client_test:
        yield client_test
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def _service_qui_leve(exception: Exception) -> SimpleNamespace:
    """Service de parcelles factice dont le dépôt de capture échoue toujours."""

    async def _deposer_capture(*_: object, **__: object) -> None:
        raise exception

    return SimpleNamespace(deposer_capture=_deposer_capture)


def _deposer(client: TestClient) -> httpx.Response:
    """Poste une capture d'une image sur une parcelle quelconque."""
    return client.post(
        "/v1/parcelles/p-1/captures",
        json={"modalite": "photos", "images": [_image_payload()]},
        headers={"X-Device-Id": DEVICE},
    )


def test_quota_d_appareil_atteint_repond_429(client_parcelles: TestClient):
    """Quota d'appareil : faute du client, donc 429 — et le motif reste lisible.

    Le service ne connaît pas HTTP ; c'est le routeur qui distingue les deux causes
    de refus, et il ne doit pas les confondre.
    """
    client_parcelles.app.dependency_overrides[get_service_parcelles] = lambda: _service_qui_leve(
        QuotaAppareilDepasse("Vous avez atteint le nombre maximal de captures enregistrées.")
    )
    reponse = _deposer(client_parcelles)
    assert reponse.status_code == 429
    assert "captures" in reponse.json()["detail"]


def test_stockage_insuffisant_repond_507(client_parcelles: TestClient):
    """Volume presque plein : condition serveur, donc 507 et non 429.

    Imputer au producteur un disque plein serait faux — et lui conseiller de
    supprimer ses captures ne réglerait rien.
    """
    client_parcelles.app.dependency_overrides[get_service_parcelles] = lambda: _service_qui_leve(
        StockageIndisponible("L'espace de stockage est insuffisant. Réessayez plus tard.")
    )
    reponse = _deposer(client_parcelles)
    assert reponse.status_code == 507
    assert "stockage" in reponse.json()["detail"].lower()


# ------------------------------------------------------------------- vision (client)


class _SettingsAvecVision(Settings):
    """Réglages augmentés des paramètres de vision, pas encore déclarés dans ``Settings``.

    ``ClientVLM.from_settings`` lit ``vision_url``/``vision_modele``/``vision_timeout_s``,
    que ``app.core.config.Settings`` n'expose pas encore (ils arriveront avec le câblage
    du service de vision). On ne les invente pas dans le code de production : on fige ici
    le contrat que ce câblage devra honorer.
    """

    vision_url: str = "http://vision:8002"
    vision_modele: str = "opencacao-vision"
    vision_timeout_s: float = 42.0


async def test_from_settings_cable_le_client_sur_les_reglages_de_vision():
    """Le client de vision se construit depuis la configuration, sans valeur en dur."""
    client = ClientVLM.from_settings(_SettingsAvecVision())
    try:
        assert str(client._client.base_url) == "http://vision:8002"
        assert client._modele == "opencacao-vision"
        assert client._client.timeout.read == 42.0
    finally:
        await client.close()


# ---------------------------------------------------------------------- outil prix


async def test_source_de_prix_en_panne_ne_fabrique_aucun_prix():
    """Une source de prix injoignable rend ``{}`` : l'agent dégrade, il n'invente pas.

    Le prix officiel est une donnée autoritaire (bug 850-vs-1200 corrigé en v0.6.47) :
    en son absence, l'outil doit rendre un contexte vide, jamais un cours plausible.
    """

    class SourceEnPanne:
        async def cours(self) -> dict[str, object]:
            raise httpx.ConnectError("source de prix injoignable")

    assert await OutilPrix(SourceEnPanne()).invoquer() == {}


# --------------------------------------------------------------------- outil météo


def _client_mock(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    """Client httpx branché sur un transport simulé (aucun appel réseau)."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _mocker_client_httpx(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> list[httpx.AsyncClient]:
    """Remplace ``httpx.AsyncClient`` par une fabrique branchée sur un transport simulé.

    Retourne la liste des clients construits, pour vérifier qu'ils sont bien refermés.
    """
    crees: list[httpx.AsyncClient] = []
    vrai_client = httpx.AsyncClient

    def _fabrique(**kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        client = vrai_client(transport=httpx.MockTransport(handler), **kwargs)  # type: ignore[arg-type]
        crees.append(client)
        return client

    monkeypatch.setattr(httpx, "AsyncClient", _fabrique)
    return crees


async def test_meteo_sans_client_injecte_ouvre_et_referme_le_sien(
    monkeypatch: pytest.MonkeyPatch,
):
    """Sans client injecté, la source ouvre le sien et le referme : aucune fuite.

    L'orchestrateur est construit à chaque requête ; un client laissé ouvert
    fuirait des sockets à chaque question posée.
    """

    def _repondre(requete: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"daily": {"precipitation_sum": [4.0]}})

    crees = _mocker_client_httpx(monkeypatch, _repondre)
    previsions = await MeteoOpenMeteo().previsions("Daloa")
    assert previsions["pluie_mm_24h"] == 4.0
    assert crees and all(client.is_closed for client in crees)


async def test_meteo_localite_hors_table_geocodee_en_direct():
    """Une localité absente de la table statique est géocodée, puis interrogée à ce point.

    Le repli doit utiliser les coordonnées retournées par le géocodage — pas une
    valeur par défaut, qui donnerait la météo d'un autre endroit.
    """
    inconnue = "Villagedeloin"
    assert localites.coordonnees(inconnue) is None, "la localité doit être hors table statique"
    vues: list[dict[str, str]] = []

    def _repondre(requete: httpx.Request) -> httpx.Response:
        vues.append(dict(requete.url.params))
        if "search" in requete.url.path:
            return httpx.Response(200, json={"results": [{"latitude": 5.78, "longitude": -6.59}]})
        return httpx.Response(200, json={"daily": {"precipitation_sum": [11.0]}})

    meteo = MeteoOpenMeteo(client=_client_mock(_repondre))
    previsions = await meteo.previsions(inconnue)
    assert previsions["resume"] == "fortes pluies attendues"
    assert vues[-1]["latitude"] == "5.78"
    assert vues[-1]["longitude"] == "-6.59"


# ----------------------------------------------------------------- outil satellite


def _gfw(handler: Callable[[httpx.Request], httpx.Response]) -> SatelliteGfw:
    """Adaptateur GFW branché sur un transport simulé, avec une clé configurée."""
    return SatelliteGfw(
        cle="cle-test", client=_client_mock(handler), api_url=_API_GFW, geocoding_url=_GEO
    )


async def test_gfw_sans_client_injecte_ouvre_et_referme_le_sien(monkeypatch: pytest.MonkeyPatch):
    """Sans client injecté, l'adaptateur GFW ouvre le sien et le referme.

    Il doit aussi suivre les redirections : l'endpoint ``latest`` répond 307.
    """

    def _repondre(requete: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"count": 0}]})

    crees = _mocker_client_httpx(monkeypatch, _repondre)
    gfw = SatelliteGfw(cle="cle-test", api_url=_API_GFW, geocoding_url=_GEO)
    resultat = await gfw.alertes(lat=5.72, lon=-6.68)
    assert resultat["alertes_depuis_2021"] == 0
    assert crees and all(client.is_closed for client in crees)
    assert crees[0].follow_redirects is True


async def test_gfw_sans_localite_ni_coordonnees_n_appelle_rien():
    """Aucune position : aucun appel réseau et aucun statut de déforestation.

    On ne consomme pas le quota de la clé pour une question sans lieu, et l'agent
    ne reçoit rien à interpréter plutôt qu'un « aucune alerte » non localisé.
    """

    def _interdit(requete: httpx.Request) -> httpx.Response:
        raise AssertionError("aucun appel réseau attendu sans position")

    assert await _gfw(_interdit).alertes(localite="   ") == {}


async def test_gfw_reponse_sans_donnee_ne_produit_aucun_statut():
    """Réponse vide de GFW : ``{}``, jamais un « zéro alerte » fabriqué.

    Une absence de donnée n'est pas une absence de déforestation — et OpenCacao ne
    certifie jamais la conformité EUDR d'une parcelle.
    """
    gfw = _gfw(lambda requete: httpx.Response(200, json={"data": []}))
    assert await gfw.alertes(lat=5.72, lon=-6.68) == {}


async def test_gfw_colonne_de_comptage_absente_ne_produit_aucun_statut():
    """Schéma inattendu (alias SQL ignoré côté GFW) : on rend ``{}`` sans deviner.

    Le champ de comptage est nommé ``count`` par l'API ; s'il change, on préfère
    l'indisponibilité à une lecture au hasard d'une autre colonne.
    """
    gfw = _gfw(lambda requete: httpx.Response(200, json={"data": [{"total_alertes": 216}]}))
    assert await gfw.alertes(lat=5.72, lon=-6.68) == {}


async def test_satellite_sans_position_ne_fabrique_aucune_cle_de_cache():
    """Sans localité ni coordonnées, rien n'est lu ni écrit dans le cache d'outil.

    Un résultat non localisé mis en cache serait resservi à une autre parcelle :
    le cache est indexé par position, ou n'est pas utilisé.
    """

    class CacheEspion:
        def __init__(self) -> None:
            self.lectures: list[str] = []
            self.ecritures: list[str] = []

        async def get_outil(self, cle: str) -> str | None:
            self.lectures.append(cle)
            return None

        async def set_outil(self, cle: str, valeur: str, ttl_s: int) -> None:
            self.ecritures.append(cle)

    class SourceMuette:
        async def alertes(self, **_: object) -> dict[str, object]:
            return {}

    cache = CacheEspion()
    outil = OutilSatellite(SourceMuette(), cache=cache)  # type: ignore[arg-type]
    assert await outil.invoquer() == {}
    assert cache.lectures == []
    assert cache.ecritures == []
