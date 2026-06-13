"""Construction RAG du corpus Q/R OpenCacao à partir des documents officiels.

Pipeline ponctuel d'enrichissement (CLAUDE §5.2, §13). Il télécharge les
documents publics de la filière (ANADER, CNRA, Conseil du Café-Cacao, FAO/ICCO)
listés dans ``corpus/sources/sources_officielles.yaml``, en extrait le texte,
le découpe en passages, construit un index sémantique local
(sentence-transformers — aucun service externe), puis demande à un LLM **local**
OpenAI-compatible (vLLM/Mistral) de rédiger des paires question/réponse
strictement ancrées dans chaque passage, en français accessible et avec citation
de la source. Chaque paire est validée par les mêmes règles que
``enrich_corpus.py`` (longueurs, source citée, refus de tout dosage
phytosanitaire chiffré) avant d'être écrite au format instruction-tuning.

Souveraineté : embeddings et génération tournent sur infrastructure locale.
Le seul appel réseau sortant est le téléchargement initial des documents publics.

Usage :
    python training/scripts/build_corpus_rag.py \\
        --target 5000 \\
        --out corpus/corpus_cacao_rag.jsonl \\
        --llm-base-url http://localhost:8000 \\
        --llm-modele opencacao-7b

Variables d'environnement (fallback des options) :
    CORPUS_LLM_BASE_URL, CORPUS_LLM_MODEL, CORPUS_LLM_API_KEY, CORPUS_EMBED_MODEL
"""

from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np
import structlog
import yaml

import enrich_corpus

logger = structlog.get_logger("build_corpus_rag")

# --- Constantes de découpage et de génération -------------------------------

TAILLE_CHUNK = 900
CHEVAUCHEMENT_CHUNK = 150
LONGUEUR_CHUNK_MIN = 250  # en deçà : passage trop maigre pour générer du Q/R.
SEUIL_DEDUP_CHUNK = 0.92  # cosinus au-delà duquel deux passages sont redondants.
SEUIL_DEDUP_QUESTION = 0.90  # cosinus au-delà duquel deux questions sont doublons.
PAIRES_PAR_CHUNK = 3
# Nombre de requêtes LLM concurrentes. vLLM agrège les requêtes (batching
# continu) : la concurrence est ce qui permet de générer le corpus en ~1 h au
# lieu de plusieurs heures en séquentiel. La validation/déduplication reste
# sérialisée (thread principal) pour garder un état déterministe.
CONCURRENCE_DEFAUT = 16
MODELE_EMBED_DEFAUT = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
TIMEOUT_TELECHARGEMENT = 60
TIMEOUT_LLM = 180

# Certains serveurs institutionnels bloquent les clients non navigateur (403)
# ou présentent une chaîne TLS incomplète : on se présente comme un navigateur
# et on valide via le bundle certifi si disponible.
_UA_NAVIGATEUR = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _contexte_ssl() -> ssl.SSLContext:
    """Retourne un contexte TLS adossé au bundle certifi si présent."""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()

# Réutilise les bornes et le filtre anti-dosage du validateur officiel.
SOURCES_RECONNUES = enrich_corpus.SOURCES

_SYSTEM_PROMPT = (
    "Tu es un agronome ivoirien qui rédige un corpus de formation pour un "
    "assistant destiné aux producteurs de cacao de Côte d'Ivoire. Tu écris en "
    "français simple, bienveillant et concret, comme à un planteur. Tu ne "
    "t'appuies QUE sur l'extrait fourni : n'invente aucun fait, aucun chiffre "
    "absent de l'extrait. Tu n'indiques JAMAIS de dosage chiffré de produit "
    "phytosanitaire (fongicide, insecticide, herbicide) ; pour ces sujets, tu "
    "renvoies vers l'agent ANADER local. Chaque réponse se termine par la "
    "mention exacte « Sources : {source}. »"
)

