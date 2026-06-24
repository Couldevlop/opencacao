"""Acheminement du lien magique vers l'utilisateur (D2).

Deux implémentations du même contrat :

* :class:`ConsoleNotifier` — **par défaut, souverain** : journalise le lien (structlog).
  Suffit en démo/dev et n'introduit aucune dépendance ni service externe.
* :class:`SmtpNotifier` — envoie le lien par email via ``smtplib`` (bibliothèque
  standard). Activé seulement si ``auth_canal = "smtp"`` et un serveur SMTP configuré.

Aucune dépendance hors spec §2.1 : ``smtplib`` et ``email`` sont dans la stdlib.
"""

from __future__ import annotations

import asyncio
from email.message import EmailMessage
from smtplib import SMTP

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


def construire_notifier(settings: Settings) -> ConsoleNotifier | SmtpNotifier:
    """Choisit le notifier selon ``auth_canal`` (console par défaut, souverain)."""
    if settings.auth_canal == "smtp" and settings.smtp_host:
        return SmtpNotifier.from_settings(settings)
    return ConsoleNotifier()
