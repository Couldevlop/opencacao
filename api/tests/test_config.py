"""Tests de configuration — parsing CSV des champs liste depuis l'environnement.

Régression : avec pydantic-settings, les champs ``list[str]`` sont JSON-décodés
depuis l'environnement, ce qui fait échouer une valeur CSV comme ``*`` ou
``a,b``. L'annotation ``NoDecode`` + le validateur ``_split_csv`` doivent
permettre le format CSV (celui du .env.example).
"""

from __future__ import annotations

from app.core.config import Settings


def test_listes_acceptent_le_csv(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://a.ci, https://b.ci")
    monkeypatch.setenv("ALLOWED_HOSTS", "*")

    settings = Settings(_env_file=None)

    assert settings.cors_origins == ["https://a.ci", "https://b.ci"]
    assert settings.allowed_hosts == ["*"]


def test_cors_origins_vide_donne_liste_vide(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "")

    settings = Settings(_env_file=None)

    assert settings.cors_origins == []


def test_defauts_sans_env():
    settings = Settings(_env_file=None)

    assert settings.cors_origins == []
    assert settings.allowed_hosts == ["*"]
