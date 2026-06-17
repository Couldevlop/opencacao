"""Cas d'usage : produire un conseil agronomique.

Orchestre rate-limit, garde-fous, cache et inférence en ne dépendant que des
ports du domaine. Cette classe est testable sans FastAPI ni Redis.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator

from app.core.logging import get_logger
from app.domain.entities import Conseil
from app.domain.exceptions import RateLimitDepasse
from app.domain.ports import CachePort, InferencePort
from app.models.chat import DISCLAIMER
from app.models.domain import Confiance, Langue
from app.services import guardrails, postprocess

logger = get_logger(__name__)

# Fin de phrase suivie d'une espace : sert à ne livrer en streaming que des
# phrases complètes, scannées par le garde-fou de sortie AVANT émission.
_FIN_PHRASE = re.compile(r"[.!?…](?=\s)")


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

        # Garde-fou de SORTIE (défense en profondeur) : ne jamais livrer un dosage.
        if guardrails.verifier_reponse(texte) is not None:
            logger.warning("garde_fou_sortie_declenche")
            return Conseil(
                reponse=guardrails.REFUS_PHYTO,
                confiance=Confiance.ELEVEE,
                sources=[],
                redirection_anader=True,
            )

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

    async def conseiller_stream(
        self, question: str, langue: Langue, client_ip: str
    ) -> AsyncIterator[dict]:
        """Produit un conseil en flux, pour un rendu progressif côté client.

        Émet des événements ``{"type": "token", "text": ...}`` au fil de l'eau, puis
        un ``{"type": "done", ...}`` final (sources, confiance, disclaimer). Le
        garde-fou de sortie est appliqué phrase par phrase AVANT émission : aucune
        phrase contenant un dosage n'est diffusée.

        Args:
            question: Question du producteur (déjà validée par le DTO).
            langue: Langue de la requête.
            client_ip: IP cliente, pour le rate-limit.

        Yields:
            Des événements de flux (dictionnaires sérialisables).

        Raises:
            RateLimitDepasse: Si le quota par IP est dépassé.
            InferenceUnavailable: Si l'inférence échoue (propagée par le port).
        """
        if await self._cache.hit_rate_limit(client_ip):
            raise RateLimitDepasse

        refus = guardrails.evaluer(question)
        if refus is not None:
            logger.info("garde_fou_declenche", categorie=refus.categorie.value)
            yield {"type": "token", "text": refus.message}
            yield self._evenement_final([], Confiance.ELEVEE, redirection=True)
            return

        cached = await self._cache.get_cached(question, langue.value)
        if cached is not None:
            donnees = json.loads(cached)
            yield {"type": "token", "text": donnees["reponse"]}
            yield self._evenement_final(
                donnees["sources"],
                Confiance(donnees["confiance"]),
                redirection=donnees["redirection_anader"],
            )
            return

        emis: list[str] = []
        tampon = ""
        compromis = False

        async for delta in self._inference.generer_stream(question):
            tampon += delta
            while (match := _FIN_PHRASE.search(tampon)) is not None:
                coupe = match.start() + 1
                phrase, tampon = tampon[:coupe], tampon[coupe:]
                if guardrails.verifier_reponse("".join(emis) + phrase) is not None:
                    compromis = True
                    break
                emis.append(phrase)
                yield {"type": "token", "text": phrase}
            if compromis:
                break

        if not compromis and tampon.strip():
            if guardrails.verifier_reponse("".join(emis) + tampon) is not None:
                compromis = True
            else:
                emis.append(tampon)
                yield {"type": "token", "text": tampon}

        if compromis:
            logger.warning("garde_fou_sortie_declenche")
            redirection = " " + guardrails.REFUS_PHYTO
            yield {"type": "token", "text": redirection}
            yield self._evenement_final([], Confiance.ELEVEE, redirection=True)
            return

        texte = "".join(emis)
        sources = postprocess.extraire_sources(texte)
        confiance = postprocess.estimer_confiance(sources)
        await self._cache.set_cached(
            question,
            langue.value,
            json.dumps(
                {
                    "reponse": texte,
                    "confiance": confiance.value,
                    "sources": sources,
                    "redirection_anader": False,
                }
            ),
        )
        yield self._evenement_final(sources, confiance, redirection=False)

    @staticmethod
    def _evenement_final(sources: list[str], confiance: Confiance, *, redirection: bool) -> dict:
        """Construit l'événement terminal du flux (métadonnées de la réponse)."""
        return {
            "type": "done",
            "sources": sources,
            "confiance": confiance.value,
            "redirection_anader": redirection,
            "disclaimer": DISCLAIMER,
        }
