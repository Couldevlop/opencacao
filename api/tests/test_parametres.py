"""Tests du dépôt de paramètres modifiables à chaud et du masquage d'email."""

from __future__ import annotations

from pathlib import Path

from app.core.parametres import (
    CLE_EMAIL_EXPEDITEUR,
    ParametresStore,
    brouiller_email,
)


def test_brouiller_email_masque_la_partie_locale() -> None:
    assert brouiller_email("waopron@openlabconsulting.com") == "w•••••n@openlabconsulting.com"
    assert brouiller_email("ab@x.ci") == "a•@x.ci"  # partie locale courte
    assert brouiller_email("a@x.ci").endswith("@x.ci")
    assert brouiller_email("") == ""
    assert brouiller_email("sans-arobase") == "•••"


async def test_store_definir_puis_obtenir(tmp_path: Path) -> None:
    store = ParametresStore(tmp_path / "p.db")
    await store.initialiser()
    assert store.pret is True
    assert await store.obtenir(CLE_EMAIL_EXPEDITEUR) is None  # absent au départ

    await store.definir(CLE_EMAIL_EXPEDITEUR, "waopron@openlabconsulting.com")
    assert await store.obtenir(CLE_EMAIL_EXPEDITEUR) == "waopron@openlabconsulting.com"

    # écrasement
    await store.definir(CLE_EMAIL_EXPEDITEUR, "autre@openlabconsulting.com")
    assert await store.obtenir(CLE_EMAIL_EXPEDITEUR) == "autre@openlabconsulting.com"


async def test_obtenir_tolere_base_absente(tmp_path: Path) -> None:
    """Sans initialisation (table absente), obtenir renvoie None sans lever."""
    store = ParametresStore(tmp_path / "vide.db")
    assert await store.obtenir(CLE_EMAIL_EXPEDITEUR) is None
