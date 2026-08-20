"""Le script de reprise GPU — ce qu'il refuse, verrouillé par le fichier.

Un script d'exploitation ne se teste pas comme du code applicatif, mais ses garde-fous
ne doivent pas pouvoir disparaître au fil des retouches. On vérifie donc qu'ils y sont.
"""

from __future__ import annotations

from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "scripts" / "reprise-gpu.sh"


@pytest.fixture(scope="module")
def source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_le_script_existe_et_est_executable() -> None:
    assert SCRIPT.is_file()


def test_il_refuse_un_point_de_terminaison_public(source: str) -> None:
    """D1 : l'inférence n'est jamais exposée. Un outil de reprise qui contournerait
    ce refus le rendrait inutile partout ailleurs."""
    assert "proxy.runpod.net" in source
    assert "ngrok" in source


def test_il_sonde_le_pod_AVANT_de_toucher_au_cluster(source: str) -> None:
    """Basculer vers une adresse morte couperait le service au lieu de le rétablir."""
    sonde = source.index("/v1/models")
    bascule = source.index("PROFIL_MATERIEL")
    assert sonde < bascule


def test_il_memorise_l_adresse_pour_la_veille_du_lendemain(source: str) -> None:
    """Écrire INFERENCE_URL sans INFERENCE_URL_GPU ferait échouer la reprise
    automatique du matin suivant, sans que rien ne le signale le jour même."""
    assert "INFERENCE_URL_GPU" in source


def test_il_verifie_le_service_public_et_pas_seulement_le_rollout(source: str) -> None:
    """Un rollout réussi ne prouve pas que les utilisateurs sont servis."""
    assert "/v1/ready" in source
    assert '"inference":true' in source


def test_il_rappelle_le_chemin_de_retour(source: str) -> None:
    """En cas de doute, l'opérateur doit lire la commande de repli sans la chercher."""
    assert "profil.sh cpu" in source


def test_il_documente_les_deux_reglages_du_pod(source: str) -> None:
    """Le volume réseau et la commande de démarrage sont les deux seuls réglages qui
    rendent la reprise en cinq minutes possible. Les oublier, c'est repousser 12 Go."""
    assert "iyqkq9jicv" in source
    assert "pre_start.sh" in source
    assert "EU-RO-1" in source
