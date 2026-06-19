"""Console de curation — application ASGI interne, protégée par session.

Authentification par **formulaire** : page de connexion soignée + cookie de
session signé (HMAC), HttpOnly/Secure/SameSite. Les routes ``/api/*`` (hors
login/santé) exigent une session valide dès qu'un mot de passe est configuré
(``CURATION_PASSWORD``). Sans mot de passe (accès par port-forward), l'auth est
désactivée.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi import Path as PathParam
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.logging import configure_logging, get_logger
from app.core.security import BodySizeLimitMiddleware
from app.curation.analytics import analytique
from app.curation.documents import DocumentInvalide, DocumentStore
from app.curation.jobs import JobsRegistry
from app.curation.models import (
    DocumentUpload,
    LoginRequest,
    RejetRequest,
    ValidationRequest,
)
from app.curation.pipeline import PipelineService
from app.curation.ratelimit import LimiteurConnexion
from app.curation.store import CurationStore, DosageRefuse, ValidationInvalide

logger = get_logger(__name__)

_store = CurationStore.from_env()
_jobs = JobsRegistry.from_env()
# Au démarrage (console mono-réplica) : tout job resté "en_cours" est orphelin
# (pod tué/redémarré) -> on le marque en échec pour ne pas bloquer l'anti-concurrence.
_jobs.reconcilier_orphelins()
_pipeline = PipelineService.from_env(_jobs)
_documents = DocumentStore.from_env()
# Référence forte vers les tâches de fond (sinon le GC peut les annuler).
_taches: set[asyncio.Task] = set()
_UTILISATEUR = os.environ.get("CURATION_USER", "curateur")
_MOT_DE_PASSE = os.environ.get("CURATION_PASSWORD", "")
# Clé de signature des sessions, dérivée du mot de passe : stable entre
# redémarrages, et la rotation du mot de passe invalide les sessions.
_SECRET = hashlib.sha256(f"opencacao-curation:{_MOT_DE_PASSE}".encode()).digest()
_COOKIE = "curation_session"
_DUREE_S = 8 * 3600

# Anti-brute-force du login (OWASP API2) : blocage par IP après N échecs.
# Seuil tolérant aux fautes de frappe d'un admin légitime, tout en stoppant le
# brute-force automatisé.
_LIMITEUR_LOGIN = LimiteurConnexion(
    max_echecs=int(os.environ.get("CURATION_LOGIN_MAX_ECHECS", "10")),
    fenetre_s=float(os.environ.get("CURATION_LOGIN_FENETRE_S", "300")),
)


def _ip_client(request: Request) -> str:
    """IP cliente (1ᵉʳ hop X-Forwarded-For posé par l'ingress, sinon IP TCP)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "inconnu"


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

# En-têtes de sécurité (OWASP Secure Headers). CSP propre à la console : la page
# charge SON js/css/img => 'self' (la CSP 'none' de l'API publique les bloquerait).
_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "img-src 'self' data:; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
_ENTETES_SECURITE = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": _CSP,
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}


@app.middleware("http")
async def _entetes_securite(request: Request, call_next) -> Response:
    """Applique les en-têtes de sécurité OWASP et retire l'en-tête Server."""
    response = await call_next(request)
    for entete, valeur in _ENTETES_SECURITE.items():
        response.headers.setdefault(entete, valeur)
    if "server" in response.headers:
        del response.headers["server"]
    return response


