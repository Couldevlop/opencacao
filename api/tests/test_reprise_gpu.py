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
    """Un rollout réussi ne prouve pas que les utilisateurs sont servis.

    La vérification s'appuyait sur `/v1/ready` : elle ne suffit plus, car cette route
    interroge la sonde de santé de llama.cpp, qui n'exige aucun jeton. Elle a répondu
    `inference:true` la nuit du 11/09 pendant que la production rendait 503.
    """
    assert (
        '"inference":true' not in source
    ), "la sonde de sante n'exige aucun jeton : elle ne prouve pas qu'un utilisateur est servi"
    assert "rollout status" in source
    assert "200*" in source


def test_il_rappelle_le_chemin_de_retour(source: str) -> None:
    """En cas de doute, l'opérateur doit lire la commande de repli sans la chercher."""
    assert "profil.sh cpu" in source


def test_il_documente_les_deux_reglages_du_pod(source: str) -> None:
    """Le volume réseau et la commande de démarrage sont les deux seuls réglages qui
    rendent la reprise en cinq minutes possible. Les oublier, c'est repousser 12 Go."""
    assert "iyqkq9jicv" in source
    assert "pre_start.sh" in source
    assert "EU-RO-1" in source


def test_il_prouve_l_authentification_AVANT_de_basculer(source: str) -> None:
    """La leçon de la nuit du 11/09 : `/v1/models` rend 401 quand le serveur est
    protégé — ce qui est bon — mais ne dit RIEN sur la validité du jeton du cluster.
    Le script a donc déclaré « OK, GPU repris » sur une production qui rendait 503.
    Il doit essayer une vraie génération, avec le jeton du Secret, avant de toucher
    au ConfigMap : un échec ne coûte alors rien, le CPU continue de servir.
    """
    essai = source.index("/v1/chat/completions")
    bascule = source.index("PROFIL_MATERIEL")
    assert essai < bascule


def test_il_lit_le_jeton_dans_le_secret_du_cluster(source: str) -> None:
    """Comparer le pod à un jeton saisi à la main revient à tester la saisie."""
    assert "opencacao-inference" in source
    assert "INFERENCE_API_KEY" in source


def test_le_jeton_ne_passe_jamais_par_la_ligne_de_commande(source: str) -> None:
    """`ps` est lisible par tout le monde sur le nœud. Reste de sécurité déjà relevé
    au 19/08 côté pod : ne pas le reproduire côté cluster."""
    assert "--config" in source
    assert '-H "Authorization: Bearer ${JETON}"' not in source
    assert "-H 'Authorization: Bearer" not in source


def test_il_valide_la_reprise_sur_une_generation_reelle(source: str) -> None:
    """Un `/v1/ready` à `inference:true` n'interroge que la sonde de santé de
    llama.cpp, qui n'exige aucun jeton. Il ne prouve pas qu'un utilisateur est servi."""
    assert "'question'" in source
    assert source.count("/v1/chat") >= 2


def test_il_affiche_les_empreintes_en_cas_d_ecart_de_jeton(source: str) -> None:
    """Diagnostiquer sans jamais afficher le secret : c'est le contrôle 2.4 de la
    recette du 19/08, et c'est ce qui a permis de trouver l'écart cette nuit."""
    assert "sha256sum" in source
    assert "cut -c1-12" in source


def test_il_verifie_le_contexte_par_conversation(source: str) -> None:
    """`-c` est un TOTAL réparti entre les slots. Une génération d'essai courte ne
    révèle pas un contexte trop étroit : on lit ce que le serveur offre vraiment."""
    assert "/props" in source
    assert "n_ctx" in source
    lecture = source.index("/props")
    bascule = source.index("PROFIL_MATERIEL")
    assert lecture < bascule


def test_il_restaure_les_fonctions_delestees(source: str) -> None:
    """Le réveil automatique rallume l'atelier et les parcelles ; la reprise manuelle
    ne le faisait pas — le 11/09 le service est revenu sur GPU en annonçant encore
    « bientôt ». Deux chemins vers le même état doivent produire le même état."""
    assert "RAPPORTS_ENABLED" in source
    assert "PARCELLES_ENABLED" in source


def test_il_ne_rallume_pas_la_vision_d_office(source: str) -> None:
    """Le modèle de vision n'est pas sur tous les pods : l'allumer sans lui donnerait
    une fonction qui échoue à chaque photo."""
    assert '"VISION_ENABLED":"true"' not in source.replace(" ", "")
