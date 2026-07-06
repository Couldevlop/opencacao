"""Keepalive du préfixe KV de l'inférence (latence du « premier producteur »).

``cache_prompt`` rend le préfixe système quasi gratuit d'une requête à l'autre —
tant qu'il est chaud dans llama-server. Après une période calme, la première
requête repaie ~15-25 s de préremplissage CPU. Cette boucle de fond envoie une
micro-génération (``max_tokens=1``) à intervalle régulier pour garder le préfixe
chaud : coût ~2-3 s de CPU par intervalle (~0,5 % à 10 min), nul en pratique
pendant le trafic réel.

Tolérant : une inférence indisponible est loguée et réessayée au tour suivant,
la boucle ne meurt jamais (elle est annulée à l'arrêt de l'application).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from app.core.logging import get_logger

logger = get_logger(__name__)

# Question minimale : le coût utile est le préremplissage du préfixe système
# (message system constant) ; la question et la réponse (1 token) sont négligeables.
_QUESTION = "ok"


async def boucle_keepalive(
    inference: object,
    intervalle_s: float,
    *,
    dormir: Callable[[float], Awaitable[None]] = asyncio.sleep,
    iterations: int | None = None,
) -> None:
    """Micro-génère à intervalle régulier pour garder le préfixe KV chaud.

    Args:
        inference: Client d'inférence (méthode ``generer``, kwarg ``max_tokens``).
        intervalle_s: Pause entre deux micro-générations.
        dormir: Fonction d'attente (injectable pour les tests).
        iterations: Nombre de tours, ou None pour boucler indéfiniment (prod).
    """
    tour = 0
    while iterations is None or tour < iterations:
        tour += 1
        await dormir(intervalle_s)
        try:
            await inference.generer(_QUESTION, max_tokens=1)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — best-effort, jamais fatal
            logger.warning("keepalive_echec", error=str(exc))
