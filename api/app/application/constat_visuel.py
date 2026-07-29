"""Étage 5 de la cascade — assemble le constat visuel destiné au producteur.

Enchaîne : description par le modèle de vision (étage 1) → croisement contextuel
(étage 4) → rédaction par le modèle de conseil → **garde-fou de sortie**.

Deux refus catégoriques, tous deux vérifiés et non contournables :

* **Vision indisponible → aucun constat.** On rend ``None``, l'appelant dira que
  l'analyse n'est pas disponible. Jamais une description imaginée : le pattern
  « contexte vide → fabrication » a déjà coûté un correctif (v0.6.48).
* **Sortie compromise → rejet, pas réécriture.** Un constat qui nomme une maladie, un
  produit ou une dose est jeté. On ne rafistole pas une sortie qui a franchi un
  interdit ; on préfère ne rien rendre.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.application.fusion_contextuelle import ContexteParcelle, fusionner
from app.core.logging import get_logger
from app.domain.ports import InferencePort, VisionPort
from app.models.constat import Constat, NiveauConfiance, Observation, Organe
from app.services import guardrails
from app.services.prompts_constat import CONSIGNE_DESCRIPTION, consigne_redaction

logger = get_logger(__name__)

# Un constat tient en quelques phrases : on borne la génération.
MAX_TOKENS_CONSTAT = 260

# Indices lexicaux du tri d'organe (étage 1). Déterministe, explicable, testable —
# le VLM décrit, ce petit tri classe. Un classifieur affiné le remplacera (étage 1
# définitif) sans changer ce contrat.
_INDICES_ORGANE: tuple[tuple[Organe, tuple[str, ...]], ...] = (
    (Organe.CABOSSE, ("cabosse", "cabosses", "fruit", "fruits")),
    (Organe.FEUILLE, ("feuille", "feuilles", "feuillage")),
    (Organe.TRONC, ("tronc", "rameau", "rameaux", "branche", "branches", "ecorce", "écorce")),
    (Organe.VUE_ENSEMBLE, ("vue d'ensemble", "plantation", "parcelle", "ombrage", "sous-bois")),
)


def _deduire_organe(description: str) -> Organe:
    """Déduit l'organe observé depuis la description, ou ``INDETERMINE``."""
    minuscules = description.lower()
    for organe, indices in _INDICES_ORGANE:
        if any(indice in minuscules for indice in indices):
            return organe
    return Organe.INDETERMINE


class ServiceConstatVisuel:
    """Produit un constat visuel non diagnostique à partir d'images de capture."""

    def __init__(self, vision: VisionPort, inference: InferencePort) -> None:
        """Initialise le service.

        Args:
            vision: Port du modèle de vision (mockable).
            inference: Port du modèle de conseil, qui rédige le constat.
        """
        self._vision = vision
        self._inference = inference

    async def analyser(
        self, images: tuple[tuple[bytes, str], ...], contexte: ContexteParcelle
    ) -> Constat | None:
        """Produit le constat d'un jeu d'images, ou ``None``.

        Args:
            images: Couples ``(octets, empreinte_sha256)`` des images recevables.
            contexte: Contexte connu de la parcelle (météo, saison, localité).

        Returns:
            Le constat, ou ``None`` si la vision est indisponible ou si la sortie a
            franchi un interdit.
        """
        if not images:
            return None
        description = await self._vision.decrire(
            tuple(octets for octets, _ in images), CONSIGNE_DESCRIPTION
        )
        if not description:
            logger.info("constat_vision_indisponible")
            return None

        fautif = guardrails.contient_diagnostic(description)
        if fautif:
            logger.warning("constat_description_compromise", terme=fautif)
            return None

        fusion = fusionner(description, NiveauConfiance.MOYENNE, contexte)
        texte = await self._inference.generer(
            question=f"{description}\n\n{consigne_redaction(fusion.facteurs)}",
            temperature=0.3,
            max_tokens=MAX_TOKENS_CONSTAT,
        )

        fautif = guardrails.contient_diagnostic(texte)
        if fautif:
            logger.warning("constat_sortie_compromise", terme=fautif)
            return None
        if guardrails.verifier_reponse(texte) is not None:
            logger.warning("constat_sortie_refusee_par_garde_fou")
            return None

        organe = _deduire_organe(description)
        observations = tuple(
            Observation(
                organe=organe,
                description=description,
                confiance=fusion.confiance,
                empreinte_image=empreinte,
            )
            for _, empreinte in images
        )
        return Constat(
            identifiant=uuid4().hex,
            capture="",
            parcelle="",
            proprietaire="",
            observations=observations,
            texte=texte.strip(),
            confiance=fusion.confiance,
            cree_le=datetime.now(UTC),
            facteurs_contexte=fusion.facteurs,
        )
