"""Règles de la revue ANADER des constats visuels (étage 6).

Deux règles, sorties du routeur pour être testables seules et réutilisables par un
export hors ligne :

* **La correction d'un agent passe les mêmes garde-fous que tout le reste.** Elle
  devient une étiquette d'entraînement : une correction qui nomme une maladie ou
  donne un dosage empoisonnerait le jeu de données ivoirien à sa source. Le chemin
  corpus applique déjà ce contrôle (``curation/store.py``) ; il n'y a aucune raison
  que le chemin vision soit plus permissif.
* **L'export est minimisé.** Il porte ce qui est observé, jamais chez qui : ni
  propriétaire, ni parcelle, ni capture, ni coordonnée. Cette garantie vit ici, pas
  dans un helper privé d'adaptateur HTTP.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable, Iterator

from app.domain.ports import ParcelleStorePort
from app.models.constat import Constat
from app.services import guardrails

# Nombre de constats lus par aller-retour pendant l'export. Borne la mémoire de la
# console : la crête ne dépend plus de la taille du jeu de données produit.
TAILLE_PAGE_EXPORT = 200


def verifier_correction(texte: str) -> str | None:
    """Retourne le terme interdit d'une correction d'agent, ou None.

    Args:
        texte: Correction saisie librement par l'agent ANADER.

    Returns:
        Le terme fautif (nom de maladie, produit ou dosage), ou ``None``.
    """
    fautif = guardrails.contient_diagnostic(texte)
    if fautif:
        return fautif
    refus = guardrails.verifier_reponse(texte)
    return refus.categorie.value if refus is not None else None


def lignes_export(constats: Iterable[Constat]) -> Iterator[str]:
    """Produit le JSONL d'entraînement, une ligne par observation.

    Générateur, et non chaîne assemblée : l'export peut porter des dizaines de
    milliers de lignes, que la console (plafonnée à 1 Gio) ne doit pas avoir à tenir
    en mémoire d'un seul tenant.

    Args:
        constats: Constats déjà revus par un agent ANADER.

    Yields:
        Une ligne JSON par observation, saut de ligne compris.
    """
    for constat in constats:
        for observation in constat.observations:
            yield (
                json.dumps(
                    {
                        "empreinte": observation.empreinte_image,
                        "organe": observation.organe.value,
                        "description": observation.description,
                        "etat": constat.etat_revue.value,
                        "correction": constat.correction,
                        "cree_le": constat.cree_le.isoformat(),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


async def exporter(depot: ParcelleStorePort, plafond: int) -> AsyncIterator[str]:
    """Produit le JSONL d'entraînement en lisant le dépôt **page par page**.

    Sérialiser en flux ne suffisait pas : charger d'un coup des milliers de constats,
    chacun portant jusqu'à douze observations, faisait de la lecture elle-même la
    crête mémoire. On lit donc par pages bornées, et rien de plus qu'une page n'existe
    en mémoire à un instant donné.

    Args:
        depot: Port du dépôt de parcelles, qui porte les constats.
        plafond: Nombre maximal de constats parcourus.

    Yields:
        Une ligne JSON par observation, saut de ligne compris.
    """
    parcourus = 0
    while parcourus < plafond:
        page = await depot.lister_constats_revus(
            limite=min(TAILLE_PAGE_EXPORT, plafond - parcourus), decalage=parcourus
        )
        if not page:
            return
        for ligne in lignes_export(page):
            yield ligne
        parcourus += len(page)
