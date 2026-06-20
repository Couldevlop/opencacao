"""Tests du mailer d'alerte ZeptoMail (fail-soft, sans appel réseau réel)."""

from __future__ import annotations

import httpx
import pytest

from app.core import email


def test_lire_config_absente_sans_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sans ZEPTOMAIL_TOKEN, la config est None (envoi sauté proprement)."""
    monkeypatch.delenv("ZEPTOMAIL_TOKEN", raising=False)
    assert email.lire_config() is None


def test_lire_config_defauts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avec un token, les défauts pointent vers ZeptoMail .com et l'équipe OpenLab."""
    monkeypatch.setenv("ZEPTOMAIL_TOKEN", "jeton-test")
    monkeypatch.delenv("EMAIL_TEAM", raising=False)
    monkeypatch.delenv("ZEPTOMAIL_API_URL", raising=False)
    cfg = email.lire_config()
    assert cfg is not None
    assert cfg.api_url.endswith("zeptomail.com/v1.1/email")
    assert cfg.team == "waopron@openlabconsulting.com"


def test_corps_message_format_zeptomail(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le corps respecte le schéma ZeptoMail et échappe le HTML du texte."""
    monkeypatch.setenv("ZEPTOMAIL_TOKEN", "jeton-test")
    cfg = email.lire_config()
    assert cfg is not None
    corps = email.corps_message(cfg, "Sujet", "alerte <b>x</b>", "dest@ex.ci")
    assert corps["to"] == [{"email_address": {"address": "dest@ex.ci"}}]
    assert corps["subject"] == "Sujet"
    assert corps["textbody"] == "alerte <b>x</b>"  # texte brut conservé
    assert "&lt;b&gt;" in corps["htmlbody"]  # HTML échappé


async def test_envoyer_saute_sans_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sans token, l'envoi renvoie False sans lever ni appeler le réseau."""
    monkeypatch.delenv("ZEPTOMAIL_TOKEN", raising=False)
    assert await email.envoyer_alerte("S", "T") is False


class _FauxReponse:
    def __init__(self, erreur: Exception | None = None) -> None:
        self._erreur = erreur

    def raise_for_status(self) -> None:
        if self._erreur is not None:
            raise self._erreur


class _FauxClient:
    """Client httpx minimal : enregistre l'appel et simule la réponse."""

    def __init__(self, reponse: _FauxReponse) -> None:
        self._reponse = reponse
        self.appels: list[dict] = []

    async def post(self, url: str, json: dict, headers: dict) -> _FauxReponse:
        self.appels.append({"url": url, "json": json, "headers": headers})
        return self._reponse


async def test_envoyer_succes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avec token et réponse 2xx, l'envoi réussit et pose le bon en-tête d'auth."""
    monkeypatch.setenv("ZEPTOMAIL_TOKEN", "jeton-test")
    client = _FauxClient(_FauxReponse())
    ok = await email.envoyer_alerte("Sujet", "Texte", destinataire="x@ex.ci", client=client)
    assert ok is True
    assert client.appels[0]["headers"]["Authorization"] == "Zoho-enczapikey jeton-test"
    assert client.appels[0]["json"]["to"][0]["email_address"]["address"] == "x@ex.ci"


async def test_envoyer_echec_http_failsoft(monkeypatch: pytest.MonkeyPatch) -> None:
    """Une erreur HTTP est absorbée (False), jamais propagée."""
    monkeypatch.setenv("ZEPTOMAIL_TOKEN", "jeton-test")
    client = _FauxClient(_FauxReponse(httpx.HTTPError("boom")))
    assert await email.envoyer_alerte("S", "T", client=client) is False
