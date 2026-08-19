"""Le manifeste de la sentinelle — ce qu'elle a le droit de faire, et rien de plus.

Ce processus est le seul du cluster capable de **modifier la configuration de
production et de redémarrer l'API**. C'est exactement le genre de composant qui, mal
cadré, transforme une faille mineure en compromission totale : un pod qui peut lire les
Secrets et patcher n'importe quel déploiement est un administrateur déguisé.

Ces tests verrouillent donc le périmètre par le fichier, pas par la confiance :

* **aucun joker** dans le Role — ni sur les verbes, ni sur les ressources ;
* **aucun droit sur les Secrets** : la sentinelle n'a rien à y lire, et le jeton
  d'authentification comme celui de l'inférence y vivent ;
* **des ``resourceNames``** : le droit ne porte que sur les objets nommés, pas sur la
  catégorie ;
* **un ServiceAccount à elle** : mutualiser celui de la curation étendrait ses droits
  à la console web, qui est exposée ;
* **un conteneur durci** (non-root, système de fichiers en lecture seule, aucune
  capacité, pas d'élévation de privilège) ;
* **aucune surface réseau entrante** : ni Service, ni Ingress, et une NetworkPolicy
  qui ferme l'ingress — un pod que ne sélectionne aucune politique est ouvert à tout
  le cluster.
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
def manifeste() -> list[dict]:
    return objets("sentinelle.yaml")


@pytest.fixture(scope="module")
def role(manifeste: list[dict]) -> dict:
    return par_type(manifeste, "Role")[0]


@pytest.fixture(scope="module")
def deploiement(manifeste: list[dict]) -> dict:
    return par_type(manifeste, "Deployment")[0]


@pytest.fixture(scope="module")
def conteneur(deploiement: dict) -> dict:
    return deploiement["spec"]["template"]["spec"]["containers"][0]


def test_la_sentinelle_a_son_propre_compte_de_service(
    manifeste: list[dict], deploiement: dict
) -> None:
    """Réutiliser celui de la curation donnerait ces pouvoirs à une console exposée."""
    comptes = par_type(manifeste, "ServiceAccount")
    assert [compte["metadata"]["name"] for compte in comptes] == ["sentinelle"]
    assert deploiement["spec"]["template"]["spec"]["serviceAccountName"] == "sentinelle"


def test_le_role_ne_contient_aucun_joker(role: dict) -> None:
    """Un « * » ici, et le pod devient administrateur du namespace."""
    for regle in role["rules"]:
        assert "*" not in regle.get("verbs", [])
        assert "*" not in regle.get("resources", [])
        assert "*" not in regle.get("apiGroups", [])


def test_le_role_ne_touche_jamais_aux_secrets(role: dict) -> None:
    """Le jeton d'authentification et celui de l'inférence y vivent."""
    for regle in role["rules"]:
        assert "secrets" not in regle.get("resources", [])


def test_le_role_est_borne_aux_objets_nommes(role: dict) -> None:
    """Sans resourceNames, le droit porte sur TOUS les objets de la catégorie."""
    nommes: set[str] = set()
    for regle in role["rules"]:
        assert regle.get("resourceNames"), f"règle sans resourceNames : {regle}"
        nommes.update(regle["resourceNames"])
    assert nommes == {"api-config", "inference", "api"}


def test_le_role_n_accorde_que_lire_et_modifier(role: dict) -> None:
    """Ni création ni suppression : la sentinelle modifie l'existant, elle ne détruit rien."""
    autorises = {"get", "patch"}
    for regle in role["rules"]:
        assert set(regle["verbs"]) <= autorises, f"verbe de trop : {regle['verbs']}"


def test_le_role_ne_peut_pas_eteindre_le_deploiement_gpu(role: dict) -> None:
    """Un pod lent n'est pas un pod mort : détruire ce qu'on n'a pas su joindre est
    le meilleur moyen de perdre ce qui serait revenu."""
    for regle in role["rules"]:
        assert "inference-gpu" not in regle.get("resourceNames", [])


def test_le_conteneur_est_durci(conteneur: dict) -> None:
    """OWASP/CIS : non-root, aucune capacité, aucune élévation, racine en lecture seule."""
    contexte = conteneur["securityContext"]
    assert contexte["runAsNonRoot"] is True
    assert contexte["allowPrivilegeEscalation"] is False
    assert contexte["readOnlyRootFilesystem"] is True
    assert contexte["capabilities"]["drop"] == ["ALL"]


