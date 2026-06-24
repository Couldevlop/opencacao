"""Acheminement du lien magique vers l'utilisateur (D2).

Trois implémentations du même contrat :

* :class:`ConsoleNotifier` — **DEV** : journalise le lien (structlog). Souverain, sans
  dépendance réseau, mais expose le jeton dans les logs (OWASP A09).
* :class:`ZeptoMailNotifier` — **PROD** : envoie le lien via l'API HTTP ZeptoMail
  (port 443, httpx). Choisi car le SMTP (587/465) est bloqué par la NetworkPolicy
  d'égress du cluster (cf. openlabconsulting/lib/email-core.ts).
* :class:`SmtpNotifier` — ``smtplib`` (stdlib). Utile hors cluster ; bloqué en prod.

``httpx`` est déjà une dépendance (client d'inférence) ; ``smtplib``/``email`` sont
dans la stdlib — aucune dépendance hors spec §2.1.
"""

from __future__ import annotations

import asyncio
from email.message import EmailMessage
from smtplib import SMTP

import httpx

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_SUJET = "Votre lien de connexion OpenCacao"


def _corps(lien: str) -> str:
    """Texte du message contenant le lien magique."""
    return (
        "Bonjour,\n\n"
        "Cliquez sur ce lien pour vous connecter à OpenCacao (valable quelques "
        f"minutes, à usage unique) :\n\n{lien}\n\n"
        "Si vous n'êtes pas à l'origine de cette demande, ignorez ce message.\n\n"
        "— OpenCacao"
    )


def _corps_html(lien: str) -> str:
    """Version HTML minimale du message (ZeptoMail exige un htmlbody)."""
    return (
        "<p>Bonjour,</p>"
        "<p>Cliquez sur ce lien pour vous connecter à OpenCacao "
        "(valable quelques minutes, à usage unique) :</p>"
        f'<p><a href="{lien}">{lien}</a></p>'
        "<p>Si vous n'êtes pas à l'origine de cette demande, ignorez ce message.</p>"
        "<p>— OpenCacao</p>"
    )


class ConsoleNotifier:
    """Journalise le lien au lieu de l'envoyer (souverain, sans dépendance réseau).

    ⚠️ Réservé au DÉVELOPPEMENT : journaliser le lien expose le jeton dans les logs
    (OWASP A09 — Security Logging). En production, utiliser :class:`SmtpNotifier`.
    """

    async def envoyer_lien(self, email: str, lien: str) -> None:
        """« Envoie » le lien en le journalisant — canal DEV (jeton exposé en logs)."""
        logger.warning(
            "lien_magique_console_dev",
            email=email,
            lien=lien,
            avertissement="canal DEV : jeton exposé en logs, utiliser SMTP en prod",
        )


class ZeptoMailNotifier:
    """Envoie le lien magique via l'API HTTP ZeptoMail (port 443, httpx).

    Choisi en production car le SMTP (587/465) est bloqué par la NetworkPolicy
    d'égress du cluster. Fail-soft : journalise et ne propage jamais (l'échec d'envoi
    ne doit pas casser la requête ; l'utilisateur peut redemander un lien).
    """

    def __init__(
        self,
        token: str,
        api_url: str,
        from_address: str,
        from_name: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Initialise le client API.

        Args:
            token: Jeton ZeptoMail (préfixe ``Zoho-enczapikey`` ajouté à l'envoi).
            api_url: URL de l'API (région .com par défaut ; le jeton est lié à la région).
            from_address: Adresse d'expédition (vérifiée chez ZeptoMail).
            from_name: Nom affiché de l'expéditeur.
            client: Client httpx injectable (tests).
        """
        self._token = token
        self._api_url = api_url
        self._from = {"address": from_address, "name": from_name}
        self._client = client or httpx.AsyncClient(timeout=10)

    @classmethod
    def from_settings(cls, settings: Settings) -> ZeptoMailNotifier:
        """Construit un notifier ZeptoMail à partir des paramètres applicatifs."""
        return cls(
            token=settings.zeptomail_token,
            api_url=settings.zeptomail_api_url,
            from_address=settings.auth_email_from,
            from_name=settings.auth_email_from_name,
        )

    async def envoyer_lien(self, email: str, lien: str) -> None:
        """POST du lien à l'API ZeptoMail (fail-soft)."""
        corps = {
            "from": self._from,
            "to": [{"email_address": {"address": email}}],
            "subject": _SUJET,
            "htmlbody": _corps_html(lien),
            "textbody": _corps(lien),
        }
        try:
            response = await self._client.post(
                self._api_url,
                json=corps,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    # ZeptoMail attend le préfixe littéral `Zoho-enczapikey`.
                    "Authorization": f"Zoho-enczapikey {self._token}",
                },
            )
            if response.status_code >= 400:
                # Le corps ZeptoMail porte la raison exacte (ex. SM_147 « Sender Address
                # not available ») — indispensable pour diagnostiquer.
                detail = (response.text or "")[:500]
                logger.warning(
                    "zeptomail_echec", email=email, status=response.status_code, detail=detail
                )
        except httpx.HTTPError as exc:
            logger.warning("zeptomail_echec", email=email, error=str(exc))

    async def close(self) -> None:
        """Ferme le client HTTP sous-jacent."""
        await self._client.aclose()


class SmtpNotifier:
    """Envoie le lien magique par email via ``smtplib`` (stdlib)."""

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        expediteur: str,
        starttls: bool = True,
    ) -> None:
        """Initialise le client SMTP.

        Args:
            host: Hôte SMTP.
            port: Port SMTP.
            user: Identifiant SMTP (peut être vide si relais sans auth).
            password: Mot de passe SMTP.
            expediteur: Adresse « From ».
            starttls: Démarrer TLS après la connexion.
        """
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._expediteur = expediteur
        self._starttls = starttls

    @classmethod
    def from_settings(cls, settings: Settings) -> SmtpNotifier:
        """Construit un notifier SMTP à partir des paramètres applicatifs."""
        return cls(
            host=settings.smtp_host,
            port=settings.smtp_port,
            user=settings.smtp_user,
            password=settings.smtp_password,
            expediteur=settings.auth_email_from,
            starttls=settings.smtp_starttls,
        )

    async def envoyer_lien(self, email: str, lien: str) -> None:
        """Envoie le lien magique par email (déporté dans un thread, smtplib bloquant)."""
        message = EmailMessage()
        message["Subject"] = _SUJET
        message["From"] = self._expediteur
        message["To"] = email
        message.set_content(_corps(lien))
        await asyncio.to_thread(self._envoyer, message)

    def _envoyer(self, message: EmailMessage) -> None:
        with SMTP(self._host, self._port, timeout=10) as smtp:
            if self._starttls:
                smtp.starttls()
            if self._user:
                smtp.login(self._user, self._password)
            smtp.send_message(message)


def construire_notifier(
    settings: Settings,
) -> ConsoleNotifier | ZeptoMailNotifier | SmtpNotifier:
    """Choisit le notifier selon ``auth_canal`` (console par défaut, souverain).

    Retombe sur la console si le canal demandé n'est pas configuré (jamais bloquant).
    """
    if settings.auth_canal == "zeptomail" and settings.zeptomail_token:
        return ZeptoMailNotifier.from_settings(settings)
    if settings.auth_canal == "smtp" and settings.smtp_host:
        return SmtpNotifier.from_settings(settings)
    return ConsoleNotifier()
