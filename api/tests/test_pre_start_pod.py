"""Le script de démarrage automatique du pod loué — verrouillé par le fichier.

Il a vécu jusqu'au 10/09 sur le seul volume réseau RunPod, et a disparu avec lui. Ce
test ne vérifie pas qu'il « marche » — cela se prouve sur un pod — mais que les
décisions qui ont coûté cher à trouver ne repartent pas à la faveur d'une retouche.
"""

from __future__ import annotations

from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "scripts" / "pod" / "pre_start.sh"


@pytest.fixture(scope="module")
def source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_le_script_est_versionne() -> None:
    """La leçon du 10/09 : un fichier dont l'unique exemplaire vit sur une machine
    louée n'est pas sauvegardé, il est emprunté."""
    assert SCRIPT.is_file()


def test_l_etat_tailscale_vit_sur_le_volume(source: str) -> None:
    """Sur le disque du conteneur, chaque redémarrage donnait une nouvelle identité —
    et la reprise automatique du matin frappait à une adresse morte (nuit du 19/08)."""
    assert "--state=" in source
    assert "/tailscale/tailscaled.state" in source


def test_le_tunnel_est_en_mode_utilisateur(source: str) -> None:
    """/dev/net/tun est absent des conteneurs RunPod."""
    assert "--tun=userspace-networking" in source


def test_le_jeton_ne_passe_jamais_par_la_ligne_de_commande(source: str) -> None:
    """`ps` est lisible par tous sur le pod : reste de sécurité relevé le 19/08."""
    assert "--api-key-file" in source
    assert "--api-key " not in source


def test_il_refuse_de_servir_sans_jeton(source: str) -> None:
    """Une inférence qui répondrait nue derrière le tunnel annulerait la défense en
    profondeur de la spec §4.5."""
    assert "JETON_FICHIER" in source
    assert "inference NON demarree" in source


def test_les_couches_sont_delestees_sur_la_carte(source: str) -> None:
    """Sans -ngl, le serveur démarre parfaitement… et sert à la vitesse du CPU. On
    aurait loué un GPU pour rien, et seul le débit le dirait."""
    assert "-ngl 99" in source


def test_il_n_expose_aucun_port_public(source: str) -> None:
    """D1 : jamais par le proxy RunPod, uniquement par le tunnel privé."""
    assert "proxy.runpod.net" not in source
    assert "--no-webui" in source


def test_il_est_idempotent(source: str) -> None:
    """C'est le mode dégradé quand la Container Start Command n'a pas été renseignée :
    on le relance à la main, et il ne doit pas doubler le serveur en silence."""
    assert "pgrep -f llama-server" in source
    assert "deja en cours" in source


def test_il_attend_la_disponibilite_reelle(source: str) -> None:
    """Un processus vivant n'est pas un modèle chargé."""
    assert "/health" in source


def test_il_ne_tire_rien_depuis_le_noeud_de_production(source: str) -> None:
    """Tirer exigerait une clé du serveur de production sur une machine louée."""
    assert "62.238.11.20" not in source
    assert "scp" not in source
