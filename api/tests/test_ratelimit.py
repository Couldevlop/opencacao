"""Tests du limiteur de tentatives de connexion (anti-brute-force)."""

from __future__ import annotations

from app.curation.ratelimit import LimiteurConnexion


class HorlogeFausse:
    """Horloge contrôlable pour tester la fenêtre glissante."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_bloque_apres_max_echecs() -> None:
    limiteur = LimiteurConnexion(max_echecs=3, fenetre_s=300.0)
    assert limiteur.bloque("1.2.3.4") is False
    for _ in range(3):
        limiteur.echec("1.2.3.4")
    assert limiteur.bloque("1.2.3.4") is True


def test_succes_reinitialise() -> None:
    limiteur = LimiteurConnexion(max_echecs=3)
    for _ in range(3):
        limiteur.echec("1.2.3.4")
    limiteur.succes("1.2.3.4")
    assert limiteur.bloque("1.2.3.4") is False


def test_isolation_par_ip() -> None:
    limiteur = LimiteurConnexion(max_echecs=2)
    limiteur.echec("a")
    limiteur.echec("a")
    assert limiteur.bloque("a") is True
    assert limiteur.bloque("b") is False


def test_fenetre_glissante_expire() -> None:
    horloge = HorlogeFausse()
    limiteur = LimiteurConnexion(max_echecs=2, fenetre_s=100.0, horloge=horloge)
    limiteur.echec("ip")
    limiteur.echec("ip")
    assert limiteur.bloque("ip") is True
    # Au-delà de la fenêtre, les anciens échecs sont purgés.
    horloge.t = 101.0
    assert limiteur.bloque("ip") is False
