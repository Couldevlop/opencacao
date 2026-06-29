"""L'adaptateur ConseilAgentique : présente l'orchestrateur comme un ConseilService."""

from __future__ import annotations

import pytest

from app.api_deps import _construire_orchestrateur
from app.application.conseil_agentique import ConseilAgentique
from app.domain.entities import Conseil
from app.models.domain import Langue


class _Inference:
    async def generer(self, question, *, contexte=None, historique=None, **kw) -> str:
        return "Taillez en saison sèche. Sources : CNRA, ANADER."

    async def generer_stream(self, question, *, contexte=None, historique=None, **kw):
        for fragment in ("Taillez en saison sèche. ", "Sources : CNRA, ANADER."):
            yield fragment

    async def ready(self) -> bool:
        return True


class _Cache:
    async def get_cached(self, q: str, lg: str) -> str | None:
        return None

    async def set_cached(self, q: str, lg: str, payload: str) -> None: ...
    async def hit_rate_limit(self, ip: str) -> bool:
        return False


class _Journal:
    async def enregistrer_interaction(self, *args: object) -> str:
        return "id-xyz"


def _adaptateur() -> ConseilAgentique:
    orch = _construire_orchestrateur(
        inference=_Inference(), cache=_Cache(), journal=_Journal(), rag=None
    )
    return ConseilAgentique(orch)


@pytest.mark.asyncio
async def test_conseiller_delegue_a_l_orchestrateur() -> None:
    conseil = await _adaptateur().conseiller("comment tailler le cacaoyer ?", Langue.FR, "ip")
    assert isinstance(conseil, Conseil)
    assert conseil.reponse.startswith("Taillez")
    assert conseil.interaction_id == "id-xyz"


@pytest.mark.asyncio
async def test_conseiller_stream_emet_des_phrases_puis_done() -> None:
    evenements = [
        e
        async for e in _adaptateur().conseiller_stream(
            "comment tailler le cacaoyer ?", Langue.FR, "ip"
        )
    ]
    # Streaming réel : un ou plusieurs 'token' (phrase par phrase) puis un 'done'.
    assert evenements[-1]["type"] == "done"
    tokens = [e for e in evenements if e["type"] == "token"]
    assert tokens, "au moins un fragment doit être streamé"
    texte = "".join(e["text"] for e in tokens)
    assert "saison sèche" in texte
    done = evenements[-1]
    assert done["interaction_id"] == "id-xyz"
    assert "disclaimer" in done
    assert done["confiance"] in {"faible", "moyenne", "elevee"}
