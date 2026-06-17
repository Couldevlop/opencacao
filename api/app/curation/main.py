"""Console de curation — application ASGI interne, protégée par session.

Authentification par **formulaire** : page de connexion soignée + cookie de
session signé (HMAC), HttpOnly/Secure/SameSite. Les routes ``/api/*`` (hors
login/santé) exigent une session valide dès qu'un mot de passe est configuré
(``CURATION_PASSWORD``). Sans mot de passe (accès par port-forward), l'auth est
désactivée.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.logging import configure_logging, get_logger
from app.curation.models import LoginRequest, RejetRequest, ValidationRequest
from app.curation.store import CurationStore, DosageRefuse, ValidationInvalide

logger = get_logger(__name__)

_store = CurationStore.from_env()
_UTILISATEUR = os.environ.get("CURATION_USER", "curateur")
_MOT_DE_PASSE = os.environ.get("CURATION_PASSWORD", "")
# Clé de signature des sessions, dérivée du mot de passe : stable entre
# redémarrages, et la rotation du mot de passe invalide les sessions.
_SECRET = hashlib.sha256(f"opencacao-curation:{_MOT_DE_PASSE}".encode()).digest()
_COOKIE = "curation_session"
_DUREE_S = 8 * 3600


def _creer_token() -> str:
    """Crée un jeton de session signé (expiration + HMAC)."""
    exp = str(int(time.time()) + _DUREE_S)
    signature = hmac.new(_SECRET, exp.encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{signature}"


def _token_valide(token: str | None) -> bool:
    """Vérifie la signature et la non-expiration d'un jeton de session."""
    if not token or "." not in token:
        return False
    exp, _, signature = token.partition(".")
    attendu = hmac.new(_SECRET, exp.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, attendu):
        return False
    try:
        return int(exp) > int(time.time())
    except ValueError:
        return False


def _exiger_session(request: Request) -> None:
    """Dépendance : exige une session valide si un mot de passe est configuré."""
    if not _MOT_DE_PASSE:
        return
    if not _token_valide(request.cookies.get(_COOKIE)):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session requise.")


Session = Annotated[None, Depends(_exiger_session)]

app = FastAPI(title="OpenCacao — Console de curation", docs_url=None, redoc_url=None)
configure_logging(os.environ.get("LOG_LEVEL", "INFO"))


@app.get("/api/sante")
async def sante() -> dict[str, str]:
    """Liveness (publique)."""
    return {"status": "ok"}


@app.get("/api/session")
async def session_etat(request: Request) -> dict[str, bool]:
    """Indique si une auth est requise et si la session courante est valide."""
    if not _MOT_DE_PASSE:
        return {"auth_requise": False, "authentifie": True}
    return {"auth_requise": True, "authentifie": _token_valide(request.cookies.get(_COOKIE))}


@app.post("/api/login")
async def login(payload: LoginRequest, response: Response) -> dict[str, bool]:
    """Vérifie les identifiants et pose un cookie de session signé."""
    valide = bool(_MOT_DE_PASSE) and (
        secrets.compare_digest(payload.utilisateur, _UTILISATEUR)
        and secrets.compare_digest(payload.mot_de_passe, _MOT_DE_PASSE)
    )
    if not valide:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Identifiants invalides."
        )
    response.set_cookie(
        _COOKIE,
        _creer_token(),
        max_age=_DUREE_S,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )
    return {"ok": True}


@app.post("/api/logout")
async def logout(response: Response) -> dict[str, bool]:
    """Termine la session (efface le cookie)."""
    response.delete_cookie(_COOKIE, path="/")
    return {"ok": True}


@app.get("/api/stats")
async def stats(_: Session) -> dict[str, int]:
    """Compteurs de curation (total, à curer, validés, rejetés)."""
    return _store.statistiques()


@app.get("/api/a-curer")
async def a_curer(_: Session) -> list[dict]:
    """Interactions à curer, triées par priorité (👎, faible confiance)."""
    return _store.a_curer()


@app.post("/api/valider", status_code=status.HTTP_202_ACCEPTED)
async def valider(payload: ValidationRequest, _: Session) -> dict[str, str]:
    """Valide (ou corrige) une paire Q/R et l'ajoute au corpus d'entraînement."""
    try:
        await _store.valider(payload.interaction_id, payload.instruction, payload.output)
    except (DosageRefuse, ValidationInvalide) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return {"status": "valide"}


@app.post("/api/rejeter", status_code=status.HTTP_202_ACCEPTED)
async def rejeter(payload: RejetRequest, _: Session) -> dict[str, str]:
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
