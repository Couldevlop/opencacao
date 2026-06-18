"""Gestion des documents sources de la console (upload, extraction, découpage).

Les documents téléversés (PDF / TXT / Markdown) sont stockés sur le volume
partagé (``/data/documents``). À l'étape « Constitution », leur texte est extrait
et découpé en extraits, vectorisé via le service d'embeddings, puis ajouté à
l'index RAG (cf. :mod:`app.curation.pipeline`).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger(__name__)

# Formats acceptés à l'upload.
EXTENSIONS_OK = (".pdf", ".txt", ".md", ".csv")
# Découpage des extraits : taille cible et chevauchement (caractères).
_TAILLE_EXTRAIT = 700
_CHEVAUCHEMENT = 100
_NOM_AUTORISE = re.compile(r"[^A-Za-z0-9._-]+")


class DocumentInvalide(Exception):
    """Levée si un document est refusé (format ou nom invalide)."""


def nom_sur(nom: str) -> str:
    """Retourne un nom de fichier sûr (anti-traversée de chemin).

    Args:
        nom: Nom proposé par le client.

    Returns:
        Un nom de base nettoyé (sans séparateur de chemin).

    Raises:
        DocumentInvalide: Si le nom est vide, ou l'extension non autorisée.
    """
    base = Path(nom.strip()).name  # neutralise tout chemin (../, /, etc.)
    base = _NOM_AUTORISE.sub("_", base).strip("._")
    if not base:
        raise DocumentInvalide("nom de fichier invalide")
    if not base.lower().endswith(EXTENSIONS_OK):
        raise DocumentInvalide(f"format non supporté (acceptés : {', '.join(EXTENSIONS_OK)})")
    return base


def decouper(
    texte: str, taille: int = _TAILLE_EXTRAIT, chevauchement: int = _CHEVAUCHEMENT
) -> list[str]:
    """Découpe un texte en extraits qui se chevauchent légèrement.

    Découpe sur les frontières de paragraphe quand c'est possible, sinon par
    fenêtres de caractères. Les extraits très courts sont écartés.

    Args:
        texte: Texte source.
        taille: Taille cible d'un extrait (caractères).
        chevauchement: Recouvrement entre extraits successifs.

    Returns:
        La liste des extraits non vides.
    """
    texte = re.sub(r"[ \t]+", " ", texte).strip()
    if not texte:
        return []
    extraits: list[str] = []
    debut = 0
    n = len(texte)
    while debut < n:
        fin = min(debut + taille, n)
        # Essaie de couper proprement à une fin de phrase/paragraphe.
        if fin < n:
            coupe = max(texte.rfind("\n", debut, fin), texte.rfind(". ", debut, fin))
            if coupe > debut + taille // 2:
                fin = coupe + 1
        extrait = texte[debut:fin].strip()
        if len(extrait) >= 50:
            extraits.append(extrait)
        if fin >= n:
            break
        debut = max(fin - chevauchement, debut + 1)
    return extraits


def _extraire_pdf(chemin: Path) -> str:
    """Extrait le texte d'un PDF (pypdf)."""
    from pypdf import PdfReader

    lecteur = PdfReader(str(chemin))
    return "\n".join((page.extract_text() or "") for page in lecteur.pages)


def extraire_texte(chemin: Path) -> str:
    """Extrait le texte brut d'un document (PDF via pypdf, sinon lecture UTF-8)."""
    if chemin.suffix.lower() == ".pdf":
        return _extraire_pdf(chemin)
    return chemin.read_text(encoding="utf-8", errors="ignore")


class DocumentStore:
    """Stockage des documents sources sur le volume partagé."""

    def __init__(self, dossier: Path) -> None:
        """Initialise le store.

        Args:
            dossier: Répertoire de stockage des documents.
        """
        self._dossier = dossier

    @classmethod
    def from_env(cls) -> DocumentStore:
        """Construit le store depuis l'environnement (``DATASET_DIR``)."""
        base = Path(os.environ.get("DATASET_DIR", "/data"))
        return cls(base / "documents")

    def enregistrer(self, nom: str, donnees: bytes) -> dict:
        """Enregistre un document (octets bruts) après validation du nom.

        Args:
            nom: Nom proposé.
            donnees: Contenu binaire du fichier.

        Returns:
            Les métadonnées du document enregistré (``nom``, ``taille``).

        Raises:
            DocumentInvalide: Si le nom/format est invalide ou le contenu vide.
        """
        sur = nom_sur(nom)
        if not donnees:
            raise DocumentInvalide("document vide")
        self._dossier.mkdir(parents=True, exist_ok=True)
        (self._dossier / sur).write_bytes(donnees)
        logger.info("document_enregistre", nom=sur, taille=len(donnees))
        return {"nom": sur, "taille": len(donnees)}

    def lister(self) -> list[dict]:
        """Liste les documents stockés (nom, taille), triés par nom."""
        if not self._dossier.is_dir():
            return []
        items = [
            {"nom": p.name, "taille": p.stat().st_size}
            for p in self._dossier.iterdir()
            if p.is_file()
        ]
        return sorted(items, key=lambda d: d["nom"])

    def existe(self, nom: str) -> bool:
        """Indique si un document du nom donné est déjà stocké."""
        try:
            return (self._dossier / nom_sur(nom)).is_file()
        except DocumentInvalide:
            return False

    def supprimer(self, nom: str) -> bool:
        """Supprime un document. Retourne True s'il existait."""
        cible = self._dossier / nom_sur(nom)
        if cible.is_file():
            cible.unlink()
            logger.info("document_supprime", nom=cible.name)
            return True
        return False

    def extraits(self) -> list[tuple[str, str]]:
        """Extrait et découpe tous les documents en (source, extrait).

        Returns:
            La liste des couples ``(nom_document, extrait)``.
        """
        resultats: list[tuple[str, str]] = []
        if not self._dossier.is_dir():
            return resultats
        for chemin in sorted(self._dossier.iterdir()):
            if not chemin.is_file():
                continue
            try:
                texte = extraire_texte(chemin)
            except Exception as exc:  # noqa: BLE001 - un PDF illisible ne bloque pas le reste
                logger.warning("extraction_echec", nom=chemin.name, error=str(exc))
                continue
            for extrait in decouper(texte):
                resultats.append((chemin.name, extrait))
        return resultats
