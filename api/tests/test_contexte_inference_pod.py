"""Le contexte servi par llama.cpp sur le pod GPU — verrouillé par les scripts.

Incident du 11/09/2026 : `-c 8192 -np 4` semblait donner 8192 tokens. llama.cpp DIVISE
le contexte entre les emplacements parallèles : chaque conversation n'en recevait que
2048. Trois tours de dialogue avec un extrait RAG suffisaient à dépasser, le serveur
rendait 400 « exceeds the available context size », et l'interface affichait « service
momentanément indisponible ». Les tests de fumée passaient : leurs questions étaient
trop courtes pour révéler le défaut.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[2]
SCRIPTS = (
    RACINE / "training" / "scripts" / "pod_serve.sh",
    RACINE / "deploy" / "scripts" / "pod" / "pre_start.sh",
)

# Ce dont une CONVERSATION a besoin : le prompt système, la mémoire de la fiche, les
# extraits RAG et cinq tours de dialogue. En dessous, le multi-tours casse.
CONTEXTE_MIN_PAR_CONVERSATION = 8192


def _valeur(source: str, nom: str) -> int:
    trouve = re.search(rf'{nom}="\$\{{{nom}:-(\d+)\}}"', source)
    assert trouve is not None, f"{nom} introuvable"
    return int(trouve.group(1))


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_chaque_conversation_dispose_du_contexte_annonce(script: Path) -> None:
    """`-c` est un TOTAL réparti entre les slots : c'est le quotient qui compte."""
    source = script.read_text(encoding="utf-8")
    contexte = _valeur(source, "CONTEXTE")
    slots = _valeur(source, "SLOTS")
    assert contexte // slots >= CONTEXTE_MIN_PAR_CONVERSATION, (
        f"{script.name} : {contexte} / {slots} slots = {contexte // slots} tokens par "
        f"conversation, il en faut {CONTEXTE_MIN_PAR_CONVERSATION}"
    )


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_le_partage_du_contexte_est_documente(script: Path) -> None:
    """Sans l'explication, le prochain réglage « d'économie » refera l'incident."""
    source = script.read_text(encoding="utf-8")
    assert "DIVISE le contexte" in source
