"""Les droits du pilotage — le périmètre verrouillé par le fichier, pas par la confiance.

La console peut désormais suspendre des travaux planifiés et basculer le matériel de
production. C'est le droit le plus lourd du cluster : ces tests interdisent qu'il
s'élargisse par inadvertance.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

K8S = Path(__file__).resolve().parents[2] / "deploy" / "k8s"


@pytest.fixture(scope="module")
def role() -> dict:
    documents = [
        d
        for d in yaml.safe_load_all((K8S / "curation-rbac.yaml").read_text(encoding="utf-8"))
        if d and d.get("metadata", {}).get("name") == "curation-fenetre-gpu"
    ]
    roles = [d for d in documents if d["kind"] == "Role"]
    assert roles, "le Role de pilotage doit exister"
    return roles[0]


def test_chaque_regle_nomme_ses_objets(role: dict) -> None:
    """Sans `resourceNames`, le droit porterait sur toute la catégorie."""
    for regle in role["rules"]:
        assert regle.get("resourceNames"), regle


def test_seuls_les_quatre_creneaux_de_la_fenetre_sont_pilotables(role: dict) -> None:
    """Ni l'enrichissement du corpus, ni le chien de garde qui le surveille."""
    crons = [r for r in role["rules"] if r["resources"] == ["cronjobs"]][0]
    assert set(crons["resourceNames"]) == {
        "fenetre-fermeture",
        "fenetre-rappel",
        "fenetre-ouverture-annonce",
        "fenetre-ouverture",
    }


def test_aucun_joker_dans_les_droits(role: dict) -> None:
    for regle in role["rules"]:
        assert "*" not in regle["verbs"]
        assert "*" not in regle["resources"]
        assert "*" not in regle.get("apiGroups", [])


def test_aucun_droit_de_suppression_ni_de_creation(role: dict) -> None:
    """On règle ce qui existe ; on ne crée ni ne détruit rien depuis un écran."""
    for regle in role["rules"]:
        assert set(regle["verbs"]) <= {"get", "patch"}, regle


def test_la_console_ne_peut_pas_enumerer_le_namespace(role: dict) -> None:
    """Elle connaît ses quatre noms : `list` lui ouvrirait tout le reste."""
    for regle in role["rules"]:
        assert "list" not in regle["verbs"]


def test_aucun_droit_sur_les_secrets(role: dict) -> None:
    """Le jeton d'inférence et les identifiants d'authentification y vivent."""
    for regle in role["rules"]:
        assert "secrets" not in regle["resources"]


def test_le_role_est_lie_au_compte_de_la_console() -> None:
    liaisons = [
        d
        for d in yaml.safe_load_all((K8S / "curation-rbac.yaml").read_text(encoding="utf-8"))
        if d and d.get("kind") == "RoleBinding" and d["metadata"]["name"] == "curation-fenetre-gpu"
    ]
    assert liaisons and liaisons[0]["subjects"][0]["name"] == "curation"
