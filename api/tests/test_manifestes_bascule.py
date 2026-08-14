"""Cohérence des manifestes de bascule CPU <-> GPU (spec V3 §4.5).

Ces tests ne portent pas sur du code applicatif : ils portent sur trois fichiers YAML
qui doivent s'accorder sur des LABELS, et dont le désaccord ne se voit qu'en
production, un jour de bascule. Ils vivent ici parce que c'est la suite qui tourne en
CI (`.github/workflows/ci.yml`), et qu'un test qui ne tourne pas ne protège rien.

Trois pièges sont verrouillés :

* **L'adoption de pods.** Le sélecteur d'un Deployment est immuable et celui du CPU
  vaut ``app: inference``. Si les pods GPU portaient le même ``app``, le ReplicaSet CPU
  les adopterait puis les supprimerait, croyant avoir une réplique de trop.
* **Le trou d'endpoints.** Le Service doit suivre un label porté par les DEUX profils,
  sinon la bascule change l'URL d'inférence — ce que toute la conception évite.
* **Le cloisonnement perdu.** Chaque profil doit être couvert par une NetworkPolicy, et
  celle-ci ne doit ouvrir qu'à l'API. Un pod que ne sélectionne aucune politique est en
  ingress ouvert : l'inférence deviendrait joignable par n'importe quel pod du cluster,
  garde-fous et journalisation contournés, ce que D1 interdit.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

K8S = Path(__file__).resolve().parents[2] / "deploy" / "k8s"


def objets(nom_fichier: str) -> list[dict]:
    """Charge les documents YAML d'un manifeste."""
    contenu = (K8S / nom_fichier).read_text(encoding="utf-8")
    return [doc for doc in yaml.safe_load_all(contenu) if doc]


def par_type(documents: list[dict], genre: str) -> list[dict]:
    """Filtre les documents par ``kind``."""
    return [doc for doc in documents if doc.get("kind") == genre]


@pytest.fixture(scope="module")
def cpu() -> dict:
    return par_type(objets("inference.yaml"), "Deployment")[0]


@pytest.fixture(scope="module")
def gpu() -> dict:
    return par_type(objets("inference-gpu.yaml"), "Deployment")[0]


@pytest.fixture(scope="module")
def service() -> dict:
    return par_type(objets("inference.yaml"), "Service")[0]


def test_les_deux_profils_sont_des_deploiements_distincts(cpu: dict, gpu: dict) -> None:
    """Même nom = appliquer l'un détruit l'autre, et le retour arrière n'est plus un scale."""
    assert cpu["metadata"]["name"] != gpu["metadata"]["name"]


def test_le_replicaset_cpu_ne_peut_pas_adopter_les_pods_gpu(cpu: dict, gpu: dict) -> None:
    """Le sélecteur de l'un ne doit JAMAIS matcher les labels de pod de l'autre."""
    labels_gpu = gpu["spec"]["template"]["metadata"]["labels"]
    selecteur_cpu = cpu["spec"]["selector"]["matchLabels"]
    assert not all(labels_gpu.get(cle) == valeur for cle, valeur in selecteur_cpu.items())

    labels_cpu = cpu["spec"]["template"]["metadata"]["labels"]
    selecteur_gpu = gpu["spec"]["selector"]["matchLabels"]
    assert not all(labels_cpu.get(cle) == valeur for cle, valeur in selecteur_gpu.items())


def test_le_service_suit_un_label_porte_par_les_deux_profils(
    cpu: dict, gpu: dict, service: dict
) -> None:
    """C'est ce qui rend la bascule invisible : INFERENCE_URL ne change jamais."""
    selecteur = service["spec"]["selector"]
    for deploiement in (cpu, gpu):
        labels = deploiement["spec"]["template"]["metadata"]["labels"]
        assert all(
            labels.get(cle) == valeur for cle, valeur in selecteur.items()
        ), f"{deploiement['metadata']['name']} ne serait pas servi par le Service"


def test_un_seul_service_nomme_inference() -> None:
    """Deux Services de même nom : le second écrase le premier, en silence."""
    services = [
        document
        for chemin in K8S.glob("*.yaml")
        for document in yaml.safe_load_all(chemin.read_text(encoding="utf-8"))
        if document and document.get("kind") == "Service"
        if document["metadata"]["name"] == "inference"
    ]
    assert len(services) == 1


def test_le_profil_gpu_ne_reserve_rien_tant_qu_on_ne_bascule_pas(gpu: dict) -> None:
    """Répliques à 0 : l'objet existe pour être mis à l'échelle, pas pour tourner."""
    assert gpu["spec"]["replicas"] == 0


def test_le_profil_cpu_sert_par_defaut(cpu: dict) -> None:
    assert cpu["spec"]["replicas"] == 1


def test_le_cloisonnement_couvre_les_deux_profils(cpu: dict, gpu: dict) -> None:
    """D1 : l'inférence n'est joignable que par l'API — sur GPU aussi.

    On demande qu'il EXISTE une politique couvrant chaque profil, sans exiger laquelle :
    un pod que ne sélectionne aucune politique est en ingress ouvert, et c'est cela
    seulement qui doit rester impossible.
    """
    politiques = par_type(objets("networkpolicy.yaml"), "NetworkPolicy")
    for deploiement in (cpu, gpu):
        labels = deploiement["spec"]["template"]["metadata"]["labels"]
        couvert = [
            politique["metadata"]["name"]
            for politique in politiques
            if all(
                labels.get(cle) == valeur
                for cle, valeur in politique["spec"]["podSelector"]["matchLabels"].items()
            )
        ]
        assert couvert, f"{deploiement['metadata']['name']} sortirait du cloisonnement"


def test_le_cloisonnement_de_l_inference_n_admet_que_l_api(cpu: dict, gpu: dict) -> None:
    """Couvert ne suffit pas : encore faut-il que la politique n'ouvre qu'à l'API."""
    politiques = par_type(objets("networkpolicy.yaml"), "NetworkPolicy")
    for deploiement in (cpu, gpu):
        labels = deploiement["spec"]["template"]["metadata"]["labels"]
        for politique in politiques:
            selecteur = politique["spec"]["podSelector"]["matchLabels"]
            if not all(labels.get(cle) == valeur for cle, valeur in selecteur.items()):
                continue
            sources = [
                origine["podSelector"]["matchLabels"]
                for regle in politique["spec"]["ingress"]
                for origine in regle["from"]
            ]
            assert sources == [
                {"app": "api"}
            ], f"{politique['metadata']['name']} ouvre l'inférence à autre chose que l'API"


def test_les_deux_profils_servent_le_meme_nom_de_modele(cpu: dict, gpu: dict) -> None:
    """MODEL_NAME est envoyé par l'API : servi sous un autre nom, l'inférence répond 404."""
    attendu = "opencacao-8b"
    for deploiement in (cpu, gpu):
        arguments = deploiement["spec"]["template"]["spec"]["containers"][0]["args"]
        assert attendu in arguments, f"{deploiement['metadata']['name']} : alias absent"
