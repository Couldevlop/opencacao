"""Mailer transactionnel d'alerte via Zoho ZeptoMail (API HTTP).

Aligné sur le mailer du site OpenLab (``lib/email-core.ts`` : même service, même
région ``.com``, même header d'authentification). On passe par l'API HTTP (port
443) et non par SMTP : le plan Zoho « Forever Free » n'autorise pas le SMTP externe
et le 587/465 serait de toute façon bloqué par la NetworkPolicy d'égress.

**Fail-soft** : si ``ZEPTOMAIL_TOKEN`` est absent (dev/CI) ou si ZeptoMail répond
en erreur, on journalise et on renvoie ``False`` — jamais d'exception. Une panne
d'alerte ne doit pas faire échouer le job qui l'émet.

Variables d'environnement :
 - ``ZEPTOMAIL_TOKEN``   : jeton « Send Mail » (obligatoire, sinon envoi sauté) ;
 - ``ZEPTOMAIL_API_URL`` : endpoint (défaut ``https://api.zeptomail.com/v1.1/email``) ;
 - ``EMAIL_FROM`` / ``EMAIL_FROM_NAME`` : expéditeur (domaine vérifié dans ZeptoMail) ;
 - ``EMAIL_TEAM``        : destinataire des alertes (défaut waopron@openlabconsulting.com).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

_TIMEOUT_S = 10.0
_DEFAULT_API_URL = "https://api.zeptomail.com/v1.1/email"
# Expéditeur ET destinataire par défaut : waopron@ est l'adresse vérifiée comme
# expéditeur dans le Mail Agent ZeptoMail (noreply@ renvoie SM_147 « Sender Address
# not available »). Surchageable via EMAIL_FROM / EMAIL_TEAM.
_DEFAULT_FROM = "waopron@openlabconsulting.com"
_DEFAULT_TEAM = "waopron@openlabconsulting.com"


@dataclass(frozen=True)
class MailerConfig:
    """Configuration du mailer, lue depuis l'environnement."""

    token: str
    api_url: str
    from_addr: str
    from_name: str
    team: str


def lire_config() -> MailerConfig | None:
    """Construit la config depuis l'environnement, ou None si le token manque."""
    token = os.environ.get("ZEPTOMAIL_TOKEN")
    if not token:
        return None
    return MailerConfig(
        token=token,
        api_url=os.environ.get("ZEPTOMAIL_API_URL", _DEFAULT_API_URL),
        from_addr=os.environ.get("EMAIL_FROM", _DEFAULT_FROM),
        from_name=os.environ.get("EMAIL_FROM_NAME", "OpenCacao"),
        team=os.environ.get("EMAIL_TEAM", _DEFAULT_TEAM),
    )


def _echapper(texte: str) -> str:
    """Échappe le HTML pour neutraliser toute injection dans le corps du mail."""
    return (
        texte.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def corps_message(cfg: MailerConfig, sujet: str, texte: str, destinataire: str) -> dict:
    """Construit le corps JSON attendu par l'API ZeptoMail (testable sans réseau)."""
    html = (
        '<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;'
        'color:#1a1d24;line-height:1.6;">'
        f"<h2 style='color:#0a0e1a;'>{_echapper(sujet)}</h2>"
        f"<pre style='white-space:pre-wrap;font-size:14px;'>{_echapper(texte)}</pre>"
        '<p style="font-size:12px;color:#5b6170;">— Supervision OpenCacao</p></div>'
    )
    return {
        "from": {"address": cfg.from_addr, "name": cfg.from_name},
        "to": [{"email_address": {"address": destinataire}}],
        "subject": sujet,
        "htmlbody": html,
        "textbody": texte,
    }


async def envoyer_alerte(
    sujet: str,
    texte: str,
    *,
    destinataire: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> bool:
    """Envoie un email d'alerte à l'équipe (fail-soft, jamais d'exception).

    Args:
        sujet: Objet du message.
        texte: Corps en texte brut (repris tel quel + version HTML échappée).
        destinataire: Adresse cible ; défaut = ``EMAIL_TEAM``.
        client: Client httpx injectable (tests).

    Returns:
        True si l'email est accepté par ZeptoMail, False sinon (token absent,
        erreur réseau, réponse non-2xx).
    """
    cfg = lire_config()
    if cfg is None:
        logger.info("email_saute_token_absent", sujet=sujet)
        return False

    to = destinataire or cfg.team
    corps = corps_message(cfg, sujet, texte, to)
    entetes = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        # ZeptoMail attend le préfixe littéral « Zoho-enczapikey ».
        "Authorization": f"Zoho-enczapikey {cfg.token}",
    }

    proche = client is None
    http = client or httpx.AsyncClient(timeout=_TIMEOUT_S)
    try:
        reponse = await http.post(cfg.api_url, json=corps, headers=entetes)
        reponse.raise_for_status()
        logger.info("email_envoye", sujet=sujet, destinataire=to)
        return True
    except httpx.HTTPError as exc:
        logger.warning("email_echec", sujet=sujet, error=str(exc))
        return False
    finally:
        if proche:
            await http.aclose()
