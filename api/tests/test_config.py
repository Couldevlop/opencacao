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


def test_dialogue_naturel_desactive_par_defaut() -> None:
    from app.core.config import Settings

    assert Settings().dialogue_naturel_enabled is False


def test_vision_desactivee_par_defaut() -> None:
    """Sans VLM joignable, l'API le dit ; elle n'invente aucune description."""
    settings = Settings(_env_file=None)

    assert settings.vision_enabled is False
    assert settings.profil_materiel == "cpu"


def test_le_service_de_vision_est_interne_par_defaut() -> None:
    """Jamais exposé publiquement : l'API le consomme en interne, comme inference."""
    settings = Settings(_env_file=None)

    assert settings.vision_url == "http://vision:8000"
    assert settings.vision_timeout_s == 60.0


def test_les_reglages_de_vision_se_lisent_dans_l_environnement(monkeypatch) -> None:
    monkeypatch.setenv("VISION_ENABLED", "true")
    monkeypatch.setenv("VISION_URL", "http://vision-gpu:8000")
    monkeypatch.setenv("VISION_MODELE", "qwen3-vl-8b")
    monkeypatch.setenv("VISION_TIMEOUT_S", "90")

    settings = Settings(_env_file=None)

    assert settings.vision_enabled is True
    assert settings.vision_url == "http://vision-gpu:8000"
    assert settings.vision_modele == "qwen3-vl-8b"
    assert settings.vision_timeout_s == 90.0
