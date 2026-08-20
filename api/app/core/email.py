"""Mailer transactionnel d'alerte — ZeptoMail (API HTTP) ou SMTP.

**Deux canaux, un seul point d'appel.** ``EMAIL_CANAL`` choisit : ``zeptomail``
(défaut, API HTTP sur 443) ou ``smtp`` (STARTTLS sur 587). Le reste du code appelle
``envoyer_alerte`` sans savoir lequel sert.

**Pourquoi le SMTP a été ajouté.** ZeptoMail répond ``429`` depuis le 19/08 : aucune
alerte ne part, et le rappel du soir — seul garde-fou contre un pod loué resté allumé —
est muet. Le projet Maturia envoie depuis des mois par SMTP Gmail sur le même domaine ;
on reprend ce canal éprouvé plutôt que d'attendre le déblocage d'un quota.

Un commentaire de ce module affirmait que « le 587/465 serait de toute façon bloqué par
la NetworkPolicy d'égress ». C'était faux : **aucune** NetworkPolicy du namespace ne
couvre l'égress, et la connexion a été vérifiée depuis un pod de production —
``smtp.gmail.com:587`` répond et STARTTLS s'y négocie. Le 465 (SSL implicite), lui, est
bien bloqué par l'hébergeur : d'où le choix du 587.

**Fail-soft** : si ``ZEPTOMAIL_TOKEN`` est absent (dev/CI) ou si ZeptoMail répond
en erreur, on journalise et on renvoie ``False`` — jamais d'exception. Une panne
d'alerte ne doit pas faire échouer le job qui l'émet.

Variables d'environnement :
 - ``ZEPTOMAIL_TOKEN``   : jeton « Send Mail » (obligatoire, sinon envoi sauté) ;
 - ``ZEPTOMAIL_API_URL`` : endpoint (défaut ``https://api.zeptomail.com/v1.1/email``) ;
 - ``EMAIL_FROM`` / ``EMAIL_FROM_NAME`` : expéditeur (domaine vérifié dans ZeptoMail) ;
 - ``EMAIL_TEAM``        : destinataire des alertes (défaut waopron@openlabconsulting.com).

Canal SMTP :
 - ``EMAIL_CANAL=smtp`` ;
 - ``SMTP_HOST`` / ``SMTP_PORT`` (défaut 587) / ``SMTP_USER`` / ``SMTP_PASSWORD``.

L'expéditeur y est **forcé au compte authentifié** : Gmail et M365 l'exigent, et
laisser passer ``EMAIL_FROM`` ferait rejeter — ou réécrire en silence — tous les envois.
Seul le nom affiché reste configurable.
"""

from __future__ import annotations

import asyncio
import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

import httpx

from app.core import email_gabarit
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


def corps_message(cfg: MailerConfig, sujet: str, texte: str, destinataire: str) -> dict:
    """Construit le corps JSON attendu par l'API ZeptoMail (testable sans réseau).

    Le gabarit est PARTAGÉ avec le canal SMTP : deux mises en forme finiraient par
    diverger, et c'est celle qu'on regarde le moins qui vieillirait mal.
    """
    return {
        "from": {"address": cfg.from_addr, "name": cfg.from_name},
        "to": [{"email_address": {"address": destinataire}}],
        "subject": sujet,
        "htmlbody": email_gabarit.html(sujet, texte),
        "textbody": email_gabarit.texte(sujet, texte),
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
        True si l'email est accepté par le canal actif, False sinon (configuration
        absente, erreur réseau, réponse non-2xx).
    """
    # Aiguillage explicite. Le défaut reste ZeptoMail : ajouter une option ne doit
    # jamais faire basculer un déploiement qui fonctionne.
    if os.environ.get("EMAIL_CANAL", "zeptomail").lower() == "smtp":
        return await envoyer_alerte_smtp(sujet, texte, destinataire=destinataire)

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


# --------------------------------------------------------------------------- #
#                                 Canal SMTP                                    #
# --------------------------------------------------------------------------- #

_SMTP_TIMEOUT_S = 15.0
_SMTP_PORT_DEFAUT = 587


@dataclass(frozen=True)
class SmtpConfig:
    """Configuration du relais SMTP, lue depuis l'environnement."""

    hote: str
    port: int
    utilisateur: str
    secret: str
    from_name: str
    team: str


