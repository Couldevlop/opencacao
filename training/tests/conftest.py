"""Configuration pytest pour les scripts d'entraînement.

Les scripts de ``training/scripts`` ne forment pas un paquet installable ; on
ajoute leur dossier au chemin d'import pour pouvoir les tester directement.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