_USER_PROMPT = (
    "Extrait officiel ({source}, {titre}, page {page}) :\n"
    "\"\"\"\n{extrait}\n\"\"\"\n\n"
    "Angle demandé : {angle_directive}\n"
    "Rédige jusqu'à {k} paires question/réponse distinctes qu'un producteur de "
    "cacao pourrait réellement poser SOUS CET ANGLE, et dont la réponse découle "
    "directement de cet extrait. Si l'angle ne s'applique pas à l'extrait, "
    "renvoie moins de paires, voire un tableau vide. Contraintes :\n"
    "- Question : 10 à 200 caractères, naturelle, en français.\n"
    "- Réponse : 60 à 1200 caractères, pratique, fidèle à l'extrait.\n"
    "- Aucun dosage chiffré de produit phytosanitaire.\n"
    "- Termine chaque réponse par « Sources : {source}. »\n"
    "Réponds UNIQUEMENT par un tableau JSON valide, sans texte autour, au "
    'format : [{{"instruction": "...", "output": "..."}}]'
)

# Angles de questionnement : un même passage porte plusieurs questions
# RÉELLEMENT distinctes (pas des paraphrases). Multiplier les angles augmente
# le nombre de paires fidèles sans extrapoler hors-source ; la déduplication
# sémantique élimine les recoupements entre angles.
ANGLES: tuple[tuple[str, str], ...] = (
    ("symptomes", "reconnaître les signes/symptômes décrits dans l'extrait."),
    ("action", "quelle action concrète mener (que faire, comment intervenir)."),
    ("prevention", "comment prévenir ou éviter le problème en amont."),
    ("calendrier", "quand agir : période, fréquence, stade de la culture."),
    ("cause", "la cause, l'origine ou le mode de propagation expliqué."),
    ("consequence", "les conséquences sur la plantation, le rendement ou la qualité."),
    ("debutant", "une question courte et simple d'un planteur débutant (style SMS)."),
    ("bonnes_pratiques", "les bonnes pratiques générales recommandées dans l'extrait."),
)


# --- Modèles de données ------------------------------------------------------


@dataclass(frozen=True)
class SourceDoc:
    """Document officiel décrit dans le manifeste.

    Attributes:
        id: Identifiant court et stable du document.
        source: Nom de la source pour citation (doit figurer dans SOURCES).
        titre: Titre lisible du document.
        url: URL de téléchargement du PDF public.
        annee: Année de publication.
    """

    id: str
    source: str
    titre: str
    url: str
    annee: int


@dataclass
class Chunk:
    """Passage de texte ancré dans un document source.

    Attributes:
        doc: Document d'origine.
        page: Numéro de page (1-indexé).
        texte: Contenu textuel nettoyé du passage.
    """

    doc: SourceDoc
    page: int
    texte: str


@dataclass
class StatistiquesRun:
    """Compteurs d'un run de construction du corpus."""

    chunks_total: int = 0
    chunks_utilises: int = 0
    paires_generees: int = 0
    paires_rejetees: int = 0
    paires_doublons: int = 0
    paires_ecrites: int = 0
    erreurs_llm: int = 0
    rejets_par_motif: dict[str, int] = field(default_factory=dict)


# --- Protocoles (pour l'injection en test) ----------------------------------


class Embedder(Protocol):
    """Encodeur de phrases en vecteurs denses normalisés."""

    def encoder(self, textes: list[str]) -> np.ndarray:
        """Encode une liste de textes en matrice (n, d) de vecteurs unitaires."""
        ...


class LLMClient(Protocol):
    """Client de génération de texte (chat completion)."""

    def generer(self, system: str, user: str) -> str:
        """Retourne la complétion brute du modèle pour le couple (system, user)."""
        ...


# --- Implémentations locales (souveraines) ----------------------------------


class LocalEmbedder:
    """Encodeur sentence-transformers chargé localement (aucun appel réseau)."""

    def __init__(self, modele: str) -> None:
        """Initialise l'encodeur.

        Args:
            modele: Identifiant du modèle sentence-transformers à charger.
        """
        from sentence_transformers import SentenceTransformer

        self._modele = SentenceTransformer(modele)

    def encoder(self, textes: list[str]) -> np.ndarray:
        """Encode et normalise les textes en vecteurs unitaires.

        Args:
            textes: Textes à encoder.

        Returns:
            Matrice numpy (n, d) de vecteurs L2-normalisés.
        """
        vecteurs = self._modele.encode(
            textes, normalize_embeddings=True, show_progress_bar=False
        )
        return np.asarray(vecteurs, dtype=np.float32)


