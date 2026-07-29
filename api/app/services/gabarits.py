"""Gabarits déclaratifs des livrables (spec §8.3).

**Ajouter un gabarit est un fichier YAML, pas du code.** Même discipline
d'extensibilité que « ajouter un agent = un adaptateur ». Le chargeur valide ce que
le moteur ne pourra plus corriger ensuite : une section sans titre resterait sans
titre (le modèle n'en émet jamais), une source inconnue serait silencieusement
ignorée à la rédaction.

Le référentiel vit dans ``app/data/gabarits/``, sur le modèle de ``sources_agro.yaml``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from app.core.logging import get_logger

logger = get_logger(__name__)

_DOSSIER = Path(__file__).resolve().parent.parent / "data" / "gabarits"

# Sources qu'une section peut déclarer. Le moteur sait collecter celles-ci et
# seulement celles-ci ; une valeur hors liste est une faute de gabarit, pas une
# extension silencieuse.
SOURCES_CONNUES = frozenset({"rag", "prix", "meteo", "satellite", "parcelle", "constats"})


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


def lire_gabarit(charge: dict) -> Gabarit:
    """Valide et construit un gabarit à partir de sa charge YAML.

    Args:
        charge: Contenu YAML désérialisé.

    Returns:
        Le gabarit validé.

    Raises:
        GabaritInvalide: Titre manquant, aucune section, section sans titre, ou
            source déclarée hors de ``SOURCES_CONNUES``.
    """
    titre = str(charge.get("titre") or "").strip()
    if not titre:
        raise GabaritInvalide("titre manquant")
    sections_brutes = charge.get("sections") or []
    if not sections_brutes:
        raise GabaritInvalide("aucune section")

    sections: list[SectionGabarit] = []
    for brute in sections_brutes:
        titre_section = str(brute.get("titre") or "").strip()
        if not titre_section:
            raise GabaritInvalide("section sans titre")
        sources = tuple(str(source) for source in brute.get("sources") or ())
        inconnues = set(sources) - SOURCES_CONNUES
        if inconnues:
            raise GabaritInvalide(f"sources inconnues : {', '.join(sorted(inconnues))}")
        sections.append(
            SectionGabarit(
                titre=titre_section,
                sources=sources,
                consigne=str(brute.get("consigne") or "").strip(),
            )
        )

    return Gabarit(
        identifiant=str(charge.get("id") or "").strip(),
        titre=titre,
        sous_titre=str(charge.get("sous_titre") or "").strip(),
        public=str(charge.get("public") or "").strip(),
        mention=str(charge.get("mention") or "").strip(),
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
        GabaritInvalide: Le fichier existe mais ne respecte pas le contrat.
    """
    # L'identifiant vient d'une requête HTTP : on n'assemble jamais un chemin avec
    # une donnée client, on choisit dans une liste blanche calculée depuis le disque.
    if identifiant not in lister_gabarits():
        raise GabaritInconnu(identifiant)
    chemin = _DOSSIER / f"{identifiant}.yaml"
    charge = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    return lire_gabarit(charge)
