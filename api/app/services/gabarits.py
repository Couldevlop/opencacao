"""Gabarits déclaratifs des livrables (spec §8.3).

**Ajouter un gabarit est un fichier YAML, pas du code.** Même discipline
d'extensibilité que « ajouter un agent = un adaptateur ». Le chargeur valide ce que
le moteur ne pourra plus corriger ensuite : une section sans titre resterait sans
titre (le modèle n'en émet jamais), une source inconnue serait silencieusement
ignorée à la rédaction.

Le référentiel vit dans ``app/data/gabarits/``, sur le modèle de ``sources_agro.yaml``.
"""

from __future__ import annotations

import string
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

from app.core.logging import get_logger

logger = get_logger(__name__)

_DOSSIER = Path(__file__).resolve().parent.parent / "data" / "gabarits"

# Sources qu'une section peut déclarer. Le moteur sait collecter celles-ci et
# seulement celles-ci ; une valeur hors liste est une faute de gabarit, pas une
# extension silencieuse.
SOURCES_CONNUES = frozenset({"rag", "prix", "meteo", "satellite", "parcelle", "constats"})

# Seul champ substituable dans un titre. Le moteur applique ``str.format`` : tout
# autre champ ouvrirait la marche d'attributs (``{sujet.__class__.__mro__}`` fonctionne)
# ou permettrait un déni de service par spécification de format (``{sujet:>1000000000}``
# produit un milliard de caractères). CWE-134 — on ferme au chargement, une fois.
_CHAMPS_AUTORISES = frozenset({"sujet"})

# Gabarits dont la mention est une obligation métier, pas une décoration. D5 : un
# dossier de parcelle qui perdrait sa mention se lirait comme une attestation de
# conformité. La garantie vit ici, pas seulement dans un test du dépôt : ajouter un
# gabarit est un fichier YAML, et un fichier ajouté hors CI doit buter sur la règle.
_MENTION_OBLIGATOIRE = frozenset({"dossier_parcelle"})


class GabaritInconnu(Exception):
    """Le gabarit demandé n'existe pas."""


class GabaritInvalide(Exception):
    """Le gabarit existe mais ne respecte pas le contrat."""


@dataclass(frozen=True)
class SectionGabarit:
    """Une section déclarée par un gabarit.

    Attributes:
        titre: Titre imposé de la section — le modèle n'en produit jamais.
        sources: Sources à collecter pour cette section.
        consigne: Consigne de rédaction propre à la section.
    """

    titre: str
    sources: tuple[str, ...] = ()
    consigne: str = ""


@dataclass(frozen=True)
class Gabarit:
    """Un gabarit de livrable.

    Attributes:
        identifiant: Identifiant du gabarit (nom du fichier, sans extension).
        titre: Titre du document, pouvant porter ``{sujet}``.
        sous_titre: Sous-titre, pouvant porter ``{sujet}``.
        public: Public visé, pour information.
        mention: Mention non contournable en tête du document (D5), ou vide.
        sections: Sections dans l'ordre de rédaction.
    """

    identifiant: str
    titre: str
    sous_titre: str
    public: str
    mention: str
    sections: tuple[SectionGabarit, ...]


def lister_gabarits() -> tuple[str, ...]:
    """Retourne les identifiants des gabarits disponibles, triés."""
    if not _DOSSIER.is_dir():
        return ()
    return tuple(sorted(chemin.stem for chemin in _DOSSIER.glob("*.yaml")))


def _texte(valeur: object, ou: str) -> str:
    """Rend une valeur de gabarit sous forme de texte, ou refuse.

    ``str(valeur)`` accepterait un dict ou une liste et les stringifierait
    silencieusement — ``titre: {a: 1}`` deviendrait ``"{'a': 1}"``, validé au
    chargement puis explosé en ``KeyError`` au moment du rendu, très loin d'ici.

    Args:
        valeur: Valeur brute issue du YAML.
        ou: Nom du champ, pour le message d'erreur.

    Returns:
        Le texte nettoyé, chaîne vide si la valeur est absente.

    Raises:
        GabaritInvalide: La valeur n'est ni absente, ni un scalaire textuel.
    """
    if valeur is None:
        return ""
    if not isinstance(valeur, str | int | float):
        raise GabaritInvalide(f"{ou} : valeur de type inattendu")
    return str(valeur).strip()


