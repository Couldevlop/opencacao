"""Types du document produit par l'atelier de livrables (V3, chantier C3).

**La provenance n'est pas une annexe, c'est une structure.** Une ``Affirmation`` ne
peut pas exister sans porter sa source : le type l'exige, et un test échoue si l'une
d'elles sort du moteur sans en avoir une. C'est ce qui rend le document défendable
devant un auditeur — et rejouable, grâce au ``Manifeste``.

Deux familles, comme ``models/parcelle.py`` et ``models/constat.py`` : types de domaine
immuables (``dataclass(frozen=True)``) et schémas Pydantic d'API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.constat import NiveauConfiance


@dataclass(frozen=True)
class Affirmation:
    """Un énoncé du document, avec ce qui permet de le vérifier.

    Attributes:
        texte: L'énoncé tel qu'il apparaît dans le document.
        source: Nom de la source (« CNRA », « Conseil du Café-Cacao »…). Jamais vide.
        date: Date de la donnée au format ISO, ou chaîne vide si la source n'en porte pas.
        methode: Comment elle a été obtenue (« rag », « outil:prix », « parcelle »).
        confiance: Niveau de confiance déclaré.
        empreinte: Empreinte du passage documentaire d'origine, ou chaîne vide pour
            une source qui n'en a pas (un outil interrogé en direct). C'est elle qui
            rend la sélection documentaire **rejouable** : sans elle, le manifeste
            dirait de quelles sources vient le document, mais pas de quels extraits.
    """

    texte: str
    source: str
    date: str
    methode: str
    confiance: NiveauConfiance
    empreinte: str = ""


@dataclass(frozen=True)
class Section:
    """Une section rédigée du document.

    Attributes:
        titre: Titre de la section, fourni par le gabarit (le modèle n'en émet jamais).
        corps: Prose rédigée, ou constat de lacune si ``lacune`` est vrai.
        affirmations: Affirmations sourcées portées par la section.
        lacune: Vrai si aucune source n'était disponible (D4) — on le dit, on n'estime pas.
    """

    titre: str
    corps: str
    affirmations: tuple[Affirmation, ...] = field(default_factory=tuple)
    lacune: bool = False


@dataclass(frozen=True)
class Tableau:
    """Un tableau de données, rendu tel quel par chaque adaptateur de format."""

    titre: str
    entetes: tuple[str, ...]
    lignes: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class Manifeste:
    """De quoi rejouer le document — la souveraineté rendue vérifiable.

    Attributes:
        modele: Nom du modèle de langage utilisé.
        version_modele: Version du modèle.
        version_app: Version applicative ayant produit le document.
        profil_materiel: « cpu » ou « gpu ».
        genere_le: Horodatage UTC de génération.
        empreinte_demandeur: **Empreinte** du demandeur, jamais son identifiant.
            ``X-Device-Id`` n'est pas un pseudonyme : c'est la seule clé
            d'autorisation des parcelles, captures et constats, et il vaut un
            ``acct_`` + jeton secret quand l'utilisateur est connecté. L'écrire en
            clair dans un fichier remis à un bailleur reviendrait à lui livrer les
            droits du producteur (OWASP API1). On n'en garde qu'une empreinte : deux
            documents d'un même demandeur restent corrélables, le secret ne sort pas.
        documents_rag: Couples ``(source, empreinte)`` des passages mobilisés.
        outils: Couples ``(nom, horodatage ISO)`` des outils appelés.
    """

    modele: str
    version_modele: str
    version_app: str
    profil_materiel: str
    genere_le: datetime
    empreinte_demandeur: str
    documents_rag: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    outils: tuple[tuple[str, str], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Document:
    """Le livrable assemblé, indépendant de tout format de sortie.

    Attributes:
        titre: Titre du document.
        sous_titre: Sous-titre (sujet, parcelle, direction régionale…).
        sections: Sections rédigées, dans l'ordre du gabarit.
        tableaux: Tableaux de données réelles.
        manifeste: Manifeste de génération.
        mention: Mention non contournable affichée en tête (D5), ou chaîne vide.
        resume: Synthèse d'ouverture, rédigée À PARTIR des sections déjà écrites — donc
            sans fait nouveau. Vide pour les livrables courts (bulletin régional), qui
            ne doivent pas afficher une rubrique creuse.
        conclusion: Clôture, construite de la même façon et sous la même contrainte.
    """

    titre: str
    sous_titre: str
    sections: tuple[Section, ...]
    tableaux: tuple[Tableau, ...]
    manifeste: Manifeste
    mention: str = ""
    resume: str = ""
    conclusion: str = ""


# --------------------------------------------------------------- schémas d'API


class SectionReponse(BaseModel):
    """Section exposée au client."""

    titre: str
    corps: str
    lacune: bool


class GabaritReponse(BaseModel):
    """Gabarit exposé au client, plan compris.

    Le plan est envoyé avec le gabarit pour que l'écran puisse afficher le sommaire
    avant toute génération : la structure vient du gabarit, pas du modèle.
    """

    identifiant: str
    titre: str
    public: str
    mention: str
    sections: list[str] = Field(default_factory=list)


class IntentionReponse(BaseModel):
    """Ce que le serveur a compris d'une demande écrite librement.

    ``certaine`` est le seul champ qui décide : à faux, l'écran pose UNE question et
    ne produit rien. ``candidats`` porte les gabarits entre lesquels trancher, avec
    leur titre lisible — un écran ne propose pas « etude_filiere ».
    """

    gabarit: str
    sujet: str
    certaine: bool
    candidats: list[GabaritReponse] = Field(default_factory=list)


class RapportReponse(BaseModel):
    """État d'un rapport exposé au client."""

    identifiant: str
    gabarit: str
    sujet: str
    etat: str
    sections_faites: int
    sections_total: int
    titre: str = ""
    sections: list[SectionReponse] = Field(default_factory=list)
