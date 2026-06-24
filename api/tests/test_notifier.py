"""Tests de l'acheminement du lien magique (console + SMTP mocké)."""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.services import notifier as notifier_mod
from app.services.notifier import ConsoleNotifier, SmtpNotifier, construire_notifier


async def test_console_notifier_ne_leve_pas() -> None:
    """Le notifier console journalise le lien sans erreur."""
    await ConsoleNotifier().envoyer_lien("a@cacao.ci", "https://opencacao.ci/?auth=x")


def test_construire_notifier_defaut_console() -> None:
    assert isinstance(construire_notifier(Settings()), ConsoleNotifier)


def test_construire_notifier_smtp_si_configure() -> None:
    settings = Settings(auth_canal="smtp", smtp_host="smtp.local")
    assert isinstance(construire_notifier(settings), SmtpNotifier)


def test_construire_notifier_smtp_sans_hote_reste_console() -> None:
    """auth_canal=smtp mais sans hôte → on retombe sur la console (jamais bloquant)."""
    assert isinstance(construire_notifier(Settings(auth_canal="smtp")), ConsoleNotifier)


class _FakeSMTP:
    """Faux serveur SMTP en mémoire (contextmanager), pour vérifier l'envoi."""

    envoye: list[object] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host, self.port = host, port

    def __enter__(self) -> _FakeSMTP:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def starttls(self) -> None:
        self.tls = True

    def login(self, user: str, password: str) -> None:
        self.user = user

    def send_message(self, message: object) -> None:
        _FakeSMTP.envoye.append(message)


async def test_smtp_notifier_envoie_le_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """SmtpNotifier construit et envoie un email contenant le lien."""
    _FakeSMTP.envoye.clear()
    monkeypatch.setattr(notifier_mod, "SMTP", _FakeSMTP)
    smtp = SmtpNotifier(
        host="smtp.local",
        port=587,
        user="u",
        password="p",
        expediteur="OpenCacao <no-reply@opencacao.ci>",
    )
    await smtp.envoyer_lien("paysan@cacao.ci", "https://opencacao.ci/?auth=jeton")

    assert len(_FakeSMTP.envoye) == 1
    message = _FakeSMTP.envoye[0]
    assert message["To"] == "paysan@cacao.ci"
    assert "jeton" in message.get_content()