def lire_config_smtp() -> SmtpConfig | None:
    """Construit la config SMTP, ou ``None`` s'il manque de quoi se connecter."""
    hote = os.environ.get("SMTP_HOST", "")
    utilisateur = os.environ.get("SMTP_USER", "")
    secret = os.environ.get("SMTP_PASSWORD", "")
    if not (hote and utilisateur and secret):
        return None
    return SmtpConfig(
        hote=hote,
        port=int(os.environ.get("SMTP_PORT", _SMTP_PORT_DEFAUT)),
        utilisateur=utilisateur,
        secret=secret,
        from_name=os.environ.get("EMAIL_FROM_NAME", "OpenCacao"),
        team=os.environ.get("EMAIL_TEAM", _DEFAULT_TEAM),
    )


def _message_smtp(cfg: SmtpConfig, sujet: str, texte: str, destinataire: str) -> EmailMessage:
    """Compose le message. L'expéditeur est le compte authentifié, jamais EMAIL_FROM."""
    message = EmailMessage()
    message["Subject"] = sujet
    # Gmail et M365 imposent que l'adresse soit celle du compte authentifié. On ne
    # tente donc pas EMAIL_FROM : le nom affiché suffit à identifier l'émetteur.
    message["From"] = f"{cfg.from_name} <{cfg.utilisateur}>"
    message["To"] = destinataire
    message.set_content(email_gabarit.texte(sujet, texte))
    message.add_alternative(email_gabarit.html(sujet, texte), subtype="html")
    return message


def _envoyer_smtp(cfg: SmtpConfig, sujet: str, texte: str, destinataire: str) -> bool:
    """Envoi bloquant, exécuté hors de la boucle d'événements par l'appelant."""
    with smtplib.SMTP(cfg.hote, cfg.port, timeout=_SMTP_TIMEOUT_S) as relais:
        relais.ehlo()
        # STARTTLS et non SSL implicite : le 465 est bloqué par l'hébergeur, le 587
        # passe. Vérifié depuis un pod de production avant d'écrire ce module.
        #
        # Le CONTEXTE est explicite, et ce n'est pas une précaution de style. Appelé
        # sans lui, `starttls()` retombe sur `ssl._create_stdlib_context()`, qui ne
        # vérifie NI la chaîne de certification NI le nom d'hôte : le tunnel serait
        # chiffré mais non authentifié, et un intercepteur sur le chemin récupérerait
        # le mot de passe d'application en clair juste après la poignée de main.
        relais.starttls(context=ssl.create_default_context())
        relais.ehlo()
        relais.login(cfg.utilisateur, cfg.secret)
        relais.send_message(_message_smtp(cfg, sujet, texte, destinataire))
    return True


async def envoyer_alerte_smtp(sujet: str, texte: str, *, destinataire: str | None = None) -> bool:
    """Envoie une alerte par SMTP (fail-soft, jamais d'exception).

    Args:
        sujet: Objet du message.
        texte: Corps en texte brut.
        destinataire: Adresse cible ; défaut = ``EMAIL_TEAM``.

    Returns:
        True si le relais a accepté le message, False sinon.
    """
    cfg = lire_config_smtp()
    if cfg is None:
        logger.info("email_saute_smtp_non_configure", sujet=sujet)
        return False
    try:
        # smtplib est bloquant : on le sort de la boucle, sinon un relais lent gèlerait
        # l'API pendant tout le dialogue SMTP.
        return await asyncio.to_thread(_envoyer_smtp, cfg, sujet, texte, destinataire or cfg.team)
    except Exception as exc:  # noqa: BLE001 — une panne d'alerte n'échoue jamais le job
        logger.warning("email_smtp_echec", sujet=sujet, error=str(exc))
        return False
