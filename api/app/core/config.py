"""Configuration de l'application, lue depuis l'environnement."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app import __version__


class Settings(BaseSettings):
    """Paramètres de l'API, surchargés par variables d'environnement.

    Attributes:
        inference_backend: Backend servant le modèle ("vllm" ou "llama-cpp").
        inference_url: URL interne de l'API d'inférence (OpenAI-compatible).
        model_path: Chemin du modèle servi côté inférence.
        model_name: Nom du modèle, transmis à l'API d'inférence.
        model_version: Version du modèle, exposée par /v1/version.
        redis_url: URL de connexion Redis (cache + rate-limit).
        rate_limit_per_min: Nombre de requêtes autorisées par minute et par IP.
        log_level: Niveau de log.
        log_questions: Journaliser (anonymisé) les questions pour le corpus.
        cors_origins: Origines CORS autorisées en production.
        request_timeout_s: Timeout des appels à l'inférence, en secondes.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    inference_backend: Literal["vllm", "llama-cpp"] = "vllm"
    inference_url: str = "http://inference:8000"
    model_path: str = "/models/opencacao-7b"
    model_name: str = "opencacao-7b"
    model_version: str = "0.1.0"

    redis_url: str = "redis://redis:6379/0"
    rate_limit_per_min: int = 20

    log_level: str = "INFO"
    log_questions: bool = False

    # NoDecode : ne PAS json-décoder la valeur d'env (sinon une liste CSV comme
    # "a,b" ou "*" lève une erreur). Le validateur _split_csv ci-dessous gère le CSV.
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    request_timeout_s: float = 60.0

    # --- Durcissement (OWASP) ---
    # Hôtes autorisés (TrustedHostMiddleware). "*" = tous (à restreindre en prod).
    allowed_hosts: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["*"])
    # Ne faire confiance à X-Forwarded-For que derrière un proxy de confiance.
    trust_forwarded_for: bool = False
    # Taille maximale du corps de requête, en octets (anti-DoS).
    max_body_bytes: int = 16_384
    # Exposer la doc OpenAPI/Swagger (à désactiver en production).
    enable_docs: bool = True

    api_version: str = __version__

    @field_validator("cors_origins", "allowed_hosts", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Accepte une liste CSV depuis l'environnement."""
        if isinstance(value, str):
            return [o.strip() for o in value.split(",") if o.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    """Retourne l'instance unique des paramètres (mise en cache)."""
    return Settings()
