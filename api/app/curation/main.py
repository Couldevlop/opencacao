"""Console de curation — application ASGI interne (jamais exposée publiquement).

Servie en ClusterIP hors Ingress ; accès par ``kubectl port-forward``. Une
authentification HTTP Basic optionnelle (CURATION_USER / CURATION_PASSWORD)
ajoute une défense en profondeur si le service venait à être exposé.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from app.core.logging import configure_logging, get_logger
from app.curation.models import RejetRequest, ValidationRequest
from app.curation.store import CurationStore, DosageRefuse, ValidationInvalide

logger = get_logger(__name__)

_store = CurationStore.from_env()
_basic = HTTPBasic(auto_error=False)
_UTILISATEUR = os.environ.get("CURATION_USER", "curateur")
_MOT_DE_PASSE = os.environ.get("CURATION_PASSWORD", "")


def _verifier_acces(
    credentials: Annotated[HTTPBasicCredentials | None, Depends(_basic)],
) -> None:
    """Exige une auth Basic correcte uniquement si un mot de passe est configuré."""
    if not _MOT_DE_PASSE:
        return  # Pas de mot de passe -> accès réservé par le réseau (ClusterIP).
    valide = credentials is not None and (
        secrets.compare_digest(credentials.username, _UTILISATEUR)
        and secrets.compare_digest(credentials.password, _MOT_DE_PASSE)
    )
    if not valide:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identifiants invalides.",
            headers={"WWW-Authenticate": "Basic"},
        )


Acces = Annotated[None, Depends(_verifier_acces)]

app = FastAPI(title="OpenCacao — Console de curation", docs_url=None, redoc_url=None)
configure_logging(os.environ.get("LOG_LEVEL", "INFO"))


@app.get("/api/sante")
async def sante() -> dict[str, str]:
    """Liveness."""
    return {"status": "ok"}


@app.get("/api/stats")
async def stats(_: Acces) -> dict[str, int]:
    """Compteurs de curation (total, à curer, validés, rejetés)."""
    return _store.statistiques()


@app.get("/api/a-curer")
async def a_curer(_: Acces) -> list[dict]:
    """Interactions à curer, triées par priorité (👎, faible confiance)."""
    return _store.a_curer()


@app.post("/api/valider", status_code=status.HTTP_202_ACCEPTED)
async def valider(payload: ValidationRequest, _: Acces) -> dict[str, str]:
    """Valide (ou corrige) une paire Q/R et l'ajoute au corpus d'entraînement."""
    try:
        await _store.valider(payload.interaction_id, payload.instruction, payload.output)
    except DosageRefuse as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except ValidationInvalide as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return {"status": "valide"}


@app.post("/api/rejeter", status_code=status.HTTP_202_ACCEPTED)
async def rejeter(payload: RejetRequest, _: Acces) -> dict[str, str]:
    """Écarte une interaction de la curation."""
    await _store.rejeter(payload.interaction_id)
    return {"status": "rejete"}


@app.exception_handler(Exception)
async def _erreur(_, exc: Exception) -> JSONResponse:
    """Pas de fuite de détails internes (OWASP)."""
    logger.error("curation_erreur", error=str(exc))
    return JSONResponse(status_code=500, content={"detail": "Erreur interne."})


_WEB = Path(__file__).resolve().parent / "web"
if (_WEB / "index.html").is_file():
    app.mount("/", StaticFiles(directory=str(_WEB), html=True), name="console")
