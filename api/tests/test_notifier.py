"""Tests de l'acheminement du lien magique (console + SMTP mocké)."""

from __future__ import annotations

import httpx
import pytest

from app.core.config import Settings
from app.services import notifier as notifier_mod
from app.services.notifier import (
    ConsoleNotifier,
    SmtpNotifier,
    ZeptoMailNotifier,
    construire_notifier,
)


async def test_console_notifier_ne_leve_pas() -> None:
    """Le notifier console journalise le lien sans erreur."""
    await ConsoleNotifier().envoyer_lien("a@cacao.ci", "https://opencacao.ci/?auth=x")


def test_construire_notifier_defaut_console() -> None:
    assert isinstance(construire_notifier(Settings()), ConsoleNotifier)


def test_construire_notifier_smtp_si_configure() -> None:
    settings = Settings(auth_canal="smtp", smtp_host="smtp.local")
    assert isinstance(construire_notifier(settings), SmtpNotifier)


def test_construire_notifier_zeptomail_si_token() -> None:
    settings = Settings(auth_canal="zeptomail", zeptomail_token="TOK")
    assert isinstance(construire_notifier(settings), ZeptoMailNotifier)


def test_construire_notifier_zeptomail_sans_token_reste_console() -> None:
    assert isinstance(construire_notifier(Settings(auth_canal="zeptomail")), ConsoleNotifier)


class _FakeResp:
    def __init__(self, status: int, text: str = "") -> None:
        self.status_code = status
        self.text = text


class _FakeHttpx:
    def __init__(self, status: int = 202, text: str = "") -> None:
        self.status = status
        self.text = text
        self.calls: list[dict] = []

    async def post(self, url: str, json: dict, headers: dict) -> _FakeResp:
        self.calls.append({"url": url, "json": json, "headers": headers})
        return _FakeResp(self.status, self.text)

    async def aclose(self) -> None:
        return None


async def test_zeptomail_notifier_poste_le_lien() -> None:
    """ZeptoMail : POST avec le bon header d'auth, destinataire et lien (texte+html)."""
    fake = _FakeHttpx()
    notifier = ZeptoMailNotifier(
        token="TOK",
        api_url="https://api.zeptomail.com/v1.1/email",
        from_address="no-reply@opencacao.ci",
        from_name="OpenCacao",
        client=fake,
    )
    await notifier.envoyer_lien("paysan@cacao.ci", "https://opencacao.ci/?auth=jeton")

    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["headers"]["Authorization"] == "Zoho-enczapikey TOK"
    assert call["json"]["to"][0]["email_address"]["address"] == "paysan@cacao.ci"
    assert "jeton" in call["json"]["textbody"]
    assert "jeton" in call["json"]["htmlbody"]


async def test_zeptomail_notifier_erreur_ne_propage_pas() -> None:
    """Un refus ZeptoMail (ex. 403 SM_147) est journalisé, jamais propagé (fail-soft)."""
    fake = _FakeHttpx(status=403, text='{"error":{"details":[{"code":"SM_147"}]}}')
    notifier = ZeptoMailNotifier("T", "u", "f", "n", client=fake)
    await notifier.envoyer_lien("p@cacao.ci", "lien")  # ne doit pas lever
    assert len(fake.calls) == 1


async def test_zeptomail_notifier_fail_soft() -> None:
    """Une erreur réseau ne propage pas (l'utilisateur peut redemander un lien)."""

    class _Err:
        async def post(self, *_: object, **__: object) -> None:
            raise httpx.ConnectError("boom")

        async def aclose(self) -> None:
            return None

    notifier = ZeptoMailNotifier("T", "u", "f", "n", client=_Err())
    await notifier.envoyer_lien("p@cacao.ci", "lien")  # ne doit pas lever


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
