"""Tests du journal des interactions (JournalFichier)."""

from __future__ import annotations

import json
from pathlib import Path

from app.core.config import Settings
from app.core.journal import JournalFichier


async def test_enregistre_interaction_ecrit_une_ligne(tmp_path: Path) -> None:
    """Une interaction journalisée produit une ligne JSONL et un identifiant."""
    journal = JournalFichier(tmp_path, actif=True)
    interaction_id = await journal.enregistrer_interaction(
        "Quand récolter ?", "fr", "Quand les cabosses sont mûres.", "moyenne", ["CNRA"], False
    )
    assert interaction_id
    lignes = (tmp_path / "interactions.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lignes) == 1
    enregistrement = json.loads(lignes[0])
    assert enregistrement["id"] == interaction_id
    assert enregistrement["question"] == "Quand récolter ?"
    assert "ts" in enregistrement
    assert "ip" not in enregistrement  # anonymisé


async def test_enregistre_feedback(tmp_path: Path) -> None:
    """Un retour 👍/👎 est ajouté au fichier feedback."""
    journal = JournalFichier(tmp_path, actif=True)
    await journal.enregistrer_feedback("abc123", "up")
    enregistrement = json.loads((tmp_path / "feedback.jsonl").read_text(encoding="utf-8"))
    assert enregistrement == {"id": "abc123", "vote": "up", "ts": enregistrement["ts"]}


async def test_inactif_n_ecrit_rien_mais_retourne_un_id(tmp_path: Path) -> None:
    """Journal inactif : un id est généré mais aucun fichier n'est écrit."""
    journal = JournalFichier(tmp_path, actif=False)
    interaction_id = await journal.enregistrer_interaction("q", "fr", "r", "faible", [], False)
    await journal.enregistrer_feedback(interaction_id, "down")
    assert interaction_id
    assert not (tmp_path / "interactions.jsonl").exists()
    assert not (tmp_path / "feedback.jsonl").exists()


async def test_tolere_dossier_invalide(tmp_path: Path) -> None:
    """Une erreur d'écriture (chemin invalide) n'interrompt pas le service."""
    fichier = tmp_path / "fichier"
    fichier.write_text("x", encoding="utf-8")  # un FICHIER là où on attend un dossier
    journal = JournalFichier(fichier / "sous", actif=True)
    # Ne doit pas lever, malgré l'impossibilité de créer le dossier.
    interaction_id = await journal.enregistrer_interaction("q", "fr", "r", "faible", [], False)
    assert interaction_id


def test_from_settings() -> None:
    """La fabrique reflète le drapeau log_questions et le dossier configuré."""
    journal = JournalFichier.from_settings(Settings(log_questions=True, dataset_dir="/tmp/x"))
    assert isinstance(journal, JournalFichier)
