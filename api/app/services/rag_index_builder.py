"""Construction et fusion de l'index RAG (logique partagée).

Ce module rassemble la logique *pure* de l'index RAG — lecture des paires,
détection de source, fusion additive, écriture atomique — afin qu'elle soit
réutilisée à la fois par le script hors-ligne ``training/scripts/build_rag_index``
et par la **console de curation** (reconstruction à chaud). Le transport des
embeddings (urllib côté script, httpx côté console) reste à l'appelant : ce
module ne fait aucun appel réseau.

La fusion est **additive** : on conserve l'index existant et on n'ajoute que les
paires dont la réponse n'y figure pas encore. Reconstruire depuis la console ne
peut donc jamais *réduire* l'index de production.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

# Sources reconnues, citées dans les réponses du corpus.
SOURCES: tuple[str, ...] = ("CNRA", "ANADER", "Conseil du Café-Cacao", "FAO")

# Arrondi des vecteurs : index ~2× plus léger, sans impact sur le cosinus.
_DECIMALES = 6


def detecter_source(output: str) -> str:
    """Retourne la première source reconnue citée dans le texte, sinon "".

    Args:
        output: Réponse dont on cherche la source.

    Returns:
        Le nom de la source reconnue, ou une chaîne vide.
    """
    for source in SOURCES:
        if source.lower() in output.lower():
            return source
    return ""


def charger_paires(sources: list[Path]) -> list[tuple[str, str]]:
    """Lit les paires (instruction, output) non vides des fichiers corpus.

    Les fichiers absents sont ignorés ; les lignes vides ou non JSON sont sautées.

    Args:
        sources: Fichiers JSONL ``{"instruction", "output", ...}``.

    Returns:
        Liste de couples ``(instruction, output)`` non vides.
    """
    paires: list[tuple[str, str]] = []
    for source in sources:
        if not source.exists():
            continue
        for ligne in source.read_text(encoding="utf-8").splitlines():
            ligne = ligne.strip()
            if not ligne:
                continue
            try:
                paire = json.loads(ligne)
            except json.JSONDecodeError:
                continue
            instruction = str(paire.get("instruction", "")).strip()
            output = str(paire.get("output", "")).strip()
            if instruction and output:
                paires.append((instruction, output))
    return paires


def lire_index(chemin: Path) -> list[dict]:
    """Charge les entrées d'un index JSONL existant (vide si absent).

    Args:
        chemin: Fichier d'index ``{"texte", "source", "vecteur"}``.

    Returns:
        Liste des entrées valides (texte + vecteur présents).
    """
    if not chemin.exists():
        return []
    entrees: list[dict] = []
    for ligne in chemin.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne:
            continue
        try:
            enr = json.loads(ligne)
        except json.JSONDecodeError:
            continue
        if "texte" in enr and "vecteur" in enr:
            entrees.append(
                {
                    "texte": str(enr["texte"]),
                    "source": str(enr.get("source", "")),
                    "vecteur": list(enr["vecteur"]),
                }
            )
    return entrees


def paires_nouvelles(existant: list[dict], paires: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Filtre les paires dont la réponse n'est pas déjà indexée.

    La déduplication se fait sur le **texte de réponse** (seul champ stocké dans
    l'index). Garantit qu'un reindex additif n'ajoute jamais de doublon.

    Args:
        existant: Entrées déjà présentes dans l'index.
        paires: Couples ``(instruction, output)`` candidats.

    Returns:
        Les couples à indexer (réponse absente de l'index et non dupliquée).
    """
    connus = {entree["texte"].strip() for entree in existant}
    nouvelles: list[tuple[str, str]] = []
    for instruction, output in paires:
        if output.strip() in connus:
            continue
        connus.add(output.strip())
        nouvelles.append((instruction, output))
    return nouvelles


def construire_entrees(paires: list[tuple[str, str]], vecteurs: list[list[float]]) -> list[dict]:
    """Assemble les entrées d'index à partir des paires et de leurs vecteurs.

    Le vecteur correspond à l'**instruction** (clé de recherche) ; le texte stocké
    est la **réponse** (output).

    Args:
        paires: Couples ``(instruction, output)``, alignés avec ``vecteurs``.
        vecteurs: Embeddings des instructions, même ordre que ``paires``.

    Returns:
        Liste d'entrées ``{"texte", "source", "vecteur"}``.

    Raises:
        ValueError: Si ``paires`` et ``vecteurs`` n'ont pas la même longueur.
    """
    if len(paires) != len(vecteurs):
        raise ValueError("paires et vecteurs doivent avoir la même longueur")
    entrees: list[dict] = []
    for (_, output), vecteur in zip(paires, vecteurs, strict=True):
        entrees.append(
            {
                "texte": output,
                "source": detecter_source(output),
                "vecteur": [round(float(x), _DECIMALES) for x in vecteur],
            }
        )
    return entrees


def ecrire_index(chemin: Path, entrees: list[dict]) -> None:
    """Écrit l'index en JSONL de façon atomique (écriture temporaire + renommage).

    Args:
        chemin: Fichier d'index à écrire.
        entrees: Entrées ``{"texte", "source", "vecteur"}`` à sérialiser.
    """
    chemin.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(chemin.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for entree in entrees:
                handle.write(json.dumps(entree, ensure_ascii=False) + "\n")
        os.replace(tmp, chemin)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