def _verifier_substitution(texte: str, ou: str) -> str:
    """Refuse tout champ de format autre que ``{sujet}`` (CWE-134).

    Args:
        texte: Titre ou sous-titre du gabarit.
        ou: Nom du champ, pour le message d'erreur.

    Returns:
        Le texte inchangé s'il est sûr.

    Raises:
        GabaritInvalide: Accolades déséquilibrées, champ non autorisé (accès
            attribut, indexation, positionnel) ou spécification de format.
    """
    try:
        champs = list(string.Formatter().parse(texte))
    except ValueError as erreur:
        raise GabaritInvalide(f"{ou} : accolades déséquilibrées") from erreur
    for _, champ, spec, _ in champs:
        if champ is None:
            continue
        # « sujet.__class__ », « sujet[0] », « 0 » et « » sont tous hors liste.
        if champ not in _CHAMPS_AUTORISES:
            raise GabaritInvalide(f"{ou} : champ interdit « {champ} »")
        if spec:
            raise GabaritInvalide(f"{ou} : spécification de format interdite")
    return texte


def lire_gabarit(charge: object) -> Gabarit:
    """Valide et construit un gabarit à partir de sa charge YAML.

    Args:
        charge: Contenu YAML désérialisé.

    Returns:
        Le gabarit validé.

    Raises:
        GabaritInvalide: Charge mal typée, titre manquant, aucune section, section
            sans titre, source hors de ``SOURCES_CONNUES``, ou champ de format
            interdit dans un titre.
    """
    if not isinstance(charge, dict):
        raise GabaritInvalide("le gabarit n'est pas un objet YAML")

    titre = _verifier_substitution(_texte(charge.get("titre"), "titre"), "titre")
    if not titre:
        raise GabaritInvalide("titre manquant")
    sections_brutes = charge.get("sections") or []
    if not isinstance(sections_brutes, list) or not sections_brutes:
        raise GabaritInvalide("aucune section")

    sections: list[SectionGabarit] = []
    for brute in sections_brutes:
        if not isinstance(brute, dict):
            raise GabaritInvalide("section mal formée")
        titre_section = _texte(brute.get("titre"), "titre de section")
        if not titre_section:
            raise GabaritInvalide("section sans titre")
        brutes = brute.get("sources") or ()
        # Une chaîne est itérable : « sources: rag » donnerait ('r','a','g'), refusé
        # plus loin mais avec un message incompréhensible. On tranche ici.
        if isinstance(brutes, str) or not isinstance(brutes, list | tuple):
            raise GabaritInvalide("sources doit être une liste")
        sources = tuple(_texte(source, "source") for source in brutes)
        inconnues = set(sources) - SOURCES_CONNUES
        if inconnues:
            raise GabaritInvalide(f"sources inconnues : {', '.join(sorted(inconnues))}")
        sections.append(
            SectionGabarit(
                titre=titre_section,
                sources=sources,
                consigne=_texte(brute.get("consigne"), "consigne"),
            )
        )

    sous_titre = _verifier_substitution(
        _texte(charge.get("sous_titre"), "sous_titre"), "sous_titre"
    )
    return Gabarit(
        identifiant=_texte(charge.get("id"), "id"),
        titre=titre,
        sous_titre=sous_titre,
        public=_texte(charge.get("public"), "public"),
        mention=_texte(charge.get("mention"), "mention"),
        sections=tuple(sections),
    )


def charger_gabarit(identifiant: str) -> Gabarit:
    """Charge un gabarit depuis ``app/data/gabarits``.

    Args:
        identifiant: Identifiant du gabarit (nom de fichier sans extension).

    Returns:
        Le gabarit validé.

    Raises:
        GabaritInconnu: Identifiant absent du dossier des gabarits.
        GabaritInvalide: Le fichier existe mais ne respecte pas le contrat, est
            illisible, ou omet une mention obligatoire (D5).
    """
    # L'identifiant vient d'une requête HTTP : on n'assemble jamais un chemin avec
    # une donnée client, on choisit dans une liste blanche calculée depuis le disque.
    if identifiant not in lister_gabarits():
        raise GabaritInconnu(identifiant)
    chemin = _DOSSIER / f"{identifiant}.yaml"
    try:
        charge = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as erreur:
        raise GabaritInvalide(f"YAML illisible : {identifiant}") from erreur

    gabarit = lire_gabarit(charge)
    if identifiant in _MENTION_OBLIGATOIRE and not gabarit.mention:
        raise GabaritInvalide(f"{identifiant} : mention préparatoire obligatoire (D5)")
    # Le nom de fichier fait foi, pas la clé « id » : un fichier « bulletin.yaml »
    # déclarant « id: etude_filiere » rendrait le manifeste de provenance faux, donc
    # le document non rejouable — ce qui est toute sa raison d'être.
    return replace(gabarit, identifiant=identifiant)
