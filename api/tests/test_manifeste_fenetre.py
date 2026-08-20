"""Le manifeste de la fenêtre GPU — les invariants qu'un YAML doit porter seul.

Ces quatre travaux modifient la configuration de production et redémarrent l'API. Les
erreurs qui coûteraient le plus cher ne sont pas dans le code Python — elles sont dans
le manifeste, où une heure, un fuseau ou un drapeau mal posé ne se voit à l'exécution
que le lendemain matin :

* un **fuseau absent** ferait tomber la fermeture à 02:00 heure de Paris, en plein
  usage l'hiver et décalée d'une heure l'été ;
* **deux travaux bavards** au lieu d'un enverraient 48 emails par jour ;
* une **veille qui déborde sur minuit** rouvrirait ce que la fermeture vient de fermer,
  indéfiniment ;
* un **ServiceAccount trop large** ferait de ces travaux des administrateurs déguisés.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

K8S = Path(__file__).resolve().parents[2] / "deploy" / "k8s"


@pytest.fixture(scope="module")
def documents() -> list[dict]:
    contenu = (K8S / "cron-fenetre-gpu.yaml").read_text(encoding="utf-8")
    return [doc for doc in yaml.safe_load_all(contenu) if doc]


@pytest.fixture(scope="module")
def crons(documents: list[dict]) -> dict[str, dict]:
    return {d["metadata"]["name"]: d for d in documents if d.get("kind") == "CronJob"}


def _conteneur(cron: dict) -> dict:
    return cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]


def _env(cron: dict) -> dict[str, str]:
    return {e["name"]: e.get("value") for e in _conteneur(cron).get("env", [])}


def test_les_quatre_creneaux_sont_declares(crons: dict[str, dict]) -> None:
    """Fermeture, rappel, annonce d'ouverture, veille d'ouverture."""
    assert set(crons) == {
        "fenetre-fermeture",
        "fenetre-rappel",
        "fenetre-ouverture-annonce",
        "fenetre-ouverture",
    }


@pytest.mark.parametrize(
    ("nom", "horaire"),
    [
        ("fenetre-fermeture", "0 0 * * *"),
        ("fenetre-rappel", "0 6 * * *"),
        ("fenetre-ouverture-annonce", "0 10 * * *"),
        ("fenetre-ouverture", "*/15 10-21 * * *"),
    ],
)
def test_les_horaires_sont_ceux_convenus(crons: dict[str, dict], nom: str, horaire: str) -> None:
    """Minuit ferme, 06:00 rappelle, 10:00 annonce, puis la veille toutes les 15 min."""
    assert crons[nom]["spec"]["schedule"] == horaire


def test_tous_les_creneaux_portent_le_fuseau_de_paris(crons: dict[str, dict]) -> None:
    """Sans `timeZone`, Kubernetes planifie en UTC : « minuit » tomberait à 02:00 locales."""
    for nom, cron in crons.items():
        assert cron["spec"].get("timeZone") == "Europe/Paris", nom


def test_la_veille_s_arrete_avant_minuit(crons: dict[str, dict]) -> None:
    """Une veille qui court jusqu'à minuit rouvrirait ce que la fermeture vient de fermer."""
    heures = crons["fenetre-ouverture"]["spec"]["schedule"].split()[1]
    derniere = int(heures.split("-")[1])
    assert derniere <= 21


def test_un_seul_travail_a_le_droit_d_annoncer_un_pod_absent(crons: dict[str, dict]) -> None:
    """C'est CE réglage qui sépare une alerte utile de 48 emails quotidiens ignorés."""
    assert _env(crons["fenetre-ouverture-annonce"])["PREVENIR_SI_ABSENT"] == "true"
    assert _env(crons["fenetre-ouverture"])["PREVENIR_SI_ABSENT"] == "false"


def test_les_verbes_correspondent_aux_creneaux(crons: dict[str, dict]) -> None:
    """Une faute de verbe ferait fermer le service à l'heure où il doit ouvrir."""
    attendus = {
        "fenetre-fermeture": "fermer",
        "fenetre-rappel": "rappeler",
        "fenetre-ouverture-annonce": "ouvrir",
        "fenetre-ouverture": "ouvrir",
    }
    for nom, verbe in attendus.items():
        assert _conteneur(crons[nom])["command"][-1] == verbe
        assert _conteneur(crons[nom])["command"][:3] == [
            "python",
            "-m",
            "app.exploitation.fenetre",
        ]


def test_les_travaux_empruntent_le_compte_de_la_sentinelle(crons: dict[str, dict]) -> None:
    """Mêmes gestes qu'elle, sur les mêmes objets : un compte de plus n'ajouterait rien.

    Surtout PAS le compte `curation`, qui sert la console web exposée sur Internet.
    """
    for nom, cron in crons.items():
        spec = cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]
        assert spec["serviceAccountName"] == "sentinelle", nom


def test_aucun_travail_ne_se_chevauche(crons: dict[str, dict]) -> None:
    """Deux ouvertures simultanées patcheraient la même ConfigMap en même temps."""
    for nom, cron in crons.items():
        assert cron["spec"]["concurrencyPolicy"] == "Forbid", nom


def test_le_jeton_email_est_optionnel(crons: dict[str, dict]) -> None:
    """Un Secret absent doit sauter l'email, jamais empêcher la bascule matérielle."""
    for nom, cron in crons.items():
        jeton = [e for e in _conteneur(cron)["env"] if e["name"] == "ZEPTOMAIL_TOKEN"]
        assert jeton and jeton[0]["valueFrom"]["secretKeyRef"]["optional"] is True, nom


def test_le_manifeste_est_pris_par_kustomize() -> None:
    """Un manifeste absent de kustomization.yaml n'est jamais déployé."""
    kustom = yaml.safe_load((K8S / "kustomization.yaml").read_text(encoding="utf-8"))
    assert "cron-fenetre-gpu.yaml" in kustom["resources"]


def test_les_reglages_communs_sont_partages_par_configmap(
    documents: list[dict], crons: dict[str, dict]
) -> None:
    """Quatre travaux, un seul endroit à corriger le jour où le coût horaire change."""
    configs = [d for d in documents if d.get("kind") == "ConfigMap"]
    assert len(configs) == 1
    assert configs[0]["metadata"]["name"] == "fenetre-config"
    for nom, cron in crons.items():
        refs = [
            r["configMapRef"]["name"] for r in _conteneur(cron)["envFrom"] if "configMapRef" in r
        ]
        assert "fenetre-config" in refs, nom


def test_les_travaux_recoivent_les_identifiants_du_relais_smtp(crons: dict[str, dict]) -> None:
    """Sans ce Secret, aucune alerte ne part — et le rappel du soir est le seul
    garde-fou contre un pod loué resté allumé."""
    for nom, cron in crons.items():
        refs = [r["secretRef"]["name"] for r in _conteneur(cron)["envFrom"] if "secretRef" in r]
        assert "opencacao-smtp" in refs, nom


def test_le_secret_smtp_est_optionnel(crons: dict[str, dict]) -> None:
    """Un Secret absent doit sauter l'email, jamais empêcher la bascule matérielle."""
    for nom, cron in crons.items():
        smtp = [
            r
            for r in _conteneur(cron)["envFrom"]
            if r.get("secretRef", {}).get("name") == "opencacao-smtp"
        ]
        assert smtp and smtp[0]["secretRef"]["optional"] is True, nom
