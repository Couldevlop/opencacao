"""Keepalive du préfixe KV : micro-générations périodiques, erreurs tolérées."""

from __future__ import annotations

import pytest

from app.application.keepalive import boucle_keepalive


class _InferenceFactice:
    def __init__(self, echouer_au: int | None = None) -> None:
        self.appels: list[dict] = []
        self._echouer_au = echouer_au

    async def generer(self, question, **kwargs) -> str:
        self.appels.append({"question": question, **kwargs})
        if self._echouer_au is not None and len(self.appels) == self._echouer_au:
            raise RuntimeError("inférence indisponible")
        return "ok"


@pytest.mark.asyncio
async def test_appelle_l_inference_a_chaque_intervalle() -> None:
    inference = _InferenceFactice()
    pauses: list[float] = []

    async def dormir(delai: float) -> None:
        pauses.append(delai)

    await boucle_keepalive(inference, intervalle_s=600, dormir=dormir, iterations=3)
    assert len(inference.appels) == 3
    assert pauses == [600, 600, 600]
    # Micro-génération : un seul token suffit à garder le préfixe système chaud.
    assert all(a.get("max_tokens") == 1 for a in inference.appels)


@pytest.mark.asyncio
async def test_une_erreur_ne_tue_pas_la_boucle() -> None:
    inference = _InferenceFactice(echouer_au=2)

    async def dormir(delai: float) -> None: ...

    await boucle_keepalive(inference, intervalle_s=600, dormir=dormir, iterations=4)
    assert len(inference.appels) == 4  # l'échec du 2e appel n'arrête pas les suivants


# --- Intégration lifespan (_lancer_keepalive) ---


def _faux_app(inference) -> object:
    from types import SimpleNamespace

    return SimpleNamespace(state=SimpleNamespace(inference=inference))


@pytest.mark.asyncio
async def test_lancer_keepalive_desactive_par_defaut() -> None:
    from app.core.config import Settings
    from app.main import _lancer_keepalive

    assert _lancer_keepalive(_faux_app(_InferenceFactice()), Settings()) is None


@pytest.mark.asyncio
async def test_lancer_keepalive_actif_cree_une_tache() -> None:
    from app.core.config import Settings
    from app.main import _lancer_keepalive

    tache = _lancer_keepalive(_faux_app(_InferenceFactice()), Settings(kv_keepalive_s=600))
    assert tache is not None
    tache.cancel()
