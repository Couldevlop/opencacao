"""Découverte automatique de nouvelles sources officielles.

Explore une **liste blanche** de sites officiels (pages d'accueil / publications),
en extrait les liens vers des **PDF** hébergés sur ces mêmes domaines, et propose
ceux qui ne sont pas déjà connus. Bornée et sûre :

* seuls les **domaines officiels autorisés** sont suivis (jamais le web ouvert) ;
* uniquement des **liens PDF** (documents) ;
* garde-fou **anti-SSRF** (``url_publique_sure``) sur chaque candidat ;
* **plafond** du nombre de découvertes par exécution.

Le téléchargement effectif est fait par le pipeline (réutilise ``telecharger``).
"""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from app.core.logging import get_logger
from app.curation.documents import DocumentStore
from app.curation.sources import nom_depuis_url, telecharger, url_publique_sure

logger = get_logger(__name__)

# Domaines officiels de la filière dont on suit les liens (liste blanche stricte).
DOMAINES_AUTORISES = (
    "conseilcafecacao.ci",
    "cnra.ci",
    "anader.ci",
    "firca.ci",
    "fao.org",
    "icco.org",
    "observaterra.ci",
)

# Pages de départ explorées (accueil + pages de publications connues).
SEEDS = (
    "http://www.conseilcafecacao.ci/",
    "https://cnra.ci/",
    "https://www.anader.ci/",
    "https://firca.ci/ressources/publications/guides-et-manuels/",
    "https://www.icco.org/",
)

_MAX_DECOUVERTES = 25


def _domaine_autorise(url: str) -> bool:
    """Vrai si l'hôte de l'URL appartient à un domaine officiel autorisé."""
    hote = (urlsplit(url).hostname or "").lower()
    return any(hote == d or hote.endswith("." + d) for d in DOMAINES_AUTORISES)


def est_pdf(url: str) -> bool:
    """Vrai si l'URL pointe vers un PDF (extension, en ignorant la requête)."""
    return urlsplit(url).path.lower().endswith(".pdf")


class _ExtracteurLiens(HTMLParser):
    """Collecte les href des balises <a>."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.liens: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "a":
            for nom, valeur in attrs:
                if nom == "href" and valeur:
                    self.liens.append(valeur)


def extraire_liens_pdf(html: str, base_url: str) -> set[str]:
    """Extrait les liens PDF (absolus, domaines autorisés) d'une page HTML.

    Args:
        html: Contenu HTML de la page.
        base_url: URL de la page (pour résoudre les liens relatifs).

    Returns:
        L'ensemble des URLs PDF sur des domaines autorisés.
    """
    parseur = _ExtracteurLiens()
    parseur.feed(html)
    urls: set[str] = set()
    for href in parseur.liens:
        absolu = urljoin(base_url, href.strip())
        if est_pdf(absolu) and _domaine_autorise(absolu):
            urls.add(absolu)
    return urls


async def decouvrir(
    client: httpx.AsyncClient, store: DocumentStore, max_docs: int = _MAX_DECOUVERTES
) -> list[dict]:
    """Explore les pages de départ et retourne les nouveaux PDF candidats.

    Args:
        client: Client HTTP.
        store: Store des documents (pour écarter ce qui est déjà connu/archivé).
        max_docs: Nombre maximum de candidats retournés.

    Returns:
        Liste de ``{"url", "nom"}`` à télécharger (PDF officiels non encore connus).
    """
    candidats: list[dict] = []
    vus: set[str] = set()
    for seed in SEEDS:
        resultat = await telecharger(client, seed)
        if not resultat:
            continue
        contenu, _ = resultat
        liens = extraire_liens_pdf(contenu.decode("utf-8", "ignore"), seed)
        for url in liens:
            if url in vus:
                continue
            vus.add(url)
            if not url_publique_sure(url):
                continue
            nom = nom_depuis_url(url, "application/pdf")
            stem = nom.rsplit(".", 1)[0]
            if store.existe_prefixe(stem):
                continue  # déjà téléchargé (actif ou archivé)
            candidats.append({"url": url, "nom": nom})
            if len(candidats) >= max_docs:
                logger.info("decouverte_plafond", max=max_docs)
                return candidats
    logger.info("decouverte_terminee", candidats=len(candidats))
    return candidats
