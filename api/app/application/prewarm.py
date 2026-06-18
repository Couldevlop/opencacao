"""Pré-chauffage du cache de réponses au démarrage.

Génère une fois les réponses aux questions fréquentes (:mod:`app.application.faq`)
pour qu'elles soient ensuite servies instantanément (cache, TTL 7 jours) au lieu
de ~20 s d'inférence CPU. Conçu pour tourner en **tâche de fond** : ne bloque pas
le démarrage ni la disponibilité de l'API.

Tolérant : si l'inférence n'est pas encore prête au lancement, on réessaie la
question courante quelques fois ; si elle reste indisponible, on s'arrête
proprement (le cache se remplira naturellement à l'usage).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence

from app.application.conseil_service import ConseilService
from app.core.logging import get_logger
from app.domain.exceptions import InferenceUnavailable
from app.models.domain import Langue

logger = get_logger(__name__)


async def prechauffer_cache(
    service: ConseilService,
    questions: Sequence[str],
    langue: Langue = Langue.FR,
    *,
    tentatives: int = 30,
    delai_s: float = 10.0,
    dormir: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> int:
    """Pré-chauffe le cache pour une liste de questions.

    Args:
        service: Cas d'usage du conseil (méthode :meth:`ConseilService.prechauffer`).
        questions: Questions fréquentes à pré-calculer.
        langue: Langue des réponses.
        tentatives: Nombre d'essais sur la 1ʳᵉ question tant que l'inférence n'est
            pas prête (au-delà, on abandonne le pré-chauffage).
        delai_s: Pause entre deux essais quand l'inférence est indisponible.
        dormir: Fonction d'attente (injectable pour les tests).

    Returns:
        Le nombre de réponses effectivement générées et mises en cache.
    """
    reussites = 0
    deja = 0
    essais_restants = tentatives
    index = 0
    while index < len(questions):
        question = questions[index]
        try:
            if await service.prechauffer(question, langue):
                reussites += 1
            else:
                deja += 1
            index += 1
            essais_restants = tentatives  # réinitialise dès qu'une réponse aboutit
        except InferenceUnavailable:
            essais_restants -= 1
            if essais_restants <= 0:
                logger.warning(
                    "prewarm_abandon", raison="inference_indisponible", a_chaud=reussites
                )
                return reussites
            logger.info("prewarm_attente_inference", essais_restants=essais_restants)
            await dormir(delai_s)
    logger.info("prewarm_termine", generes=reussites, deja_en_cache=deja, total=len(questions))
    return reussites
