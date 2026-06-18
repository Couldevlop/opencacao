"""Recherche des sources officielles : lecture du manifeste + téléchargement.

« Lancer la recherche » télécharge les documents publics listés dans le manifeste
``sources_officielles.yaml`` (snapshot embarqué ; source de vérité :
``corpus/sources/sources_officielles.yaml``) vers le store de documents, d'où ils
pourront être constitués en RAG.

Le téléchargement se fait **en flux** (vers la mémoire bornée puis disque) : peu
gourmand, contrairement à l'extraction de texte qui a lieu à la constitution.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import yaml

from app.core.logging import get_logger

logger = get_logger(__name__)

# Manifeste embarqué dans l'image (à côté de ce module).
SOURCES_PATH = Path(__file__).resolve().parent / "sources_officielles.yaml"
# Garde-fou : on n'ingère pas un fichier démesuré (manuels officiels = quelques Mo).
_TAILLE_MAX = 40 * 1024 * 1024


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
                }
            )
    return documents


def nom_fichier(doc: dict) -> str:
    """Déduit un nom de fichier pour un document (``<id>`` + extension de l'URL)."""
    suffixe = Path(httpx.URL(doc["url"]).path).suffix.lower()
    if suffixe not in (".pdf", ".txt", ".md"):
        suffixe = ".pdf"
    return f"{doc['id']}{suffixe}"


async def telecharger(client: httpx.AsyncClient, url: str) -> bytes | None:
    """Télécharge un document en flux. Retourne les octets, ou None en cas d'échec.

    Args:
        client: Client HTTP asynchrone.
        url: URL du document.

    Returns:
        Le contenu binaire, ou ``None`` si l'URL est injoignable ou trop volumineuse.
    """
    try:
        async with client.stream("GET", url) as reponse:
            reponse.raise_for_status()
            morceaux: list[bytes] = []
            total = 0
            async for bloc in reponse.aiter_bytes():
                total += len(bloc)
                if total > _TAILLE_MAX:
                    logger.warning("source_trop_volumineuse", url=url)
                    return None
                morceaux.append(bloc)
            return b"".join(morceaux)
    except httpx.HTTPError as exc:
        logger.warning("source_telechargement_echec", url=url, error=str(exc))
        return None
