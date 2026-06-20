"""Tests de l'annuaire de mise en relation (contacts ANADER par zone)."""

from __future__ import annotations

from app.services import contacts


def test_chercher_zone_directe() -> None:
    """Une zone citée renvoie sa Direction Régionale."""
    c = contacts.chercher("Je suis planteur à Daloa")
    assert c is not None
    assert c.nom == "Direction Régionale Centre-Ouest"
    assert c.siege == "Daloa"


def test_chercher_insensible_accents_casse() -> None:
    c = contacts.chercher("je cultive vers SOUBRE")
    assert c is not None
    assert c.siege == "San Pedro"  # Soubré relève de la DR Sud-Ouest


def test_chercher_libelle_compose() -> None:
    """Un libellé en deux mots (San Pedro) est reconnu."""
    c = contacts.chercher("bonjour je suis a san pedro")
    assert c is not None
    assert c.nom == "Direction Régionale Sud-Ouest"


def test_chercher_zone_inconnue() -> None:
    assert contacts.chercher("je suis à Paris") is None


def test_intention_contact() -> None:
    assert contacts.intention_contact("Quel est le numéro de l'ANADER ?") is True
    assert contacts.intention_contact("comment joindre un agent") is True
    assert contacts.intention_contact("mes feuilles jaunissent") is False


def test_formater_contient_coordonnees() -> None:
    c = contacts.chercher("Korhogo")
    assert c is not None
    texte = contacts.formater(c)
    assert "Direction Régionale Nord" in texte
    assert "Korhogo" in texte
    assert c.tel and c.tel in texte


def test_siege_disponible() -> None:
    s = contacts.siege()
    assert s is not None
    assert "anader.ci" in s.email
