"""Endpoint POST /v1/feedback — retour utilisateur 👍/👎 sur une réponse.

Alimente la boucle d'amélioration : les interactions mal notées seront revues en
curation avant d'enrichir le corpus d'entraînement.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.api_deps import get_journal
from app.domain.ports import JournalPort
from app.models.chat import FeedbackRequest

router = APIRouter(prefix="/v1", tags=["feedback"])


@router.post("/feedback", status_code=status.HTTP_202_ACCEPTED)
async def feedback(
    payload: FeedbackRequest,
    journal: JournalPort = Depends(get_journal),
) -> dict[str, str]:
    """Enregistre un retour (👍/👎) pour une interaction donnée.

    Tolérant : l'enregistrement est best-effort (le journal absorbe ses pannes).
    """
    await journal.enregistrer_feedback(payload.interaction_id, payload.vote)
    return {"status": "enregistre"}
