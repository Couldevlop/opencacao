"""File de revue ANADER des constats visuels — étage 6 de la cascade.

**Le vrai différenciateur du chantier.** Chaque constat part en revue ; l'agent
confirme ou corrige ; l'étiquette corrigée alimente l'export qui deviendra le jeu de
données ivoirien. Le système s'améliore *parce qu'il est utilisé*, et la précision
annoncée devient mesurable puis publiable.

**Ces routes voient les constats de TOUS les producteurs** — elles sont donc derrière
l'authentification de la console, jamais sur l'API publique. L'export est volontairement
**minimisé** : il ne porte ni identifiant d'appareil ni identifiant de parcelle. Un jeu
d'entraînement a besoin de savoir *ce qui est observé*, jamais *chez qui*.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.application.revue_constats import exporter as exporter_jsonl
from app.application.revue_constats import verifier_correction
from app.core.logging import get_logger
from app.domain.ports import ParcelleStorePort
from app.models.constat import ConstatRevuReponse, EtatRevue

logger = get_logger(__name__)

router = APIRouter(prefix="/curation", tags=["revue"])

LIMITE_FILE = 50
LIMITE_EXPORT = 5000


class RevueRequest(BaseModel):
    """Décision d'un agent ANADER sur un constat."""

    etat: EtatRevue
    revu_par: str = Field(min_length=1, max_length=120)
    correction: str = Field(default="", max_length=2000)


def obtenir_depot(request: Request) -> ParcelleStorePort:
    """Dépendance : retourne le dépôt de parcelles porté par la console."""
    return request.app.state.parcelles


@router.get("/constats", response_model=list[ConstatRevuReponse])
async def lister_file(
    depot: ParcelleStorePort = Depends(obtenir_depot),
) -> list[ConstatRevuReponse]:
    """Liste les constats en attente de revue, les plus anciens d'abord."""
    constats = await depot.lister_constats_en_attente(limite=LIMITE_FILE)
    return [ConstatRevuReponse.model_validate(c, from_attributes=True) for c in constats]


@router.get("/constats/export")
async def exporter(depot: ParcelleStorePort = Depends(obtenir_depot)) -> StreamingResponse:
    """Exporte les constats **revus** en JSONL — le jeu de données ivoirien.

    Un constat en attente n'a pas d'étiquette humaine : il ne sert pas d'exemple et
    n'est donc jamais exporté. La réponse est lue *et* diffusée par pages : la console
    est plafonnée à 1 Gio et l'export peut porter des dizaines de milliers de lignes.

    Servi en pièce jointe : ce fichier se télécharge, il ne s'affiche pas — le
    navigateur n'a jamais à interpréter du texte saisi par un agent.
    """
    return StreamingResponse(
        exporter_jsonl(depot, LIMITE_EXPORT),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": 'attachment; filename="constats_revus.jsonl"'},
    )


@router.post("/constats/{identifiant}/revue", response_model=ConstatRevuReponse)
async def reviser(
    identifiant: str,
    payload: RevueRequest,
    depot: ParcelleStorePort = Depends(obtenir_depot),
) -> ConstatRevuReponse:
    """Enregistre la décision d'un agent ANADER.

    La correction est une **étiquette d'entraînement** : elle passe les mêmes
    garde-fous que toute sortie du système. Une correction qui nomme une maladie ou
    donne un dosage est refusée, pas nettoyée.

    Raises:
        HTTPException: 422 si la correction franchit un interdit, 404 si le constat
            est inconnu.
    """
    fautif = verifier_correction(payload.correction)
    if fautif is not None:
        logger.warning("correction_refusee", constat=identifiant, motif=fautif)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La correction ne peut nommer une maladie, un produit ou un dosage.",
        )
    revu = await depot.reviser_constat(
        identifiant, payload.etat, payload.revu_par, payload.correction
    )
    if revu is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Constat inconnu.")
    logger.info("constat_revu", constat=identifiant, etat=payload.etat.value)
    return ConstatRevuReponse.model_validate(revu, from_attributes=True)
