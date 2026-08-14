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
        parcelles_enabled: Active les parcelles et les captures terrain (V3, C1).
        parcelles_db_path: Chemin du fichier SQLite des parcelles (volume /data).
        captures_dir: Dossier des images de capture (volume /data).
        captures_retention_jours: Rétention des captures avant purge, en jours.
        profil_materiel: Capacités disponibles ("gpu" ou "cpu").
        rapports_enabled: Active l'atelier de livrables (V3, C3).
        rapports_db_path: Chemin du fichier SQLite des rapports (volume /data).
        rapports_retention_jours: Rétention des rapports avant purge, en jours.
        vision_enabled: Active l'analyse visuelle des captures (V3, C2).
        vision_url: URL interne du service de vision (jamais exposé publiquement).
        vision_modele: Nom du modèle de vision servi.
        vision_timeout_s: Timeout des appels au modèle de vision, en secondes.
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
    # Version applicative (tag de l'image API). Incluse dans la clé de cache pour
    # qu'un déploiement changeant le post-traitement (extraction de sources, RAG…)
    # n'aille PAS resservir d'anciennes réponses. Fixée par le déploiement
    # (roll-image.sh patche APP_VERSION dans la ConfigMap).
    app_version: str = "dev"

    redis_url: str = "redis://redis:6379/0"
    rate_limit_per_min: int = 20

    # Plafond de génération. Les réponses cacao sont brèves (consigne « 10 phrases
    # maximum » dans le prompt système) : ce plafond sert de filet de sécurité, le
    # modèle s'arrête bien avant. Réglable sans rebuild via INFERENCE_MAX_TOKENS.
    inference_max_tokens: int = 400

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
    # Reranking (F9) : on récupère un vivier large par similarité dense, puis on
    # réordonne en fusionnant la similarité dense et le recouvrement lexical
    # (mots de la question présents dans le passage ou son nom de source). Un fort
    # recouvrement lexical (>= rag_seuil_lexical) fait remonter un document
    # littéralement pertinent même si l'embedding seul le classait sous le seuil.
    rag_candidats: int = 12
    rag_poids_lexical: float = 0.35
    rag_seuil_lexical: float = 0.5
    # Récupération hybride (F10) : on complète le vivier dense par un canal BM25
    # (recall lexical sur tout le corpus) — rattrape les noms rares de maladies/
    # variétés que le dense classe hors vivier. Désactivable via RAG_HYBRIDE_ENABLED.
    rag_hybride_enabled: bool = True
    # Plafond de longueur d'un passage RAG injecté (réduit les tokens d'entrée -> la
    # latence de préremplissage CPU). Coupe à une frontière de phrase. Réglable à chaud.
    rag_passage_max_chars: int = 480

    # --- Plateforme agentique V3 ---
    # Active l'orchestrateur + les agents spécialisés (RAG/Météo/Prix/Reporting).
    # OFF par défaut : tant que le flag est OFF, le chemin V2 (ConseilService) reste
    # seul en production. Activable sans rebuild via AGENTS_ENABLED (ConfigMap).
    agents_enabled: bool = False
    # Sources de données des agents Météo/Prix (souveraineté : données factuelles,
    # jamais un LLM tiers). Météo = Open-Meteo (API publique libre, sans clé).
    meteo_timeout_s: float = 10.0
    # Prix bord-champ officiel (Conseil Café-Cacao), valeur administrée par campagne.
    # 0 = non renseigné -> l'agent Prix dégrade vers le RAG (jamais de prix inventé).
    # À mettre à jour à chaque campagne via ConfigMap (PRIX_BORD_CHAMP_FCFA_KG).
    prix_bord_champ_fcfa_kg: int = 0
    prix_campagne: str = ""
    # Global Forest Watch (agent Satellite) : clé Data API (Secret K8s, jamais en
    # ConfigMap). Vide = agent actif mais source indisponible (consigne explicite).
    # ⚠ La clé expire après UN AN (créée le 05/07/2026 -> renouveler avant 30/06/2027).
    gfw_api_key: str = ""
    gfw_timeout_s: float = 15.0
    # Cache Redis des résultats d'outils (préfixe outil:) : la pluie sous 30 min ne
    # change pas de conseil, les alertes GFW sont hebdomadaires — et il n'y a que
    # ~60 zones. Économise 1-3 s d'appels HTTP répétés + le quota GFW. 0 = coupé.
    outil_cache_meteo_ttl_s: int = 1800
    outil_cache_satellite_ttl_s: int = 86_400
    # Keepalive du préfixe KV : micro-génération périodique (max_tokens=1) pour que
    # le préfixe système (cache_prompt) reste chaud dans llama-server — le premier
    # producteur après une période calme ne repaie pas ~15-25 s de préremplissage.
    # 0 = coupé (défaut) ; prod : 600 (10 min, ~0,5 % de CPU).
    kv_keepalive_s: int = 0

    # --- Cache sémantique ---
    # Sur un miss exact, vectorise la question et sert la réponse d'une question
    # cachée sémantiquement proche (paraphrase) -> évite une génération CPU.
    # Désactivé par défaut : activable sans rebuild via SEMANTIC_CACHE_ENABLED.
    semantic_cache_enabled: bool = False
    # Dialogue naturel (formulation des clarifications par le modèle). Désactivé par
    # défaut : à activer après validation manuelle (déploiement en deux temps). Voir
    # docs/superpowers/specs/2026-07-24-dialogue-naturel-design.md.
    dialogue_naturel_enabled: bool = False
    # Similarité cosinus minimale pour servir une réponse cachée (conservateur :
    # mieux vaut un miss qu'une réponse hors sujet).
    semantic_cache_threshold: float = 0.92
    # Garde-fou lexical : couverture minimale des mots-clés de la question cachée par
    # la question entrante. Bloque un voisin sémantique au qualificatif divergent
    # (« cacaoyer adulte » vs « cacaoyer jeune »), dont la réponse diffère.
    semantic_cache_lexical_min: float = 0.75
    # Plafond d'entrées de l'index sémantique par (version, langue).
    semantic_cache_max_entries: int = 2000

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

    # --- Parcelles & captures terrain (V3, chantier C1) ---
    parcelles_enabled: bool = False
    parcelles_db_path: str = "/data/parcelles.db"
    captures_dir: str = "/data/captures"
    captures_retention_jours: int = 90
    # Quotas de capture (OWASP API4:2023). Le volume /data porte aussi les sessions,
    # l'index RAG et le journal : sans borne, des captures le satureraient et
    # emporteraient le RAG avec elles.
    captures_quota_par_appareil: int = 200
    captures_espace_libre_min_octets: int = 104_857_600

    # Profil matériel : déclare les capacités disponibles, pas le backend
    # (``inference_backend`` s'en charge). Défaut ``cpu`` : une erreur de
    # configuration dégrade le service, elle ne le casse pas.
    profil_materiel: Literal["gpu", "cpu"] = "cpu"

    # --- Atelier de livrables (V3, chantier C3) ---
    # OFF par défaut : les routes ne sont montées que si le drapeau est levé, comme
    # pour les parcelles — on vérifie d'abord que le schéma se crée bien sur /data.
    rapports_enabled: bool = False
    rapports_db_path: str = "/data/rapports.db"
    # Rétention des livrables. Un document se télécharge le jour même ; le garder
    # indéfiniment ferait croître /data, partagé avec l'index RAG et les sessions.
    # 0 = conservation indéfinie.
    rapports_retention_jours: int = 30

    # --- Analyse visuelle (V3, chantier C2) ---
    # OFF par défaut : sans VLM joignable, l'API le dit (503 + consigne ANADER),
    # elle n'invente aucune description.
    vision_enabled: bool = False
    vision_url: str = "http://vision:8000"
    vision_modele: str = "qwen3-vl"
    # Plafond volontairement bas. L'endpoint de constat est SYNCHRONE et enchaîne deux
    # générations : vision (ce timeout) puis rédaction (``request_timeout_s``). Le
    # domaine est derrière Cloudflare, qui coupe une réponse d'origine vers 100 s — le
    # 524 de la composition multi-agents (juillet 2026) est venu exactement de là.
    # Avant d'activer ``vision_enabled``, vérifier le budget CUMULÉ : soit il tient
    # sous 100 s, soit le constat passe en flux (ou 202 + polling) comme /chat/stream.
    vision_timeout_s: float = 30.0

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
    # Timeout des appels à l'inférence. Le nœud de prod (CX53, CPU/GGUF) génère en
    # ~1 min ; une question récupérant beaucoup de contexte RAG allonge encore le
    # traitement du prompt. 60 s était trop juste (503 « service indisponible »
    # sporadiques sur les requêtes lentes) -> 120 s couvre le pire cas CPU.
    request_timeout_s: float = 120.0

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
    # Plafond propre aux dépôts de capture de parcelle (/v1/parcelles), qui portent
    # légitimement des images encodées en base64. Le plafond global reste bas : on
    # ouvre une porte étroite plutôt que de relâcher la borne pour toute l'API.
    # 8 Mio couvrent 12 vues de ~200 Ko (1024 px, JPEG q0.85) avec large marge.
    captures_max_body_bytes: int = 8_388_608
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
