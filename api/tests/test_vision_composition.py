"""Tests du câblage de la source de vision selon le profil matériel (V3, C2).

Le VLM ne tient que sur GPU. Le point vérifié ici est la **décision** : en profil CPU,
ou vision désactivée, c'est la source neutre qui est branchée — celle qui se déclare
indisponible au lieu d'inventer une description (leçon v0.6.48).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.services.vision.indisponible import VisionIndisponible
from app.services.vision.vlm import ClientVLM


@pytest.fixture
def construire(tmp_path, monkeypatch):
    """Construit l'application avec l'environnement demandé."""

    def _construire(**variables: str) -> TestClient:
        monkeypatch.setenv("PARCELLES_ENABLED", "true")
        monkeypatch.setenv("PARCELLES_DB_PATH", str(tmp_path / "parcelles.db"))
        monkeypatch.setenv("CAPTURES_DIR", str(tmp_path / "captures"))
        monkeypatch.setenv("SESSIONS_DB_PATH", str(tmp_path / "sessions.db"))
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


def test_en_profil_cpu_la_source_neutre_est_branchee(construire):
    """Le VLM ne tient pas sur le CX53 : on ne prétend pas le contraire."""
    with construire(VISION_ENABLED="true", PROFIL_MATERIEL="cpu") as client:
        assert isinstance(client.app.state.vision, VisionIndisponible)


def test_vision_desactivee_donne_la_source_neutre_meme_sur_gpu(construire):
    """Le drapeau prime : une vision coupée reste coupée, GPU ou non."""
    with construire(VISION_ENABLED="false", PROFIL_MATERIEL="gpu") as client:
        assert isinstance(client.app.state.vision, VisionIndisponible)


def test_en_profil_gpu_avec_vision_active_le_client_vlm_est_branche(construire):
    """Seule combinaison qui parle réellement au modèle de vision."""
    with construire(VISION_ENABLED="true", PROFIL_MATERIEL="gpu") as client:
        assert isinstance(client.app.state.vision, ClientVLM)


def test_le_client_de_vision_est_ferme_a_l_arret(construire, monkeypatch):
    """Une session HTTP laissée ouverte fuit des sockets à chaque redémarrage."""
    fermetures: list[bool] = []
    ferme_reel = ClientVLM.close

    async def _close(self) -> None:
        fermetures.append(True)
        await ferme_reel(self)

    monkeypatch.setattr(ClientVLM, "close", _close)
    with construire(VISION_ENABLED="true", PROFIL_MATERIEL="gpu"):
        pass
    assert fermetures == [True]
