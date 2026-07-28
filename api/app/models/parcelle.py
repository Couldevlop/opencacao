"""Types métier de la parcelle cacaoyère et de ses captures terrain.

Deux familles cohabitent, comme dans ``app/models/session.py`` :

* les **types de domaine** (``dataclass(frozen=True)``) — immuables, sans dépendance
  framework, manipulés par les services et le dépôt ;
* les **schémas d'API** (Pydantic v2) — validation des entrées et des sorties HTTP.

L'immuabilité n'est pas un ornement : une capture qui traverse plusieurs couches
(recevabilité, écriture disque, persistance) ne doit pas pouvoir être modifiée en
route par surprise.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from app.services.geometrie import dans_cote_ivoire, superficie_ha

# Bornes de plausibilité d'une parcelle cacaoyère ivoirienne. En dessous de 0,1 ha le
# tracé est une erreur de manipulation ; au-delà de 50 ha ce n'est plus une parcelle
# de producteur mais un domaine, qui relève d'un autre traitement.
SUPERFICIE_MIN_HA = 0.1
SUPERFICIE_MAX_HA = 50.0

# Un anneau exploitable demande au moins quatre sommets distincts (un triangle fermé
# par le parcours GPS est presque toujours un tracé abandonné).
POINTS_MIN_POLYGONE = 4

# Plafond d'images par capture, aligné sur l'échantillonnage vidéo du navigateur
# (1 image / 2 s). Douze vues suffisent à un constat et bornent le téléversement.
IMAGES_MAX_PAR_CAPTURE = 12

# Bornes du score de netteté (variance du laplacien) calculé par le navigateur.
SCORE_NETTETE_MIN = 0.0
SCORE_NETTETE_MAX = 100_000.0

NOM_MAX = 120
LOCALITE_MAX = 120


class TypeGeometrie(str, Enum):
    """Nature géométrique d'une parcelle."""

    POINT = "point"
    POLYGONE = "polygone"


class SourceGeometrie(str, Enum):
    """Provenance de la géométrie."""

    PARCOURS_GPS = "parcours_gps"
    SAISIE_MANUELLE = "saisie_manuelle"


class Modalite(str, Enum):
    """Modalité de capture terrain."""

    PHOTOS = "photos"
    VIDEO = "video"
    PARCOURS = "parcours"
    PARCOURS_VIDEO = "parcours_video"


class MotifRecevabilite(str, Enum):
    """Motif du verdict de recevabilité d'une image."""

    OK = "ok"
    FLOU = "flou"
    SOUS_EXPOSE = "sous_expose"
    SUR_EXPOSE = "sur_expose"
    TROP_PETITE = "trop_petite"
    FORMAT_REFUSE = "format_refuse"


@dataclass(frozen=True)
class Coordonnee:
    """Point géographique horodaté, tel que rendu par le navigateur."""

    latitude: float
    longitude: float
    precision_m: float | None = None
    horodatage: datetime | None = None


@dataclass(frozen=True)
class Geometrie:
    """Géométrie d'une parcelle : un point, ou un anneau et sa superficie."""

    type: TypeGeometrie
    points: tuple[Coordonnee, ...]
    source: SourceGeometrie
    superficie_ha: float | None = None

    @classmethod
    def depuis_points(cls, points: tuple[Coordonnee, ...], source: SourceGeometrie) -> Geometrie:
        """Construit une géométrie et calcule sa superficie si c'est un anneau.

        Args:
            points: Sommets relevés, dans l'ordre du parcours.
            source: Provenance des points.

        Returns:
            Une géométrie de type ``POINT`` si un seul point, ``POLYGONE`` sinon.
        """
        if len(points) < POINTS_MIN_POLYGONE:
            return cls(type=TypeGeometrie.POINT, points=points, source=source)
        surface = superficie_ha([(p.latitude, p.longitude) for p in points])
        return cls(
            type=TypeGeometrie.POLYGONE,
            points=points,
            source=source,
            superficie_ha=surface,
        )


@dataclass(frozen=True)
class Recevabilite:
    """Verdict de recevabilité d'une image, avec conseil de reprise."""

    recevable: bool
    motif: MotifRecevabilite
    conseil: str
    score_nettete: float


@dataclass(frozen=True)
class Image:
    """Image persistée : identifiée par l'empreinte de son contenu."""

    empreinte_sha256: str
    largeur: int
    hauteur: int
    recevabilite: Recevabilite
    coordonnee: Coordonnee | None = None


