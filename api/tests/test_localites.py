"""Tests du module de détection de localités ivoiriennes."""

from __future__ import annotations

from pathlib import Path

from app.services import localites


def test_detecter_localite_cacaoyere_insensible_casse_accents() -> None:
    assert localites.detecter("Quel temps à daloa ?") == "Daloa"


def test_detecter_libelle_compose() -> None:
    # Libellé en deux mots reconnu (le plus long prime).
    assert localites.detecter("prévisions sur san pedro") == "San Pedro"


def test_detecter_mot_frontiere_pas_de_match_partiel() -> None:
    # « Manioc » ne doit pas matcher la zone « Man ».
    assert localites.detecter("je cultive le manioc") is None


def test_detecter_aucune_localite() -> None:
    assert localites.detecter("bonjour, comment ça va ?") is None


def test_detecter_exclut_ville_nord() -> None:
    # Korhogo est dans le YAML mais hors filière cacao : exclu du détecteur cacaoyer.
    assert localites.detecter("quel temps à Korhogo ?") is None


def test_detecter_nord_reconnait_ville_nord() -> None:
    assert localites.detecter_nord("quel temps à Korhogo ?") == "Korhogo"


def test_detecter_nord_none_sur_ville_cacaoyere() -> None:
    assert localites.detecter_nord("quel temps à Daloa ?") is None


def test_chercher_zone_renvoie_dr_et_libelle() -> None:
    trouve = localites.chercher_zone("je suis planteur à Daloa")
    assert trouve is not None
    dr, zone = trouve
    assert dr["nom"] == "Direction Régionale Centre-Ouest"
    assert zone == "daloa"


def test_chercher_zone_inclut_le_nord() -> None:
    # Un producteur du Nord garde droit au contact ANADER.
    trouve = localites.chercher_zone("contact à Korhogo")
    assert trouve is not None
    dr, _zone = trouve
    assert dr["nom"] == "Direction Régionale Nord"


def test_yaml_illisible_degrade_proprement(monkeypatch) -> None:
    monkeypatch.setattr(localites, "_CHEMIN", Path("/inexistant/contacts.yaml"))
    localites._annuaire.cache_clear()
    localites._index.cache_clear()
    try:
        assert localites.detecter("quel temps à Daloa ?") is None
        assert localites.chercher_zone("Daloa") is None
    finally:
        localites._annuaire.cache_clear()
        localites._index.cache_clear()


def test_detecter_localite_la_plus_recente() -> None:
    # Plusieurs villes cacaoyères : on géocode la DERNIÈRE citée (contexte courant),
    # même si une ville citée plus tôt a un libellé plus long.
    assert localites.detecter("j'étais à Gagnoa hier, et à Daloa aujourd'hui") == "Daloa"
    # Cas simple (une seule ville) inchangé.
    assert localites.detecter("quel temps à Gagnoa ?") == "Gagnoa"
