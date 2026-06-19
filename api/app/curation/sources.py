"""Recherche des sources officielles : lecture du manifeste + téléchargement.

« Lancer la recherche » télécharge les documents publics listés dans le manifeste
``sources_officielles.yaml`` (snapshot embarqué ; source de vérité :
``corpus/sources/sources_officielles.yaml``) vers le store de documents, d'où ils
pourront être constitués en RAG.

Le téléchargement se fait **en flux** (vers la mémoire bornée puis disque) : peu
gourmand, contrairement à l'extraction de texte qui a lieu à la constitution.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
from pathlib import Path

import httpx
import yaml

from app.core.logging import get_logger

logger = get_logger(__name__)


def url_publique_sure(url: str) -> bool:
    """Vrai si l'URL pointe vers un hôte PUBLIC (anti-SSRF).

    Bloque les hôtes internes/privés (services du cluster, loopback, lien-local,
    métadonnées cloud 169.254.169.254…) pour qu'un ajout par URL ne puisse pas
    faire interroger des ressources internes au serveur.

    Args:
        url: URL fournie par l'utilisateur.

    Returns:
        True si toutes les IP résolues sont publiques et le schéma http/https.
    """
    try:
        u = httpx.URL(url)
    except (ValueError, TypeError):
        return False
    if u.scheme not in ("http", "https") or not u.host:
        return False
    hote = u.host
    # Noms internes au cluster Kubernetes (sans domaine public).
    if hote in {"localhost"} or hote.endswith((".local", ".svc", ".cluster.local", ".internal")):
        return False
    try:
        infos = socket.getaddrinfo(hote, None)
    except socket.gaierror:
        return False
    for info in infos:
        adresse = ipaddress.ip_address(info[4][0])
        if (
            adresse.is_private
            or adresse.is_loopback
            or adresse.is_link_local
            or adresse.is_reserved
            or adresse.is_multicast
            or adresse.is_unspecified
        ):
            return False
    return True


# Manifeste embarqué dans l'image (à côté de ce module).
SOURCES_PATH = Path(__file__).resolve().parent / "sources_officielles.yaml"
# Garde-fou : on n'ingère pas un fichier démesuré (manuels officiels = quelques Mo).
_TAILLE_MAX = 40 * 1024 * 1024
# Certains serveurs publics (FAO, gestionnaires de téléchargement) rejettent un
# client sans User-Agent « navigateur » (403). On se présente comme un navigateur.
NAVIGATEUR_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)


def charger_sources(chemin: Path = SOURCES_PATH) -> list[dict]:
    """Charge la liste des documents officiels depuis le manifeste YAML.

    Args:
        chemin: Chemin du manifeste.

    Returns:
        Liste de dicts ``{id, source, titre, url}`` (entrées valides uniquement).
    """
    if not chemin.exists():
        logger.warning("sources_manifeste_absent", chemin=str(chemin))
        return []
    data = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    documents: list[dict] = []
    for doc in data.get("documents", []):
        url = str(doc.get("url", "")).strip()
        ident = str(doc.get("id", "")).strip()
        if url and ident:
            documents.append(
                {
                    "id": ident,
                    "source": str(doc.get("source", "")),
                    "titre": str(doc.get("titre", ident)),
                    "url": url,
                    # Vérification TLS : False pour les serveurs à certificat cassé.
                    "verify": bool(doc.get("verify", True)),
                }
            )
    return documents


# Type de contenu HTTP -> extension de fichier.
_TYPE_EXT = {
    "application/pdf": ".pdf",
    "text/html": ".html",
    "application/xhtml+xml": ".html",
    "text/plain": ".txt",
    "text/markdown": ".md",
}


def extension_pour(url: str, content_type: str | None) -> str:
    """Déduit l'extension d'un document depuis le type de contenu, sinon l'URL.

    Args:
        url: URL téléchargée.
        content_type: En-tête ``Content-Type`` de la réponse, le cas échéant.

    Returns:
        Une extension de fichier (``.pdf``, ``.html``…) ou ``.bin`` si inconnue.
    """
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in _TYPE_EXT:
        return _TYPE_EXT[ct]
    suffixe = Path(httpx.URL(url).path).suffix.lower()
    return suffixe if suffixe in (".pdf", ".txt", ".md", ".html", ".htm") else ".bin"


def nom_depuis_url(url: str, content_type: str | None) -> str:
    """Déduit un nom de fichier UNIQUE et lisible depuis une URL (+ extension du type).

    Inclut les paramètres de requête (ex. ``?id=111``) pour distinguer les articles
    d'un même CMS, et ajoute un court hachage garantissant l'unicité même après
    troncature.

    Args:
        url: URL de la page/document.
        content_type: En-tête Content-Type de la réponse.

    Returns:
        Un nom de fichier (hôte + chemin + requête + extension), assaini par le store.
    """
    u = httpx.URL(url)
    query = u.query.decode("utf-8", "ignore") if u.query else ""
    base = (u.host or "page").replace(".", "-")
    brut = f"{base}-{u.path}-{query}".lower()
    slug = re.sub(r"[^a-z0-9]+", "-", brut).strip("-")[:80]
    # Hachage de l'URL complète : unicité même si des articles ont le même slug tronqué.
    empreinte = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8] if query else ""
    nom = f"{slug}-{empreinte}" if empreinte else slug
    return f"{nom}{extension_pour(url, content_type)}"


async def telecharger(client: httpx.AsyncClient, url: str) -> tuple[bytes, str | None] | None:
    """Télécharge un document en flux. Retourne (octets, content-type), ou None si échec.

    Args:
        client: Client HTTP asynchrone.
        url: URL du document.

    Returns:
        ``(contenu, content_type)``, ou ``None`` si injoignable ou trop volumineux.
    """
    try:
        async with client.stream("GET", url) as reponse:
            reponse.raise_for_status()
            content_type = reponse.headers.get("content-type")
            morceaux: list[bytes] = []
            total = 0
            async for bloc in reponse.aiter_bytes():
                total += len(bloc)
                if total > _TAILLE_MAX:
                    logger.warning("source_trop_volumineuse", url=url)
                    return None
                morceaux.append(bloc)
            return b"".join(morceaux), content_type
    except httpx.HTTPError as exc:
        logger.warning("source_telechargement_echec", url=url, error=str(exc))
        return None