def test_le_conteneur_a_un_plafond_de_ressources(conteneur: dict) -> None:
    """Un processus en boucle sans limite peut affamer l'API sur un nœud unique."""
    assert conteneur["resources"]["limits"]["memory"]
    assert conteneur["resources"]["limits"]["cpu"]


def test_la_sentinelle_lance_bien_son_module(conteneur: dict) -> None:
    assert conteneur["command"] == ["python", "-m", "app.exploitation.sentinelle"]


def test_la_sentinelle_n_est_jamais_joignable(manifeste: list[dict]) -> None:
    """Aucun Service, aucun Ingress : rien ne doit pouvoir lui parler."""
    assert par_type(manifeste, "Service") == []
    assert par_type(manifeste, "Ingress") == []


def test_une_politique_reseau_ferme_l_ingress_de_la_sentinelle() -> None:
    """Un pod que ne sélectionne AUCUNE politique est en ingress ouvert."""
    politiques = par_type(objets("networkpolicy.yaml"), "NetworkPolicy")
    sienne = [
        politique
        for politique in politiques
        if politique["spec"]["podSelector"].get("matchLabels", {}).get("app") == "sentinelle"
    ]
    assert sienne, "aucune NetworkPolicy ne couvre la sentinelle"
    assert sienne[0]["spec"]["policyTypes"] == ["Ingress"]
    # Pas de section `ingress` du tout = tout trafic entrant refusé.
    assert not sienne[0]["spec"].get("ingress")


def test_le_manifeste_est_deploye() -> None:
    """Un manifeste absent de la kustomization n'existe pas dans le cluster."""
    kustomization = yaml.safe_load((K8S / "kustomization.yaml").read_text(encoding="utf-8"))
    assert "sentinelle.yaml" in kustomization["resources"]


def test_l_expediteur_des_alertes_est_celui_que_zeptomail_accepte(conteneur: dict) -> None:
    """`noreply@` est refusé (SM_147) : une alerte non partie est une alerte perdue."""
    variables = {var["name"]: var.get("value") for var in conteneur["env"]}
    assert variables["EMAIL_FROM"] == "waopron@openlabconsulting.com"


# --------------------------------------------------------------------------------
# GitOps — ArgoCD ne doit pas défaire le repli.
# --------------------------------------------------------------------------------

ARGOCD = Path(__file__).resolve().parents[2] / "deploy" / "argocd" / "application.yaml"

# Champs que la BASCULE (manuelle ou automatique) modifie dans le cluster vivant, et
# qui ne peuvent donc pas être tenus par Git.
CHAMPS_MUTABLES = {
    "PROFIL_MATERIEL",
    "INFERENCE_BACKEND",
    "INFERENCE_URL",
    "VISION_ENABLED",
    "RAPPORTS_ENABLED",
    "PARCELLES_ENABLED",
    "REPLI_CPU",
}


@pytest.fixture(scope="module")
def application() -> dict:
    return yaml.safe_load(ARGOCD.read_text(encoding="utf-8"))


def test_argocd_repare_bien_les_derives(application: dict) -> None:
    """Contre-épreuve : si selfHeal était éteint, le test suivant ne prouverait rien."""
    assert application["spec"]["syncPolicy"]["automated"]["selfHeal"] is True


def test_argocd_ne_defait_pas_le_repli_sur_la_configmap(application: dict) -> None:
    """`selfHeal` ré-applique l'état de Git. Sans exception explicite, ArgoCD
    annulerait le patch de la sentinelle en quelques minutes — le service repartirait
    vers un GPU mort, et le bandeau d'avis disparaîtrait de l'écran."""
    exceptions = application["spec"].get("ignoreDifferences", [])
    configmap = [
        exception
        for exception in exceptions
        if exception.get("kind") == "ConfigMap" and exception.get("name") == "api-config"
    ]
    assert configmap, "aucune exception ArgoCD sur la ConfigMap api-config"
    ignores = " ".join(configmap[0].get("jsonPointers", []))
    for champ in CHAMPS_MUTABLES:
        assert champ in ignores, f"{champ} serait ré-écrasé par ArgoCD"


def test_argocd_ne_rallume_pas_l_inference_gpu(application: dict) -> None:
    """Le nombre de répliques est piloté par la bascule, pas par Git : sans exception,
    ArgoCD remettrait `inference` à 0 juste après que la sentinelle l'a remonté."""
    exceptions = application["spec"].get("ignoreDifferences", [])
    deploiements = [
        exception
        for exception in exceptions
        if exception.get("kind") == "Deployment"
        and "/spec/replicas" in exception.get("jsonPointers", [])
    ]
    noms = {exception.get("name") for exception in deploiements}
    assert {"inference", "inference-gpu"} <= noms