@dataclass(frozen=True)
class Capture:
    """Une session de capture terrain rattachée à une parcelle."""

    identifiant: str
    parcelle: str
    proprietaire: str
    modalite: Modalite
    cree_le: datetime
    images: tuple[Image, ...] = ()
    trace: tuple[Coordonnee, ...] = ()


@dataclass(frozen=True)
class Parcelle:
    """Une parcelle cacaoyère déclarée par un producteur."""

    identifiant: str
    proprietaire: str
    nom: str
    localite: str
    direction_regionale: str
    cree_le: datetime
    maj_le: datetime
    geometrie: Geometrie | None = None


# --------------------------------------------------------------- schémas d'API


class CoordonneeRequest(BaseModel):
    """Point géographique reçu du navigateur."""

    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    precision_m: float | None = Field(default=None, ge=0.0, le=10_000.0)
    horodatage: datetime | None = None


class ImageRequest(BaseModel):
    """Image téléversée, encodée en base64.

    L'encodage base64 évite la dépendance ``python-multipart``, conformément au choix
    déjà retenu pour la console de curation (``app/curation/models.py``).

    Les métriques ``score_nettete`` et ``luminance_moyenne`` sont calculées par le
    navigateur, qui possède les pixels décodés. Le serveur les borne sans leur faire
    confiance : ce sont des indications de confort, pas un contrôle de sécurité.
    """

    contenu_base64: str = Field(min_length=1)
    largeur: int = Field(ge=1, le=20_000)
    hauteur: int = Field(ge=1, le=20_000)
    score_nettete: float = Field(ge=SCORE_NETTETE_MIN, le=SCORE_NETTETE_MAX)
    luminance_moyenne: float = Field(default=128.0, ge=0.0, le=255.0)
    coordonnee: CoordonneeRequest | None = None


class CaptureRequest(BaseModel):
    """Dépôt d'une capture : des images, une trace, ou les deux."""

    modalite: Modalite
    images: list[ImageRequest] = Field(default_factory=list, max_length=IMAGES_MAX_PAR_CAPTURE)
    trace: list[CoordonneeRequest] = Field(default_factory=list, max_length=2_000)

    @model_validator(mode="after")
    def _au_moins_une_donnee(self) -> CaptureRequest:
        """Refuse une capture qui n'apporte ni image ni trace."""
        if not self.images and not self.trace:
            raise ValueError("Une capture doit contenir au moins une image ou une trace.")
        return self


class GeometrieRequest(BaseModel):
    """Enregistrement d'une géométrie de parcelle (parcours ou saisie)."""

    points: list[CoordonneeRequest] = Field(min_length=1, max_length=2_000)
    source: SourceGeometrie = SourceGeometrie.PARCOURS_GPS


class CreerParcelleRequest(BaseModel):
    """Création d'une parcelle."""

    nom: str = Field(min_length=1, max_length=NOM_MAX)
    localite: str = Field(min_length=1, max_length=LOCALITE_MAX)


class RecevabiliteReponse(BaseModel):
    """Verdict de recevabilité exposé au client."""

    recevable: bool
    motif: MotifRecevabilite
    conseil: str
    score_nettete: float


class ImageReponse(BaseModel):
    """Image telle que persistée, exposée au client."""

    empreinte_sha256: str
    largeur: int
    hauteur: int
    recevabilite: RecevabiliteReponse
    coordonnee: CoordonneeRequest | None = None


class GeometrieReponse(BaseModel):
    """Géométrie exposée au client, superficie comprise."""

    type: TypeGeometrie
    points: list[CoordonneeRequest]
    source: SourceGeometrie
    superficie_ha: float | None = None


class ParcelleReponse(BaseModel):
    """Parcelle exposée au client."""

    identifiant: str
    nom: str
    localite: str
    direction_regionale: str
    cree_le: datetime
    maj_le: datetime
    geometrie: GeometrieReponse | None = None


class CaptureReponse(BaseModel):
    """Capture exposée au client."""

    identifiant: str
    parcelle: str
    modalite: Modalite
    cree_le: datetime
    images: list[ImageReponse] = Field(default_factory=list)
    trace: list[CoordonneeRequest] = Field(default_factory=list)


def point_ivoirien(point: CoordonneeRequest) -> bool:
    """Indique si un point reçu tombe dans l'enveloppe de la Côte d'Ivoire."""
    return dans_cote_ivoire(point.latitude, point.longitude)
