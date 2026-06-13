"""Cas d'usage : produire un conseil agronomique.

Orchestre rate-limit, garde-fous, cache et inférence en ne dépendant que des
ports du domaine. Cette classe est testable sans FastAPI ni Redis.
"""

from __future__ import annotations

import json

from app.core.logging import get_logger
from app.domain.entities import Conseil
from app.domain.exceptions import RateLimitDepasse
from app.domain.ports import CachePort, InferencePort
from app.models.domain import Confiance, Langue
from app.services import guardrails, postprocess

logger = get_logger(__name__)


class ConseilService:
    """Cas d'usage central du conseil agronomique."""

    def __init__(self, inference: InferencePort, cache: CachePort) -> None:
        """Initialise le service avec ses dépendances (ports).

        Args:
            inference: Port d'inférence.
            cache: Port de cache/rate-limit.
        """
        self._inference = inference
        self._cache = cache

    async def conseiller(self, question: str, langue: Langue, client_ip: str) -> Conseil:
        """Produit un conseil pour la question donnée.

        Args:
            question: Question du producteur (déjà validée par le DTO).
            langue: Langue de la requête.
            client_ip: IP cliente, pour le rate-limit.

        Returns:
            Un objet Conseil.

        Raises:
            RateLimitDepasse: Si le quota par IP est dépassé.
            InferenceUnavailable: Si l'inférence échoue (propagée par le port).
        """
        if await self._cache.hit_rate_limit(client_ip):
            raise RateLimitDepasse

        # Garde-fous métier : refus sans appeler le modèle.
        refus = guardrails.evaluer(question)
        if refus is not None:
            logger.info("garde_fou_declenche", categorie=refus.categorie.value)
            return Conseil(
                reponse=refus.message,
                confiance=Confiance.ELEVEE,
                sources=[],
                redirection_anader=True,
            )

        # Cache de réponses.
        cached = await self._cache.get_cached(question, langue.value)
        if cached is not None:
            donnees = json.loads(cached)
            return Conseil(
                reponse=donnees["reponse"],
                confiance=Confiance(donnees["confiance"]),
                sources=donnees["sources"],
                redirection_anader=donnees["redirection_anader"],
            )

        # Inférence (peut lever InferenceUnavailable).
        texte = await self._inference.generer(question)

        sources = postprocess.extraire_sources(texte)
        conseil = Conseil(
            reponse=texte,
            confiance=postprocess.estimer_confiance(sources),
            sources=sources,
            redirection_anader=False,
        )

        await self._cache.set_cached(
            question,
            langue.value,
            json.dumps(
                {
                    "reponse": conseil.reponse,
                    "confiance": conseil.confiance.value,
                    "sources": conseil.sources,
                    "redirection_anader": conseil.redirection_anader,
                }
            ),
        )
        return conseil