class LocalLLMClient:
    """Client vers un serveur LLM local OpenAI-compatible (vLLM/llama-cpp).

    N'utilise que ``urllib`` (bibliothèque standard) : aucun SDK propriétaire,
    aucune dépendance à un service externe (CLAUDE §1.3).
    """

    def __init__(
        self,
        base_url: str,
        modele: str,
        api_key: str | None = None,
        temperature: float = 0.4,
        max_tokens: int = 1024,
    ) -> None:
        """Initialise le client.

        Args:
            base_url: URL de base du serveur (ex. ``http://localhost:8000``).
            modele: Nom du modèle servi.
            api_key: Jeton optionnel (les serveurs locaux n'en exigent pas).
            temperature: Température d'échantillonnage.
            max_tokens: Plafond de tokens générés par appel.
        """
        self._base_url = base_url.rstrip("/")
        self._endpoint = self._base_url + "/v1/chat/completions"
        self._modele = modele
        self._api_key = api_key
        self._temperature = temperature
        self._max_tokens = max_tokens

    def verifier(self) -> bool:
        """Vérifie que le serveur LLM répond (préflight avant génération).

        Returns:
            ``True`` si l'endpoint ``/v1/models`` est joignable, sinon ``False``.
        """
        entetes = {}
        if self._api_key:
            entetes["Authorization"] = f"Bearer {self._api_key}"
        requete = urllib.request.Request(  # noqa: S310 - endpoint local contrôlé
            self._base_url + "/v1/models", headers=entetes, method="GET"
        )
        try:
            with urllib.request.urlopen(requete, timeout=10) as reponse:  # noqa: S310
                return 200 <= reponse.status < 300
        except (urllib.error.URLError, TimeoutError, OSError):
            return False

    def generer(self, system: str, user: str) -> str:
        """Appelle le serveur de chat completion et retourne le contenu texte.

        Args:
            system: Message système.
            user: Message utilisateur.

        Returns:
            Le contenu textuel de la première complétion.

        Raises:
            urllib.error.URLError: En cas d'échec réseau.
            ValueError: Si la réponse n'a pas la forme attendue.
        """
        charge = {
            "model": self._modele,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
        }
        donnees = json.dumps(charge).encode("utf-8")
        entetes = {"Content-Type": "application/json"}
        if self._api_key:
            entetes["Authorization"] = f"Bearer {self._api_key}"

        requete = urllib.request.Request(  # noqa: S310 - endpoint local contrôlé
            self._endpoint, data=donnees, headers=entetes, method="POST"
        )
        with urllib.request.urlopen(  # noqa: S310
            requete, timeout=TIMEOUT_LLM
        ) as reponse:
            corps = json.loads(reponse.read().decode("utf-8"))

        try:
            return str(corps["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"Réponse LLM inattendue : {corps!r}") from exc


# --- Étapes du pipeline ------------------------------------------------------


def charger_manifeste(chemin: Path) -> list[SourceDoc]:
    """Charge la liste des documents officiels depuis le manifeste YAML.

    Args:
        chemin: Chemin du fichier ``sources_officielles.yaml``.

    Returns:
        Liste des documents déclarés.

    Raises:
        ValueError: Si une source citée n'est pas reconnue par le validateur.
    """
    donnees = yaml.safe_load(chemin.read_text(encoding="utf-8"))
    documents: list[SourceDoc] = []
    for item in donnees.get("documents", []):
        if item["source"] not in SOURCES_RECONNUES:
            raise ValueError(
                f"Source '{item['source']}' non reconnue (doc {item['id']}). "
                f"Attendu l'une de : {SOURCES_RECONNUES}."
            )
        documents.append(
            SourceDoc(
                id=item["id"],
                source=item["source"],
                titre=item["titre"],
                url=item["url"],
                annee=int(item["annee"]),
            )
        )
    return documents


# Gestionnaire de téléchargement WordPress (cnra.ci) : la page /download/<slug>/
# renvoie du HTML dont le lien réel du PDF porte ?wpdmdl=<id> ou data-downloadurl.
_RE_DATA_DL = re.compile(r'data-downloadurl="([^"]+)"', re.IGNORECASE)
_RE_WPDMDL = re.compile(r"wpdmdl=(\d+)", re.IGNORECASE)

# Hôtes officiels dont la chaîne TLS est incomplète côté serveur : on tolère
# l'absence de vérification du certificat UNIQUEMENT pour ces domaines connus
# (documents publics de la filière), jamais en général.
_HOTES_TLS_TOLERE = frozenset({"ars1000.conseilcafecacao.ci"})


def _recuperer(url: str, verifier_tls: bool = True) -> bytes:
    """Effectue un GET et retourne le corps brut.

    Args:
        url: URL à récupérer.
        verifier_tls: Si ``False``, ne vérifie pas le certificat TLS (réservé
            aux hôtes officiels listés dans ``_HOTES_TLS_TOLERE``).

    Returns:
        Le contenu binaire de la réponse.
    """
    if verifier_tls:
        contexte = _contexte_ssl()
    else:
        contexte = ssl.create_default_context()
        contexte.check_hostname = False
        contexte.verify_mode = ssl.CERT_NONE

    requete = urllib.request.Request(  # noqa: S310 - sources officielles listées
        url, headers={"User-Agent": _UA_NAVIGATEUR, "Accept": "application/pdf,*/*"}
    )
    with urllib.request.urlopen(  # noqa: S310
        requete, timeout=TIMEOUT_TELECHARGEMENT, context=contexte
    ) as reponse:
        return reponse.read()


def _recuperer_tolerant(url: str) -> bytes:
    """GET avec repli sans vérification TLS pour les hôtes officiels listés.

    Args:
        url: URL à récupérer.

    Returns:
        Le contenu binaire de la réponse.

    Raises:
        urllib.error.URLError: Si la récupération échoue malgré le repli.
    """
    try:
        return _recuperer(url, verifier_tls=True)
    except urllib.error.URLError as exc:
        hote = urllib.parse.urlsplit(url).hostname or ""
        if isinstance(exc.reason, ssl.SSLError) and hote in _HOTES_TLS_TOLERE:
            logger.warning("tls_tolere", hote=hote)
            return _recuperer(url, verifier_tls=False)
        raise


def _resoudre_lien_wpdm(html: bytes, url_base: str) -> str | None:
    """Extrait l'URL réelle du PDF depuis une page WordPress Download Manager.

    Args:
        html: Corps HTML de la page ``/download/<slug>/``.
        url_base: URL de la page (sert de base pour la forme ``?wpdmdl=``).

    Returns:
        L'URL directe du fichier, ou ``None`` si introuvable.
    """
    texte = html.decode("utf-8", errors="ignore")
    correspondance = _RE_DATA_DL.search(texte)
    if correspondance:
        return correspondance.group(1).replace("&amp;", "&")
    correspondance = _RE_WPDMDL.search(texte)
    if correspondance:
        separateur = "&" if "?" in url_base else "?"
        return f"{url_base.rstrip('/')}/{separateur}wpdmdl={correspondance.group(1)}"
    return None


def telecharger(doc: SourceDoc, dossier_brut: Path) -> Path | None:
    """Télécharge (avec cache) le PDF d'un document officiel.

    Gère les pages WordPress Download Manager (cnra.ci) : si l'URL renvoie du
    HTML, l'URL réelle du PDF (``?wpdmdl=``) est extraite puis re-téléchargée.

    Args:
        doc: Document à récupérer.
        dossier_brut: Dossier de cache des PDF bruts.

    Returns:
        Le chemin du PDF local, ou ``None`` si le téléchargement échoue.
    """
    dossier_brut.mkdir(parents=True, exist_ok=True)
    cible = dossier_brut / f"{doc.id}.pdf"
    if cible.exists() and cible.stat().st_size > 0:
        logger.info("pdf_cache", doc=doc.id, chemin=str(cible))
        return cible

    try:
        contenu = _recuperer_tolerant(doc.url)
        if not contenu.startswith(b"%PDF"):
            lien = _resoudre_lien_wpdm(contenu, doc.url)
            if lien is None:
                logger.error("pas_un_pdf", doc=doc.id, url=doc.url)
                return None
            logger.info("wpdm_resolu", doc=doc.id, lien=lien)
            contenu = _recuperer_tolerant(lien)
    except (urllib.error.URLError, TimeoutError) as exc:
        logger.error("telechargement_echec", doc=doc.id, url=doc.url, erreur=str(exc))
        return None

    if not contenu.startswith(b"%PDF"):
        logger.error("pas_un_pdf", doc=doc.id, url=doc.url)
        return None

    cible.write_bytes(contenu)
    logger.info("pdf_telecharge", doc=doc.id, octets=len(contenu))
    return cible


def _nettoyer_texte(brut: str) -> str:
    """Normalise les espaces et retire les coupures de mots en fin de ligne."""
    sans_cesure = re.sub(r"-\n", "", brut)
    espaces = re.sub(r"[ \t\r\f\v]+", " ", sans_cesure.replace("\n", " "))
    return espaces.strip()


def extraire_pages(pdf: Path) -> list[tuple[int, str]]:
    """Extrait le texte page par page d'un PDF.

    Args:
        pdf: Chemin du fichier PDF.

    Returns:
        Liste de couples (numéro de page 1-indexé, texte nettoyé).
    """
    from pypdf import PdfReader

    lecteur = PdfReader(str(pdf))
    pages: list[tuple[int, str]] = []
    for index, page in enumerate(lecteur.pages, start=1):
        texte = _nettoyer_texte(page.extract_text() or "")
        if texte:
            pages.append((index, texte))
    return pages


def decouper_en_chunks(
    doc: SourceDoc,
    pages: list[tuple[int, str]],
    taille: int = TAILLE_CHUNK,
    chevauchement: int = CHEVAUCHEMENT_CHUNK,
) -> list[Chunk]:
    """Découpe le texte d'un document en passages chevauchants.

    Args:
        doc: Document source (pour la traçabilité).
        pages: Pages extraites (numéro, texte).
        taille: Taille cible d'un passage en caractères.
        chevauchement: Recouvrement entre passages consécutifs.

    Returns:
        Liste des passages d'au moins ``LONGUEUR_CHUNK_MIN`` caractères.
    """
    pas = max(taille - chevauchement, 1)
    chunks: list[Chunk] = []
    for page, texte in pages:
        for debut in range(0, len(texte), pas):
            morceau = texte[debut : debut + taille].strip()
            if len(morceau) >= LONGUEUR_CHUNK_MIN:
                chunks.append(Chunk(doc=doc, page=page, texte=morceau))
            if debut + taille >= len(texte):
                break
    return chunks


def _indices_diversifies(embeddings: np.ndarray, seuil: float) -> list[int]:
    """Sélectionne gloutonnement des vecteurs peu redondants entre eux.

    Args:
        embeddings: Matrice (n, d) de vecteurs unitaires.
        seuil: Cosinus maximal toléré avec un vecteur déjà retenu.

    Returns:
        Indices des éléments retenus, dans l'ordre d'origine.
    """
    retenus: list[int] = []
    if len(embeddings) == 0:
        return retenus
    matrice_retenue = np.empty((0, embeddings.shape[1]), dtype=np.float32)
    for i in range(len(embeddings)):
        if matrice_retenue.shape[0] == 0:
            retenus.append(i)
            matrice_retenue = embeddings[i : i + 1]
            continue
        sims = matrice_retenue @ embeddings[i]
        if float(sims.max()) < seuil:
            retenus.append(i)
            matrice_retenue = np.vstack([matrice_retenue, embeddings[i : i + 1]])
    return retenus


def deduppliquer_chunks(
    chunks: list[Chunk], embedder: Embedder, seuil: float = SEUIL_DEDUP_CHUNK
) -> list[Chunk]:
    """Retire les passages quasi identiques pour diversifier les contextes.

    Args:
        chunks: Passages candidats.
        embedder: Encodeur sémantique.
        seuil: Seuil de cosinus de redondance.

    Returns:
        Sous-ensemble diversifié des passages.
    """
    if not chunks:
        return []
    embeddings = embedder.encoder([c.texte for c in chunks])
    indices = _indices_diversifies(embeddings, seuil)
    return [chunks[i] for i in indices]


def construire_prompt(
    chunk: Chunk, k: int, angle_directive: str = "toute question pertinente."
) -> tuple[str, str]:
    """Construit le couple (system, user) pour générer ``k`` paires.

    Args:
        chunk: Passage source à exploiter.
        k: Nombre de paires demandées.
        angle_directive: Consigne d'angle orientant le type de questions.

    Returns:
        Le message système et le message utilisateur formatés.
    """
    system = _SYSTEM_PROMPT.format(source=chunk.doc.source)
    user = _USER_PROMPT.format(
        source=chunk.doc.source,
        titre=chunk.doc.titre,
        page=chunk.page,
        extrait=chunk.texte,
        k=k,
        angle_directive=angle_directive,
    )
    return system, user


def parser_reponse_llm(brut: str) -> list[dict[str, str]]:
    """Extrait le tableau JSON de paires d'une complétion LLM.

    Tolère un éventuel texte parasite autour du tableau JSON.

    Args:
        brut: Texte renvoyé par le modèle.

    Returns:
        Liste de dictionnaires possédant les clés ``instruction`` et ``output``
        (liste vide si aucun JSON exploitable n'est trouvé).
    """
    debut = brut.find("[")
    fin = brut.rfind("]")
    if debut == -1 or fin == -1 or fin <= debut:
        return []
    try:
        donnees = json.loads(brut[debut : fin + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(donnees, list):
        return []

    paires: list[dict[str, str]] = []
    for item in donnees:
        if (
            isinstance(item, dict)
            and isinstance(item.get("instruction"), str)
            and isinstance(item.get("output"), str)
        ):
            paires.append(
                {
                    "instruction": item["instruction"].strip(),
                    "input": "",
                    "output": item["output"].strip(),
                }
            )
    return paires


def normaliser_instruction(instruction: str) -> str:
    """Normalise une question pour la détection de doublons exacts.

    Args:
        instruction: Question brute.

    Returns:
        Forme minuscule, sans accents ni ponctuation superflue.
    """
    sans_accents = "".join(
        c
        for c in unicodedata.normalize("NFD", instruction)
        if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"[^a-z0-9 ]+", "", sans_accents.lower()).strip()


def paire_valide(paire: dict[str, str]) -> tuple[bool, str]:
    """Valide une paire avec les règles officielles du corpus.

    Réutilise ``enrich_corpus._valider_paire`` (longueurs, source citée,
    refus de tout dosage phytosanitaire chiffré).

    Args:
        paire: Dictionnaire ``instruction``/``input``/``output``.

    Returns:
        Couple (valide, motif). ``motif`` vide si la paire est valide.
    """
    problemes = enrich_corpus._valider_paire(0, paire)
    if problemes:
        return False, problemes[0].message
    return True, ""


def _charger_instructions_existantes(chemin: Path) -> set[str]:
    """Charge les instructions déjà présentes (reprise idempotente)."""
    vues: set[str] = set()
    if not chemin.exists():
        return vues
    with chemin.open(encoding="utf-8") as handle:
        for ligne in handle:
            ligne = ligne.strip()
            if not ligne:
                continue
            try:
                paire = json.loads(ligne)
            except json.JSONDecodeError:
                continue
            vues.add(normaliser_instruction(str(paire.get("instruction", ""))))
    return vues


def construire_corpus(
    chunks: list[Chunk],
    client: LLMClient,
    embedder: Embedder,
    sortie: Path,
    cible: int,
    paires_par_chunk: int = PAIRES_PAR_CHUNK,
    seuil_question: float = SEUIL_DEDUP_QUESTION,
    angles: tuple[tuple[str, str], ...] = ANGLES,
    concurrence: int = CONCURRENCE_DEFAUT,
) -> StatistiquesRun:
    """Génère, valide, déduplique et écrit les paires Q/R jusqu'à la cible.

    Pour chaque passage, interroge le LLM sous plusieurs **angles** (symptômes,
    action, prévention, calendrier…) afin d'extraire un maximum de questions
    réellement distinctes sans extrapoler hors-source. Les appels LLM sont
    émis en parallèle (``concurrence`` requêtes simultanées, agrégées par vLLM) ;
    la validation et la déduplication exacte + sémantique restent sérialisées
    dans le thread principal pour un état déterministe.

    Args:
        chunks: Passages sources diversifiés.
        client: Client LLM local de génération.
        embedder: Encodeur pour la déduplication sémantique des questions.
        sortie: Fichier JSONL de sortie (ajout incrémental, reprise possible).
        cible: Nombre total de paires visé dans le fichier de sortie.
        paires_par_chunk: Paires demandées par couple (passage, angle).
        seuil_question: Cosinus au-delà duquel deux questions sont doublons.
        angles: Angles de questionnement (clé, directive) à parcourir.
        concurrence: Nombre de requêtes LLM simultanées.

    Returns:
        Les statistiques du run.
    """
    stats = StatistiquesRun(chunks_total=len(chunks))
    sortie.parent.mkdir(parents=True, exist_ok=True)

    vues = _charger_instructions_existantes(sortie)
    deja = len(vues)
    embeddings_questions: list[np.ndarray] = []

    def _atteint() -> bool:
        return deja + stats.paires_ecrites >= cible

    def _enregistrer(handle, paire: dict[str, str]) -> None:
        """Valide, déduplique et écrit une paire candidate."""
        stats.paires_generees += 1
        valide, motif = paire_valide(paire)
        if not valide:
            stats.paires_rejetees += 1
            stats.rejets_par_motif[motif] = stats.rejets_par_motif.get(motif, 0) + 1
            return

        cle = normaliser_instruction(paire["instruction"])
        if not cle or cle in vues:
            stats.paires_doublons += 1
            return

        vecteur = embedder.encoder([paire["instruction"]])[0]
        if embeddings_questions:
            sims = np.stack(embeddings_questions) @ vecteur
            if float(sims.max()) >= seuil_question:
                stats.paires_doublons += 1
                return

        handle.write(json.dumps(paire, ensure_ascii=False) + "\n")
        handle.flush()
        vues.add(cle)
        embeddings_questions.append(vecteur)
        stats.paires_ecrites += 1

    taches = ((chunk, directive) for chunk in chunks for _ka, directive in angles)
    chunks_soumis: set[int] = set()

    with (
        sortie.open("a", encoding="utf-8") as handle,
        ThreadPoolExecutor(max_workers=concurrence) as executor,
    ):
        en_cours: dict[Future, Chunk] = {}

        def _soumettre() -> bool:
            """Soumet la prochaine tâche (chunk, angle). False si épuisé."""
            tache = next(taches, None)
            if tache is None:
                return False
            chunk, directive = tache
            chunks_soumis.add(id(chunk))
            system, user = construire_prompt(chunk, paires_par_chunk, directive)
            en_cours[executor.submit(client.generer, system, user)] = chunk
            return True

        for _ in range(concurrence):
            if not _soumettre():
                break

        while en_cours and not _atteint():
            termines, _ = wait(list(en_cours), return_when=FIRST_COMPLETED)
            for fut in termines:
                chunk = en_cours.pop(fut)
                try:
                    brut = fut.result()
                except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                    stats.erreurs_llm += 1
                    logger.warning(
                        "llm_erreur", doc=chunk.doc.id, page=chunk.page, erreur=str(exc)
                    )
                else:
                    for paire in parser_reponse_llm(brut):
                        _enregistrer(handle, paire)
                        if _atteint():
                            break
                if not _atteint():
                    _soumettre()

        for fut in en_cours:
            fut.cancel()

    stats.chunks_utilises = len(chunks_soumis)

    logger.info(
        "run_termine",
        cible=cible,
        deja_presentes=deja,
        ecrites=stats.paires_ecrites,
        total=deja + stats.paires_ecrites,
        generees=stats.paires_generees,
        rejetees=stats.paires_rejetees,
        doublons=stats.paires_doublons,
        erreurs_llm=stats.erreurs_llm,
        chunks_utilises=stats.chunks_utilises,
        rejets_par_motif=stats.rejets_par_motif,
    )
    return stats


def collecter_chunks(documents: list[SourceDoc], dossier_brut: Path) -> list[Chunk]:
    """Télécharge, extrait et découpe l'ensemble des documents.

    Args:
        documents: Documents du manifeste.
        dossier_brut: Cache des PDF.

    Returns:
        Tous les passages exploitables, tous documents confondus.
    """
    chunks: list[Chunk] = []
    for doc in documents:
        pdf = telecharger(doc, dossier_brut)
        if pdf is None:
            continue
        try:
            pages = extraire_pages(pdf)
        except Exception as exc:  # noqa: BLE001 - PDF parfois corrompu/scanné
            logger.error("extraction_echec", doc=doc.id, erreur=str(exc))
            continue
        morceaux = decouper_en_chunks(doc, pages)
        logger.info("document_traite", doc=doc.id, pages=len(pages), chunks=len(morceaux))
        chunks.extend(morceaux)
    return chunks


def _parser_args(argv: list[str] | None) -> argparse.Namespace:
    racine = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="Construction RAG du corpus OpenCacao.")
    parser.add_argument(
        "--manifeste",
        type=Path,
        default=racine / "corpus" / "sources" / "sources_officielles.yaml",
        help="Manifeste YAML des documents officiels.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=racine / "corpus" / "corpus_cacao_rag.jsonl",
        help="Fichier JSONL de sortie (ajout incrémental).",
    )
    parser.add_argument(
        "--sources-dir",
        type=Path,
        default=racine / "train",
        help="Dossier où sont rassemblés les documents officiels téléchargés.",
    )
    parser.add_argument("--target", type=int, default=10000, help="Nombre de paires visé.")
    parser.add_argument(
        "--par-chunk",
        type=int,
        default=PAIRES_PAR_CHUNK,
        help="Paires demandées par couple (passage, angle).",
    )
    parser.add_argument(
        "--concurrence",
        type=int,
        default=CONCURRENCE_DEFAUT,
        help="Requêtes LLM simultanées (clé du <1 h ; vLLM les agrège).",
    )
    parser.add_argument(
        "--angles",
        default="",
        help=(
            "Angles de questionnement à utiliser (clés séparées par des "
            f"virgules). Défaut : tous ({','.join(c for c, _ in ANGLES)})."
        ),
    )
    parser.add_argument(
        "--modele-embed",
        default=os.environ.get("CORPUS_EMBED_MODEL", MODELE_EMBED_DEFAUT),
        help="Modèle sentence-transformers pour l'index sémantique.",
    )
    parser.add_argument(
        "--llm-base-url",
        default=os.environ.get("CORPUS_LLM_BASE_URL", "http://localhost:8000"),
        help="URL du serveur LLM local OpenAI-compatible.",
    )
    parser.add_argument(
        "--llm-modele",
        default=os.environ.get("CORPUS_LLM_MODEL", "opencacao-7b"),
        help="Nom du modèle servi pour la génération.",
    )
    parser.add_argument(
        "--collect-only",
        action="store_true",
        help="Se limiter au téléchargement + découpage (aucun appel LLM).",
    )
    parser.add_argument("--log-level", default="INFO", help="Niveau de log.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée CLI du pipeline RAG. Retourne 0 si des paires sont écrites.

    Args:
        argv: Arguments (par défaut ``sys.argv``).

    Returns:
        Code de sortie processus.
    """
    args = _parser_args(argv)

    from logging import getLevelName

    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            getLevelName(args.log_level.upper())
        )
    )

    documents = charger_manifeste(args.manifeste)
    logger.info("manifeste_charge", documents=len(documents))

    # Préflight de connectivité AVANT tout travail coûteux (téléchargements,
    # chargement du modèle d'embeddings) : échec rapide et message clair.
    client: LLMClient | None = None
    if not args.collect_only:
        client = LocalLLMClient(
            base_url=args.llm_base_url,
            modele=args.llm_modele,
            api_key=os.environ.get("CORPUS_LLM_API_KEY"),
        )
        if not client.verifier():
            logger.error(
                "llm_injoignable",
                base_url=args.llm_base_url,
                message=(
                    "Le serveur LLM ne répond pas. Démarrer l'inférence "
                    "(ex. `make demo-base`) et vérifier --llm-base-url / "
                    "CORPUS_LLM_BASE_URL avant de relancer."
                ),
            )
            return 2

    chunks = collecter_chunks(documents, args.sources_dir)
    if not chunks:
        logger.error("aucun_chunk", message="Aucun passage exploitable (téléchargements ?).")
        return 1

    if args.collect_only:
        logger.info("collect_only_termine", chunks=len(chunks))
        return 0

    embedder = LocalEmbedder(args.modele_embed)
    chunks = deduppliquer_chunks(chunks, embedder)
    logger.info("chunks_diversifies", restants=len(chunks))

    if args.angles.strip():
        demandes = {a.strip() for a in args.angles.split(",") if a.strip()}
        angles = tuple((c, d) for c, d in ANGLES if c in demandes)
        if not angles:
            logger.error(
                "angles_inconnus",
                demandes=sorted(demandes),
                disponibles=[c for c, _ in ANGLES],
            )
            return 1
    else:
        angles = ANGLES
    logger.info("angles_actifs", angles=[c for c, _ in angles])

    assert client is not None  # garanti par le préflight ci-dessus
    stats = construire_corpus(
        chunks=chunks,
        client=client,
        embedder=embedder,
        sortie=args.out,
        cible=args.target,
        paires_par_chunk=args.par_chunk,
        angles=angles,
        concurrence=args.concurrence,
    )
    return 0 if stats.paires_ecrites > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