# Anti-DoS : plafond du corps de requête. Relevé pour l'upload de documents
# (contenu base64) ; reste borné pour éviter les abus. ~12 Mo de body ≈ ~9 Mo de fichier.
app.add_middleware(BodySizeLimitMiddleware, max_body_bytes=12_000_000)


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
async def login(payload: LoginRequest, request: Request, response: Response) -> dict[str, bool]:
    """Vérifie les identifiants et pose un cookie de session signé.

    Les valeurs sont nettoyées (strip) pour tolérer un espace/retour-ligne
    introduit par un copier-coller. Le login est protégé contre le brute-force
    par un blocage par IP après plusieurs échecs.

    Raises:
        HTTPException: 429 si trop de tentatives, 401 si identifiants invalides.
    """
    ip = _ip_client(request)
    if _LIMITEUR_LOGIN.bloque(ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Trop de tentatives de connexion. Réessayez dans quelques minutes.",
        )
    utilisateur = payload.utilisateur.strip()
    mot_de_passe = payload.mot_de_passe.strip()
    valide = bool(_MOT_DE_PASSE) and (
        secrets.compare_digest(utilisateur, _UTILISATEUR)
        and secrets.compare_digest(mot_de_passe, _MOT_DE_PASSE)
    )
    if not valide:
        _LIMITEUR_LOGIN.echec(ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Identifiants invalides."
        )
    _LIMITEUR_LOGIN.succes(ip)
    response.set_cookie(
        _COOKIE,
        _creer_token(),
        max_age=_DUREE_S,
        httponly=True,
        secure=True,
        samesite="lax",  # renvoyé de façon fiable sur les requêtes same-origin
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


@app.get("/api/analytics")
async def analytics(_: Session) -> dict:
    """Analytique des visites : compteurs par période (jour/semaine/mois/an) + pays."""
    return analytique()


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


# --- Étape ① Documents (upload) ---


@app.get("/api/documents")
async def documents_liste(_: Session) -> list[dict]:
    """Liste les documents sources téléversés (nom, taille)."""
    return _documents.lister()


@app.post("/api/documents", status_code=status.HTTP_201_CREATED)
async def documents_upload(payload: DocumentUpload, _: Session) -> list[dict]:
    """Téléverse un document (PDF/TXT/MD, contenu base64) et retourne la liste.

    Raises:
        HTTPException: 422 si le nom/format est invalide ou le contenu illisible.
    """
    try:
        donnees = base64.b64decode(payload.contenu_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Contenu base64 invalide."
        ) from exc
    try:
        _documents.enregistrer(payload.nom, donnees)
    except DocumentInvalide as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return _documents.lister()


@app.delete("/api/documents/{nom}")
async def documents_suppr(nom: str, _: Session) -> list[dict]:
    """Supprime un document téléversé et retourne la liste mise à jour."""
    try:
        _documents.supprimer(nom)
    except DocumentInvalide as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return _documents.lister()


@app.post("/api/recherche", status_code=status.HTTP_202_ACCEPTED)
async def recherche_sources(_: Session) -> dict[str, str]:
    """Lance (tâche de fond) le téléchargement des sources officielles.

    Raises:
        HTTPException: 409 si une recherche est déjà en cours.
    """
    job = await _pipeline.demarrer_recherche()
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Une recherche est déjà en cours."
        )
    tache = asyncio.create_task(_pipeline.collecter_sources(job["id"]))
    _taches.add(tache)
    tache.add_done_callback(_taches.discard)
    return {"job_id": job["id"], "statut": job["statut"]}


# --- Pipeline : constitution RAG, reindex, préparation fine-tuning, suivi des jobs ---


@app.post("/api/rag/constituer", status_code=status.HTTP_202_ACCEPTED)
async def rag_constituer(_: Session) -> dict[str, str]:
    """Lance (tâche de fond) la constitution du RAG depuis les documents téléversés.

    Raises:
        HTTPException: 409 si une constitution est déjà en cours.
    """
    job = await _pipeline.demarrer_constitution()
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Une constitution est déjà en cours."
        )
    tache = asyncio.create_task(_pipeline.constituer_rag(job["id"]))
    _taches.add(tache)
    tache.add_done_callback(_taches.discard)
    return {"job_id": job["id"], "statut": job["statut"]}


@app.post("/api/rag/reindex", status_code=status.HTTP_202_ACCEPTED)
async def rag_reindex(_: Session) -> dict[str, str]:
    """Lance (en tâche de fond) l'ajout des faits curés à l'index RAG + reload API.

    Raises:
        HTTPException: 409 si un reindex est déjà en cours.
    """
    job = await _pipeline.demarrer_reindex()
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Un reindex est déjà en cours."
        )
    tache = asyncio.create_task(_pipeline.reindexer_rag(job["id"]))
    _taches.add(tache)
    tache.add_done_callback(_taches.discard)
    return {"job_id": job["id"], "statut": job["statut"]}


@app.post("/api/finetuning/prepare", status_code=status.HTTP_202_ACCEPTED)
async def finetuning_prepare(_: Session) -> dict:
    """Assemble le corpus curé et fournit la procédure d'entraînement (pod GPU).

    Raises:
        HTTPException: 409 si une préparation est déjà en cours.
    """
    job = await _pipeline.preparer_finetuning()
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Une préparation est déjà en cours.",
        )
    return job


@app.get("/api/jobs")
async def jobs_liste(_: Session) -> list[dict]:
    """Liste les jobs du pipeline, du plus récent au plus ancien."""
    return await _jobs.lister()


@app.get("/api/jobs/{job_id}")
async def job_detail(
    _: Session,
    job_id: Annotated[str, PathParam(pattern=r"^[0-9a-f]{16}$")],
) -> dict:
    """Détail d'un job (statut, message, log).

    Raises:
        HTTPException: 404 si le job est introuvable.
    """
    job = await _jobs.obtenir(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job introuvable.")
    return job


@app.exception_handler(Exception)
async def _erreur(_, exc: Exception) -> JSONResponse:
    """Pas de fuite de détails internes (OWASP)."""
    logger.error("curation_erreur", error=str(exc))
    return JSONResponse(status_code=500, content={"detail": "Erreur interne."})


_WEB = Path(__file__).resolve().parent / "web"
if (_WEB / "index.html").is_file():
    app.mount("/", StaticFiles(directory=str(_WEB), html=True), name="console")
