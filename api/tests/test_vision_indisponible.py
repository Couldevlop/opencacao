"""Tests de la source de vision neutre (profil CPU, ou VLM absent)."""

from __future__ import annotations

from app.services.vision.indisponible import CONSIGNE_INDISPONIBLE, VisionIndisponible


async def test_vision_indisponible_ne_decrit_rien():
    """Jamais de description inventée : None, que l'appelant devra traiter."""
    assert await VisionIndisponible().decrire((b"\xff\xd8",), "décris") is None


async def test_vision_indisponible_se_declare_absente():
    assert await VisionIndisponible().disponible() is False


def test_la_consigne_d_indisponibilite_est_explicite_et_oriente():
    """Le producteur doit comprendre ce qui se passe et quoi faire, pas voir une erreur."""
    assert "analyse" in CONSIGNE_INDISPONIBLE.lower()
    assert "ANADER" in CONSIGNE_INDISPONIBLE
