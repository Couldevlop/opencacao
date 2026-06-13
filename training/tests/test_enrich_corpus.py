"""Tests du validateur de corpus (enrich_corpus).

Conformément au garde-fou métier (CLAUDE §13), aucun dosage phytosanitaire
chiffré n'est écrit dans ces tests, même à titre d'exemple. La branche de
détection du filtre est donc vérifiée indirectement : on s'assure qu'un texte
agronomique légitime (sans dosage) n'est pas faussement rejeté.
"""

from __future__ import annotations

import json
from pathlib import Path

import enrich_corpus
from enrich_corpus import valider_corpus

_SOURCE = " Sources : CNRA, ANADER."
_OUTPUT_OK = (
    "Pour limiter la pourriture brune, retirez les cabosses malades et aérez "
    "la plantation. Surveillez régulièrement vos arbres." + _SOURCE
)


def _ecrire(tmp_path: Path, lignes: list[dict[str, str]]) -> Path:
    chemin = tmp_path / "corpus.jsonl"
    chemin.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in lignes),
        encoding="utf-8",
    )
    return chemin


def test_paire_valide_ne_produit_aucun_probleme(tmp_path: Path) -> None:
    chemin = _ecrire(
        tmp_path,
        [{"instruction": "Comment limiter la pourriture brune ?", "input": "", "output": _OUTPUT_OK}],
    )
    assert valider_corpus(chemin) == []


def test_champ_obligatoire_manquant(tmp_path: Path) -> None:
    chemin = _ecrire(tmp_path, [{"instruction": "Question valable ici ?", "output": _OUTPUT_OK}])
    problemes = valider_corpus(chemin)
    assert any("input" in p.message for p in problemes)


def test_instruction_trop_courte(tmp_path: Path) -> None:
    chemin = _ecrire(tmp_path, [{"instruction": "court", "input": "", "output": _OUTPUT_OK}])
    problemes = valider_corpus(chemin)
    assert any("instruction" in p.message for p in problemes)


def test_output_trop_court(tmp_path: Path) -> None:
    chemin = _ecrire(
        tmp_path,
        [{"instruction": "Une question bien assez longue ?", "input": "", "output": "Trop court."}],
    )
    problemes = valider_corpus(chemin)
    assert any("output" in p.message for p in problemes)


def test_source_absente_signale(tmp_path: Path) -> None:
    chemin = _ecrire(
        tmp_path,
        [
            {
                "instruction": "Comment aérer ma cacaoyère ?",
                "input": "",
                "output": "Taillez les branches basses pour laisser circuler l'air entre les arbres librement.",
            }
        ],
    )
    problemes = valider_corpus(chemin)
    assert any("source" in p.message for p in problemes)


def test_json_invalide_signale(tmp_path: Path) -> None:
    chemin = tmp_path / "corpus.jsonl"
    chemin.write_text('{"instruction": "x", ', encoding="utf-8")
    problemes = valider_corpus(chemin)
    assert any("JSON" in p.message for p in problemes)


def test_lignes_vides_ignorees(tmp_path: Path) -> None:
    chemin = tmp_path / "corpus.jsonl"
    contenu = json.dumps(
        {"instruction": "Comment limiter la pourriture brune ?", "input": "", "output": _OUTPUT_OK},
        ensure_ascii=False,
    )
    chemin.write_text(f"\n{contenu}\n\n", encoding="utf-8")
    assert valider_corpus(chemin) == []


def test_texte_agronomique_legitime_non_rejete(tmp_path: Path) -> None:
    """Une réponse mentionnant un fongicide sans dosage chiffré reste valide.

    Garantit que le filtre anti-dosage ne produit pas de faux positif sur les
    bonnes pratiques générales (qui n'indiquent aucune quantité).
    """
    output = (
        "En cas de forte pression, un fongicide homologué peut être envisagé, "
        "mais demandez toujours conseil à votre agent ANADER pour le choix et "
        "l'application." + _SOURCE
    )
    chemin = _ecrire(
        tmp_path,
        [{"instruction": "Que faire contre la pourriture brune ?", "input": "", "output": output}],
    )
    assert valider_corpus(chemin) == []


def test_corpus_de_demarrage_reel_est_valide() -> None:
    """Le corpus livré dans le dépôt passe la validation."""
    chemin = Path(enrich_corpus.__file__).resolve().parents[2] / "corpus" / "corpus_cacao_demarrage.jsonl"
    assert valider_corpus(chemin) == []
