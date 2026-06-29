"""Helpers de streaming partagés : garde-fou de sortie phrase par phrase + événements.

Mutualise la mécanique de flux (SSE) entre les chemins V2 et V3 : on n'émet une
phrase que lorsqu'elle est complète ET que le texte cumulé reste sain (le garde-fou
de sortie ne diffuse jamais un dosage). Aucune dépendance framework.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

from app.models.chat import DISCLAIMER
from app.models.domain import Confiance, Langue
from app.services import guardrails

# Fin de phrase suivie d'une espace : ne livrer en flux que des phrases complètes,
# scannées par le garde-fou de sortie AVANT émission.
FIN_PHRASE = re.compile(r"[.!?…](?=\s)")


class FiltreSortie:
    """Filtre de flux : émet des phrases validées par le garde-fou de sortie.

    Accumule les fragments, découpe en phrases complètes et ne livre une phrase que
    si le texte cumulé reste sain. Dès qu'une phrase compromettrait la sortie, le
    filtre s'arrête et lève ``compromis``. Le texte sûr déjà émis est dans ``texte``.
    """

    def __init__(self) -> None:
        self._emis: list[str] = []
        self.compromis = False

    @property
    def texte(self) -> str:
        """Texte sûr émis jusqu'ici."""
        return "".join(self._emis)

    async def diffuser(self, fragments: AsyncIterator[str]) -> AsyncIterator[str]:
        """Consomme un flux de fragments et émet des phrases sûres.

        Args:
            fragments: Flux brut de fragments de texte (sortie d'inférence).

        Yields:
            Les phrases complètes validées par le garde-fou de sortie.
        """
        tampon = ""
        async for delta in fragments:
            tampon += delta
            while (match := FIN_PHRASE.search(tampon)) is not None:
                coupe = match.start() + 1
                phrase, tampon = tampon[:coupe], tampon[coupe:]
                if guardrails.verifier_reponse(self.texte + phrase) is not None:
                    self.compromis = True
                    return
                self._emis.append(phrase)
                yield phrase
        if tampon.strip():
            if guardrails.verifier_reponse(self.texte + tampon) is not None:
                self.compromis = True
                return
            self._emis.append(tampon)
            yield tampon


def evenements_token(texte_base: str, texte_enrichi: str) -> list[dict]:
    """Événements 'token' pour un texte envoyé d'un bloc, + le contact ajouté s'il y en a."""
    evenements = [{"type": "token", "text": texte_base}]
    if texte_enrichi != texte_base and texte_enrichi.startswith(texte_base):
        evenements.append({"type": "token", "text": texte_enrichi[len(texte_base) :]})
    return evenements


async def evenement_final(
    journal: object,
    question: str,
    langue: Langue,
    reponse: str,
    sources: list[str],
    confiance: Confiance,
    *,
    redirection: bool,
) -> dict:
    """Journalise puis construit l'événement terminal du flux (métadonnées)."""
    interaction_id = await journal.enregistrer_interaction(  # type: ignore[attr-defined]
        question, langue.value, reponse, confiance.value, sources, redirection
    )
    return {
        "type": "done",
        "sources": sources,
        "confiance": confiance.value,
        "redirection_anader": redirection,
        "disclaimer": DISCLAIMER,
        "interaction_id": interaction_id,
    }
