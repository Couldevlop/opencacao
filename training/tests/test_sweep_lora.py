"""Tests de la recette LoRA pilotée par l'éval (sweep_lora, F4).

Couvre la logique pure (faisable hors GPU) : construction de la grille,
identifiant déterministe, portail garde-fous (non négociable, CLAUDE §13),
classement qualité/latence, sélection du meilleur et chargement des rapports.

Conformément au garde-fou métier, aucun dosage chiffré n'apparaît ici : les
synthèses de test ne portent que des indicateurs agrégés.
"""

from __future__ import annotations

import json
from pathlib import Path

from sweep_lora import (
    Combo,
    charger_rapports,
    classer,
    combo_depuis_id,
    eligible,
    formater_tableau,
    grille,
    id_combo,
    score_qualite,
    selectionner_meilleur,
)


def _synthese(
    *,
    garde: float = 1.0,
    fuites: int = 0,
    qualite: float = 0.8,
    juge: float | None = None,
    lat_p95: float = 0.0,
) -> dict:
    """Construit une synthèse d'éval minimale pour les tests."""
    return {
        "garde_fou_taux": garde,
        "fuites_dosage": fuites,
        "qualite_taux": qualite,
        "juge_moyen": juge,
        "latence_p95_s": lat_p95,
    }


# --- Identifiant & grille ---


def test_id_combo_deterministe_et_sur() -> None:
    """L'identifiant est stable et ne contient pas de caractère de chemin risqué."""
    combo = Combo(
        epochs=2, lora_r=32, lora_alpha=64, learning_rate=2e-4, max_seq_len=1024
    )
    assert id_combo(combo) == "e2-r32-a64-lr2e-04-s1024"
    assert "/" not in id_combo(combo) and " " not in id_combo(combo)


def test_combo_depuis_id_est_inverse() -> None:
    """``combo_depuis_id`` reconstruit fidèlement la combinaison."""
    combo = Combo(
        epochs=3, lora_r=64, lora_alpha=128, learning_rate=1e-4, max_seq_len=1536
    )
    assert combo_depuis_id(id_combo(combo)) == combo


def test_grille_cartesienne_et_alpha() -> None:
    """La grille couvre le produit cartésien avec alpha = 2·rang."""
    combos = grille([1, 2], [16, 32], [2e-4], [1024])
    assert len(combos) == 4
    assert all(c.lora_alpha == 2 * c.lora_r for c in combos)
    # Tous distincts.
    assert len({id_combo(c) for c in combos}) == 4


def test_grille_deterministe_et_dedupliquee() -> None:
    """Deux appels donnent le même ordre ; les rangs en double sont écartés."""
    a = grille([1, 2, 3], [16, 32, 64], [2e-4, 1e-4], [1024, 1536])
    b = grille([1, 2, 3], [16, 32, 64], [2e-4, 1e-4], [1024, 1536])
    assert [id_combo(c) for c in a] == [id_combo(c) for c in b]
    assert len(a) == 3 * 3 * 2 * 2
    # Un rang répété ne crée pas de doublon.
    assert len(grille([1], [16, 16], [2e-4], [1024])) == 1


# --- Portail garde-fous (non négociable) ---


def test_eligible_exige_100pc_et_zero_dosage() -> None:
    """Le portail refuse toute fuite de dosage et tout garde-fou manqué."""
    assert eligible(_synthese(garde=1.0, fuites=0)) is True
    assert eligible(_synthese(garde=1.0, fuites=1)) is False  # fuite de dosage
    assert eligible(_synthese(garde=0.99, fuites=0)) is False  # garde-fou manqué


def test_combo_la_plus_qualitative_mais_non_sure_est_exclue() -> None:
    """Une combinaison excellente en qualité mais non sûre n'est jamais retenue."""
    rapports = [
        {"id": "sure", "synthese": _synthese(qualite=0.70, juge=0.70)},
        {"id": "dangereuse", "synthese": _synthese(qualite=0.99, juge=0.99, fuites=1)},
    ]
    meilleur = selectionner_meilleur(rapports)
    assert meilleur is not None and meilleur["id"] == "sure"


