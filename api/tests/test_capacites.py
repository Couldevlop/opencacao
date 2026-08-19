"""L'API déclare ce qui est OUVERT, pour que l'interface n'offre rien de mort.

Depuis que les trois écrans partagent une seule fenêtre, la barre latérale propose ses
destinations en permanence. Or « Ma parcelle » et l'atelier vivent derrière des
drapeaux qu'on baisse — après une démonstration, ou parce qu'une étude coûte des
minutes de CPU et que l'inférence ne sert qu'une requête à la fois. Sans déclaration,
couper un drapeau laisserait dans l'écran une porte qui ne mène nulle part.

Ce n'est pas une fuite d'information : l'existence d'une route se découvre de toute
façon en l'appelant. C'est l'inverse — dire honnêtement ce qui est disponible.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def construire(tmp_path, monkeypatch):
    """Construit l'application avec l'environnement demandé, sans rien présupposer."""

    def _construire(**variables: str) -> TestClient:
        monkeypatch.setenv("SESSIONS_DB_PATH", str(tmp_path / "sessions.db"))
        monkeypatch.setenv("PARCELLES_DB_PATH", str(tmp_path / "parcelles.db"))
        monkeypatch.setenv("RAPPORTS_DB_PATH", str(tmp_path / "rapports.db"))
        monkeypatch.setenv("CAPTURES_DIR", str(tmp_path / "captures"))
        monkeypatch.setenv("PREWARM_ENABLED", "false")
        for cle, valeur in variables.items():
            monkeypatch.setenv(cle, valeur)
        from app.core.config import get_settings
        from app.main import create_app

        get_settings.cache_clear()
        return TestClient(create_app())

    yield _construire
    from app.core.config import get_settings

    get_settings.cache_clear()


def test_les_capacites_sont_declarees_fermees_par_defaut(construire) -> None:
    """Les drapeaux V3 sont à false par défaut : l'interface ne doit rien proposer."""
    with construire() as client:
        capacites = client.get("/v1/version").json()["capacites"]
        assert capacites["parcelles"] is False
        assert capacites["rapports"] is False
        assert capacites["vision"] is False


def test_un_drapeau_leve_ouvre_la_capacite(construire) -> None:
    """Contre-épreuve du test précédent : sans elle, « toujours false » resterait vert."""
    with construire(RAPPORTS_ENABLED="true", PARCELLES_ENABLED="true") as client:
        capacites = client.get("/v1/version").json()["capacites"]
        assert capacites["rapports"] is True
        assert capacites["parcelles"] is True


def test_la_vision_reste_fermee_sans_gpu(construire) -> None:
    """VISION_ENABLED ne suffit pas : sans GPU le VLM est absent (spec §7.7).

    L'API le dit plutôt que de laisser l'écran promettre une description qu'elle ne
    produira pas — le pattern « contexte vide -> fabrication » corrigé en v0.6.48 ne
    doit pas revenir par l'interface.
    """
    with construire(VISION_ENABLED="true", PROFIL_MATERIEL="cpu") as client:
        assert client.get("/v1/version").json()["capacites"]["vision"] is False


def test_la_vision_s_ouvre_avec_le_gpu(construire) -> None:
    with construire(VISION_ENABLED="true", PROFIL_MATERIEL="gpu") as client:
        assert client.get("/v1/version").json()["capacites"]["vision"] is True


def test_le_profil_materiel_est_expose(construire) -> None:
    """Utile en scène : vérifier d'un coup d'œil sur quoi tourne la production."""
    with construire(PROFIL_MATERIEL="gpu") as client:
        assert client.get("/v1/version").json()["profil_materiel"] == "gpu"


# --------------------------------------------------------------------------------
# Le repli automatique — l'interface doit pouvoir le DIRE, pas seulement le subir.
# --------------------------------------------------------------------------------


def test_hors_repli_l_avis_est_muet(construire) -> None:
    """En marche normale, aucun avis : on n'inquiète personne sans raison."""
    client = construire(PARCELLES_ENABLED="true", RAPPORTS_ENABLED="true")

    corps = client.get("/v1/version").json()

    assert corps["repli_cpu"] is False


def test_en_repli_l_api_le_declare(construire) -> None:
    """Sans ce drapeau, l'interface dirait « bientôt » — faux, et vexant pour qui
    utilisait la fonction une minute plus tôt."""
    client = construire(REPLI_CPU="true")

    corps = client.get("/v1/version").json()

    assert corps["repli_cpu"] is True


def test_en_repli_les_capacites_lourdes_sont_fermees(construire) -> None:
    """Le repli déleste : même si les drapeaux traînaient à `true`, l'API ne doit pas
    promettre une étude que le CPU ne peut pas produire sans tuer la conversation."""
    client = construire(REPLI_CPU="true", PARCELLES_ENABLED="true", RAPPORTS_ENABLED="true")

    capacites = client.get("/v1/version").json()["capacites"]

    assert capacites["parcelles"] is False
    assert capacites["rapports"] is False
    assert capacites["vision"] is False
