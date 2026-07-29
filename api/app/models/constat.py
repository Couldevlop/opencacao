"""Types métier du constat visuel d'une capture de parcelle.

**Constat, pas diagnostic.** Ce module ne porte aucun nom de maladie, aucun produit,
aucune posologie — c'est l'arbitrage D3 du 28/07/2026 rendu structurel : le vocabulaire
lui-même interdit le diagnostic. Un constat décrit ce qui est observé, affiche sa
confiance, et part en revue humaine.

Deux familles, comme dans ``app/models/parcelle.py`` : types de domaine immuables
(``dataclass(frozen=True)``) et schémas Pydantic d'API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from app.models.chat import DISCLAIMER


class Organe(str, Enum):
    """Partie du cacaoyer identifiée sur une image (étage 1 de la cascade)."""

    CABOSSE = "cabosse"
    FEUILLE = "feuille"
    TRONC = "tronc"
    VUE_ENSEMBLE = "vue_ensemble"
    INDETERMINE = "indetermine"


class NiveauConfiance(str, Enum):
    """Confiance déclarée pour une observation ou un constat.

    Ordonné (``rang``) parce que la fusion contextuelle doit pouvoir **dégrader** :
    une observation contredite par la météo descend d'un cran.
    """

    FAIBLE = "faible"
    MOYENNE = "moyenne"
    ELEVEE = "elevee"

    @property
    def rang(self) -> int:
        """Rang ordinal, pour comparer et dégrader."""
        return {"faible": 0, "moyenne": 1, "elevee": 2}[self.value]

    def degrader(self) -> NiveauConfiance:
        """Retourne le niveau immédiatement inférieur (plancher : faible)."""
        return NiveauConfiance.FAIBLE if self.rang <= 1 else NiveauConfiance.MOYENNE


class EtatRevue(str, Enum):
    """Cycle de revue par un agent ANADER (étage 6)."""

    EN_ATTENTE = "en_attente"
    CONFIRME = "confirme"
    CORRIGE = "corrige"
    REJETE = "rejete"


@dataclass(frozen=True)
class Observation:
    """Ce qui est observé sur UNE image, sans interprétation étiologique."""

    organe: Organe
    description: str
    confiance: NiveauConfiance
    empreinte_image: str


@dataclass(frozen=True)
class Constat:
    """Constat visuel d'une capture : observations, texte rédigé, état de revue."""

    identifiant: str
    capture: str
    parcelle: str
    proprietaire: str
    observations: tuple[Observation, ...]
    texte: str
    confiance: NiveauConfiance
    cree_le: datetime
    etat_revue: EtatRevue = EtatRevue.EN_ATTENTE
    revu_par: str = ""
    correction: str = ""
    facteurs_contexte: tuple[str, ...] = field(default_factory=tuple)


# --------------------------------------------------------------- schémas d'API


class ObservationReponse(BaseModel):
    """Observation exposée au client."""

    organe: Organe
    description: str
    confiance: NiveauConfiance
    empreinte_image: str


class ConstatReponse(BaseModel):
    """Constat exposé au client.

    Le ``disclaimer`` est porté par le schéma, pas par la consigne au modèle : un
    constat qui oublierait d'orienter vers l'ANADER n'existe pas, puisque la mention
    est structurelle. Même parti pris que ``ChatResponse``.
    """

    identifiant: str
    capture: str
    parcelle: str
    texte: str
    confiance: NiveauConfiance
    cree_le: datetime
    etat_revue: EtatRevue
    observations: list[ObservationReponse] = Field(default_factory=list)
    facteurs_contexte: list[str] = Field(default_factory=list)
    disclaimer: str = DISCLAIMER


class ConstatRevuReponse(ConstatReponse):
    """Constat vu par la console de revue ANADER, décision de l'agent comprise.

    Schéma distinct du précédent, et non un élargissement : l'identifiant de l'agent
    qui a tranché n'a rien à faire dans la réponse rendue au producteur.
    """

    revu_par: str = ""
    correction: str = ""
