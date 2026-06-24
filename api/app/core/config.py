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
        sessions_enabled: Active la persistance des sessions de conversation (V2).
        sessions_db_path: Chemin du fichier SQLite des sessions (volume /data).
        sessions_max_messages: Plafond de messages par session (anti-abus).
        log_level: Niveau de log.
        log_questions: Journaliser (anonymisé) les questions pour le corpus.
        cors_origins: Origines CORS autorisées en production.
        request_timeout_s: Timeout des appels à l'inférence, en secondes.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    inference_backend: Literal["vllm", "llama-cpp"] = "vllm"
    inference_url: str = "http://inference:8000"
    model_path: str = "/models/opencacao-8b"
    model_name: str = "opencacao-8b"
    model_version: str = "0.1.0"

    redis_url: str = "redis://redis:6379/0"
    rate_limit_per_min: int = 20

    # Plafond de génération. Les réponses cacao sont brèves (consigne « SMS ») :
    # abaisser réduit la latence CPU sur les réponses longues. Réglable sans
    # rebuild via INFERENCE_MAX_TOKENS.
    inference_max_tokens: int = 512

    # Paramètres de décodage (qualité de génération). Réglés bas/conservateurs pour
    # un petit modèle CPU : réponses ancrées et factuelles plutôt que créatives.
    #  - temperature basse  -> moins d'hallucination/digression ;
    #  - top_p (noyau)      -> coupe la longue traîne improbable ;
    #  - frequency_penalty  -> réduit les répétitions et le remplissage.
    inference_temperature: float = 0.2
    inference_top_p: float = 0.9
    inference_frequency_penalty: float = 0.3

    # --- RAG (génération augmentée par récupération) ---
    # Désactivé tant que l'index n'est pas construit et le service d'embeddings prêt.
    rag_enabled: bool = False
    embeddings_url: str = "http://embeddings:8001"
    rag_index_path: str = "/data/rag_index.jsonl"
    rag_top_k: int = 3
    # Similarité cosinus minimale pour qu'un passage soit injecté (sinon ignoré).
    rag_min_similarite: float = 0.62

    # Pré-chauffage du cache au démarrage : génère une fois les questions FAQ
    # (app.application.faq) en tâche de fond -> réponses instantanées ensuite.
    prewarm_enabled: bool = True

    log_level: str = "INFO"
    # Journalisation (anonymisée) des interactions Q/R + retours 👍/👎, pour
    # constituer un jeu de données d'amélioration (boucle humain-dans-la-boucle).
    log_questions: bool = False
    # Dossier où écrire le journal JSONL (interactions.jsonl, feedback.jsonl).
    dataset_dir: str = "/data"
    # Analytique des visites (anonymisée : horodatage + pays + canal, jamais d'IP).
    log_visites: bool = True
    # Base GeoLite2 (IP -> pays, en local). Déposée sur le nœud comme les modèles.
    geoip_db_path: str = "/models/GeoLite2-Country.mmdb"

    # --- Sessions conversationnelles persistantes (V2) ---
    # Stockage durable des conversations (SQLite, bibliothèque standard : aucune
    # dépendance hors spec §2.1). Fichier sur le volume persistant /data.
    sessions_enabled: bool = True
    sessions_db_path: str = "/data/opencacao_sessions.db"
    # Plafond de messages par session (garde-fou anti-abus, appliqué côté service).
    sessions_max_messages: int = 200
    # Mémoire conversationnelle (B2) : au-delà de sessions_resume_seuil messages, les
    # tours anciens sont condensés en un résumé et seuls sessions_fenetre_messages
    # messages récents sont réinjectés mot pour mot. Borne le contexte -> latence CPU
    # maîtrisée sur le nœud CX53 (risque R1).
    sessions_fenetre_messages: int = 8
    sessions_resume_seuil: int = 16
    # RGPD (E2) : durée de rétention des conversations inactives, en jours. Au-delà,
    # elles sont purgées automatiquement (au démarrage puis une fois par jour). 0
    # désactive la purge (conservation indéfinie).
    sessions_retention_jours: int = 365

    # --- Authentification légère par lien magique (D2, optionnelle) ---
    # Désactivée par défaut : l'usage anonyme par appareil (D1) reste la norme.
    # Activée, un email vérifié donne un identifiant de compte stable qui rattache
    # les conversations à la personne (et non plus au seul navigateur).
    auth_enabled: bool = False
    auth_db_path: str = "/data/opencacao_auth.db"
    # Paramètres modifiables à chaud depuis la console (volume /data partagé) :
    # l'API les lit à chaque envoi, la console les écrit. Sert notamment à régler
    # l'adresse d'expédition sans redéploiement.
    parametres_db_path: str = "/data/opencacao_parametres.db"
    # Durée de validité d'un lien magique (minutes).
    auth_token_ttl_min: int = 20
    # Base d'URL publique pour fabriquer le lien (vide = déduite de la requête).
    auth_base_url: str = ""
    # Acheminement du lien magique :
    #  - "console"   : journalisé (souverain par défaut, DEV uniquement) ;
    #  - "zeptomail" : API HTTPS ZeptoMail (port 443) — recommandé en prod, car le
    #    SMTP (587/465) est bloqué par la NetworkPolicy d'égress du cluster ;
    #  - "smtp"      : smtplib (utile hors cluster ; bloqué en prod).
    auth_canal: Literal["console", "zeptomail", "smtp"] = "console"
    # Expéditeur (adresse vérifiée chez le fournisseur) + nom affiché.
    auth_email_from: str = "no-reply@opencacao.ci"
    auth_email_from_name: str = "OpenCacao"
    # ZeptoMail (API HTTP) — token lié à la région du compte (.com global ici).
    zeptomail_token: str = ""
    zeptomail_api_url: str = "https://api.zeptomail.com/v1.1/email"
    # SMTP (utilisé seulement si auth_canal = "smtp" ; smtplib de la stdlib).
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True

    # NoDecode : ne PAS json-décoder la valeur d'env (sinon une liste CSV comme
    # "a,b" ou "*" lève une erreur). Le validateur _split_csv ci-dessous gère le CSV.
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    request_timeout_s: float = 60.0

    # Dossier de l'interface web à servir à la racine (même origine que l'API ->
    # plus de CORS). Vide = détection auto du dossier web/ du dépôt s'il existe.
    web_dir: str = ""

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
