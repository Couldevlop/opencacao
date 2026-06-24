"""Tests du dépôt et du cas d'usage d'authentification par lien magique (D2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.application.auth_service import AuthService
from app.core.auth_store import AuthStore, hacher_token


async def _store(tmp_path: Path) -> AuthStore:
    store = AuthStore(tmp_path / "auth.db")
    await store.initialiser()
    assert store.pret is True
    return store


# --- Dépôt SQLite ---


async def test_lien_valide_consomme_une_seule_fois(tmp_path: Path) -> None:
    """Un lien valide renvoie l'email une fois, puis devient inutilisable."""
    store = await _store(tmp_path)
    expire = datetime.now(UTC) + timedelta(minutes=20)
    await store.creer_lien("paysan@ci", hacher_token("jeton-1"), expire)

    assert await store.consommer_lien(hacher_token("jeton-1")) == "paysan@ci"
    assert await store.consommer_lien(hacher_token("jeton-1")) is None  # déjà utilisé


async def test_lien_expire_refuse(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    passe = datetime.now(UTC) - timedelta(minutes=1)
    await store.creer_lien("paysan@ci", hacher_token("jeton-2"), passe)
    assert await store.consommer_lien(hacher_token("jeton-2")) is None


async def test_lien_inconnu_refuse(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    assert await store.consommer_lien(hacher_token("inconnu")) is None


async def test_compte_stable_par_email(tmp_path: Path) -> None:
    """Le même email donne toujours le même identifiant de compte ; deux emails diffèrent."""
    store = await _store(tmp_path)
    a1 = await store.compte_pour("a@ci")
    a2 = await store.compte_pour("a@ci")
    b = await store.compte_pour("b@ci")
    assert a1 == a2 and a1.startswith("acct_")
    assert a1 != b


async def test_purge_liens_expires(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    await store.creer_lien("a@ci", hacher_token("vieux"), datetime.now(UTC) - timedelta(minutes=1))
    await store.creer_lien("a@ci", hacher_token("frais"), datetime.now(UTC) + timedelta(minutes=20))
    assert await store.purger_liens_expires() == 1
    assert await store.consommer_lien(hacher_token("frais")) == "a@ci"


# --- Cas d'usage (service) ---


class _FakeNotifier:
    def __init__(self) -> None:
        self.envois: list[tuple[str, str]] = []

    async def envoyer_lien(self, email: str, lien: str) -> None:
        self.envois.append((email, lien))


async def test_service_envoie_un_lien_puis_verifie(tmp_path: Path) -> None:
    """Le service fabrique un lien (avec jeton), l'achemine, puis le vérifie."""
    store = await _store(tmp_path)
    notifier = _FakeNotifier()
    service = AuthService(store, notifier, ttl_minutes=20)

    await service.demander_lien("paysan@ci", "https://opencacao.ci")
    assert len(notifier.envois) == 1
    email, lien = notifier.envois[0]
    assert email == "paysan@ci"
    assert lien.startswith("https://opencacao.ci/?auth=")
    token = lien.split("auth=")[1]

    identite = await service.verifier(token)
    assert identite is not None
    assert identite.email == "paysan@ci"
    assert identite.account_id.startswith("acct_")
    # Usage unique : un second appel échoue.
    assert await service.verifier(token) is None


async def test_service_verifie_jeton_invalide(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    service = AuthService(store, _FakeNotifier(), ttl_minutes=20)
    assert await service.verifier("jeton-bidon") is None
