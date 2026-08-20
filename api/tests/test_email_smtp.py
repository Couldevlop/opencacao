"""Canal SMTP des alertes — le repli quand le fournisseur HTTP refuse.

ZeptoMail répond 429 depuis le 19/08 : aucune alerte ne part, et le rappel du soir
— seul garde-fou contre un pod loué resté allumé — est muet. Le projet Maturia envoie
depuis des mois par SMTP Gmail sur le même domaine ; on reprend ce canal.

Vérifié sur le cluster avant d'écrire une ligne : `smtp.gmail.com:587` est ATTEIGNABLE
et STARTTLS s'y négocie. Le commentaire du code affirmant que l'égress bloquait le SMTP
était périmé — aucune NetworkPolicy du namespace ne couvre l'égress.
"""

from __future__ import annotations

import pytest

from app.core import email


class _SmtpFactice:
    """Serveur SMTP simulé : retient ce qu'on lui a demandé, dans l'ordre."""

    dernier: _SmtpFactice | None = None

    def __init__(self, hote: str, port: int, timeout: float = 0) -> None:
        self.hote, self.port = hote, port
        self.gestes: list[str] = []
        self.identifiants: tuple[str, str] | None = None
        self.message = ""
        _SmtpFactice.dernier = self

    def __enter__(self) -> _SmtpFactice:
        return self

    def __exit__(self, *_: object) -> None:
        self.gestes.append("quit")

    def ehlo(self) -> None:
        self.gestes.append("ehlo")

    def starttls(self) -> None:
        self.gestes.append("starttls")

    def login(self, utilisateur: str, secret: str) -> None:
        self.gestes.append("login")
        self.identifiants = (utilisateur, secret)

    def send_message(self, message) -> None:
        self.gestes.append("send")
        self.message = message.as_string()


@pytest.fixture
def smtp(monkeypatch) -> type[_SmtpFactice]:
    # `dernier` est un attribut de CLASSE : sans remise à zéro, un test hériterait de
    # l'instance du précédent et « aucune connexion » serait indiscernable de « la
    # connexion d'avant ».
    _SmtpFactice.dernier = None
    monkeypatch.setattr(email.smtplib, "SMTP", _SmtpFactice)
    monkeypatch.setenv("EMAIL_CANAL", "smtp")
    monkeypatch.setenv("SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "compte@gmail.com")
    monkeypatch.setenv("SMTP_PASSWORD", "mot-de-passe-application")
    monkeypatch.setenv("EMAIL_TEAM", "equipe@openlabconsulting.com")
    return _SmtpFactice


@pytest.mark.asyncio
async def test_le_canal_smtp_envoie_reellement(smtp) -> None:
    """Le geste qui compte : la connexion, le chiffrement, l'authentification, l'envoi."""
    assert await email.envoyer_alerte("Sujet", "Corps") is True
    assert smtp.dernier.gestes == ["ehlo", "starttls", "ehlo", "login", "send", "quit"]
    assert (smtp.dernier.hote, smtp.dernier.port) == ("smtp.gmail.com", 587)


@pytest.mark.asyncio
async def test_l_expediteur_est_le_compte_authentifie(smtp, monkeypatch) -> None:
    """Gmail et M365 EXIGENT que le From soit le compte authentifié.

    Laisser passer `EMAIL_FROM` ici ferait rejeter — ou réécrire en silence — tous les
    envois. Seul le nom affiché reste libre.
    """
    monkeypatch.setenv("EMAIL_FROM", "waopron@openlabconsulting.com")
    monkeypatch.setenv("EMAIL_FROM_NAME", "OpenCacao")
    await email.envoyer_alerte("Sujet", "Corps")
    assert "compte@gmail.com" in smtp.dernier.message
    assert "OpenCacao" in smtp.dernier.message


@pytest.mark.asyncio
async def test_le_sujet_et_le_corps_arrivent(smtp) -> None:
    await email.envoyer_alerte("Le pod tourne encore", "Pensez à l'arrêter.")
    assert "Le pod tourne encore" in smtp.dernier.message


@pytest.mark.asyncio
async def test_le_destinataire_peut_etre_impose(smtp) -> None:
    await email.envoyer_alerte("Sujet", "Corps", destinataire="autre@exemple.test")
    assert "autre@exemple.test" in smtp.dernier.message


@pytest.mark.asyncio
async def test_une_panne_smtp_ne_leve_jamais(monkeypatch, smtp) -> None:
    """Fail-soft : une panne d'alerte ne doit pas faire échouer le job qui l'émet."""

    def _casse(*_a: object, **_k: object):
        raise OSError("connexion refusée")

    monkeypatch.setattr(email.smtplib, "SMTP", _casse)
    assert await email.envoyer_alerte("Sujet", "Corps") is False


@pytest.mark.asyncio
async def test_sans_identifiants_l_envoi_est_saute(monkeypatch, smtp) -> None:
    """Comme pour ZeptoMail : une configuration absente saute, elle ne casse pas."""
    monkeypatch.delenv("SMTP_PASSWORD")
    assert await email.envoyer_alerte("Sujet", "Corps") is False
    assert smtp.dernier is None, "aucune connexion ne doit être tentée sans identifiants"


@pytest.mark.asyncio
async def test_le_canal_zeptomail_reste_le_defaut(monkeypatch) -> None:
    """Le repli SMTP ne doit pas changer le comportement de l'existant.

    Sans `EMAIL_CANAL`, on continue d'appeler ZeptoMail — les déploiements qui
    fonctionnent ne doivent pas basculer parce qu'on a ajouté une option.
    """
    monkeypatch.delenv("EMAIL_CANAL", raising=False)
    monkeypatch.delenv("ZEPTOMAIL_TOKEN", raising=False)
    monkeypatch.setattr(email.smtplib, "SMTP", _SmtpFactice)
    _SmtpFactice.dernier = None
    assert await email.envoyer_alerte("Sujet", "Corps") is False
    assert _SmtpFactice.dernier is None, "aucune connexion SMTP ne doit être tentée"