def test_aucune_combinaison_sure_renvoie_none() -> None:
    """Si rien ne passe le portail, aucun vainqueur n'est désigné."""
    rapports = [{"id": "x", "synthese": _synthese(fuites=1)}]
    assert selectionner_meilleur(rapports) is None


# --- Classement qualité / latence ---


def test_score_qualite_prefere_le_juge() -> None:
    """Le score privilégie la note du juge quand elle est disponible et valide."""
    assert score_qualite(_synthese(qualite=0.5, juge=0.9)) == 0.9
    # Juge absent : on retombe sur le taux de qualité.
    assert score_qualite(_synthese(qualite=0.5, juge=None)) == 0.5
    # Juge indisponible (-1) : ignoré, taux de qualité utilisé.
    assert score_qualite(_synthese(qualite=0.5, juge=-1.0)) == 0.5


def test_classement_par_qualite_puis_latence() -> None:
    """À qualité égale, la latence p95 la plus basse l'emporte (F4 = qualité × latence)."""
    rapports = [
        {"id": "lent", "synthese": _synthese(juge=0.8, lat_p95=60.0)},
        {"id": "rapide", "synthese": _synthese(juge=0.8, lat_p95=40.0)},
        {"id": "meilleur", "synthese": _synthese(juge=0.9, lat_p95=70.0)},
    ]
    classement = [r["id"] for r in classer(rapports)]
    assert classement == ["meilleur", "rapide", "lent"]
    assert selectionner_meilleur(rapports)["id"] == "meilleur"


def test_classement_exclut_les_non_sures() -> None:
    """Le classement ne contient que des combinaisons éligibles."""
    rapports = [
        {"id": "ok", "synthese": _synthese(juge=0.7)},
        {"id": "ko", "synthese": _synthese(juge=0.99, garde=0.5)},
    ]
    assert [r["id"] for r in classer(rapports)] == ["ok"]


# --- Chargement des rapports & rendu ---


def test_charger_rapports_ignore_invalides_et_propre_sortie(tmp_path: Path) -> None:
    """Lit les ``<id>.json`` valides, ignore le bruit et son propre rapport de sweep."""
    (tmp_path / "e1-r16-a32-lr2e-04-s1024.json").write_text(
        json.dumps({"synthese": _synthese(), "cas": []}), encoding="utf-8"
    )
    (tmp_path / "casse.json").write_text("{pas du json", encoding="utf-8")
    (tmp_path / "sans_synthese.json").write_text(
        json.dumps({"cas": []}), encoding="utf-8"
    )
    (tmp_path / "rapport_sweep.json").write_text(
        json.dumps({"meilleur": "x"}), encoding="utf-8"
    )

    rapports = charger_rapports(tmp_path)
    assert [r["id"] for r in rapports] == ["e1-r16-a32-lr2e-04-s1024"]


def test_formater_tableau_marque_le_vainqueur() -> None:
    """Le tableau marque le vainqueur et signale les combinaisons non sûres."""
    rapports = [
        {"id": "gagnant", "synthese": _synthese(juge=0.9)},
        {"id": "exclu", "synthese": _synthese(fuites=1)},
    ]
    tableau = formater_tableau(rapports, "gagnant")
    assert "* gagnant" in tableau
    assert "NON" in tableau  # la combinaison non sûre est marquée
    assert "gagnant" in tableau


def test_formater_tableau_aucun_vainqueur() -> None:
    """Sans vainqueur, le tableau l'annonce explicitement (rien à déployer)."""
    rapports = [{"id": "x", "synthese": _synthese(fuites=1)}]
    tableau = formater_tableau(rapports, None)
    assert "Aucune combinaison" in tableau
