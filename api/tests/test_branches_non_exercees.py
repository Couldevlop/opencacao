"""Chemins « faux » de conditions que rien n'exerçait.

Activer la couverture par BRANCHES a révélé une famille de trous invisibles à la
couverture par lignes : des ``if`` dont seule l'issue vraie était jouée. La ligne était
comptée couverte, l'autre moitié du comportement ne l'était pas.

Aucun de ces cas n'est théorique : un titre dont le dernier espace tombe trop tôt, un
contact sans siège ni coordonnée, deux localités dont la seconde ne l'emporte pas. Ils
se produisent, simplement personne ne les avait écrits.
"""

from __future__ import annotations

from app.application.memoire import _court
from app.services import localites, titres
from app.services.contacts import ContactDR, formater


class TestTroncatureSansFrontiereDeMot:
    """Quand couper au dernier mot amputerait plus de la moitié du texte."""

    def test_un_titre_dont_le_dernier_espace_tombe_trop_tot(self) -> None:
        # « Bonjour » puis un mot très long : couper à l'espace ne laisserait presque
        # rien. On coupe alors dans le mot plutôt que de rendre un titre vide de sens.
        question = "abc " + "z" * 200
        titre = titres.depuis_question(question, longueur_max=60)
        assert titre.endswith("…")
        # La coupe est DANS le mot long : le titre reste informatif.
        assert len(titre) > 30
        assert "z" in titre

    def test_un_titre_dont_l_espace_est_bien_place_coupe_au_mot(self) -> None:
        # Contre-épreuve : sans elle, le test ci-dessus passerait aussi si la coupe au
        # mot avait disparu du code.
        question = "la production de cacao dans la region de Soubre cette annee " * 3
        titre = titres.depuis_question(question, longueur_max=60)
        assert titre.endswith("…")
        assert not titre[:-1].endswith(" ")

    def test_un_fragment_de_memoire_sans_espace_utilisable(self) -> None:
        assert _court("ab " + "y" * 100, longueur_max=40).endswith("…")

    def test_un_fragment_de_memoire_coupe_au_mot(self) -> None:
        resume = _court("la production de cacao progresse dans la region de Soubre", 40)
        assert resume.endswith("…")
        assert " " in resume


class TestMiseEnFormeD_unContact:
    """Toutes les colonnes de l'annuaire ne sont pas toujours remplies."""

    def test_un_contact_sans_siege_ne_porte_pas_de_parenthese_vide(self) -> None:
        contact = ContactDR(
            nom="DR Soubré", siege="", tel="+225 27 00 00 00", email="", verifie=True
        )
        rendu = formater(contact)
        assert "DR Soubré" in rendu
        assert "siège" not in rendu
        assert "(" not in rendu

    def test_un_contact_sans_aucune_coordonnee_reste_lisible(self) -> None:
        # Une DR connue mais dont on n'a ni téléphone ni courriel : on la nomme
        # quand même, plutôt que de faire disparaître l'information.
        contact = ContactDR(nom="DR Man", siege="Man", tel="", email="", verifie=False)
        rendu = formater(contact)
        assert "DR Man" in rendu
        assert "·" not in rendu
        # Non vérifié : la réserve est portée, jamais tue (principe de vérité).
        assert "confirmer" in rendu

    def test_un_contact_complet_porte_ses_deux_coordonnees(self) -> None:
        contact = ContactDR(
            nom="DR Daloa",
            siege="Daloa",
            tel="+225 27 00 00 00",
            email="daloa@anader.ci",
            verifie=True,
        )
        rendu = formater(contact)
        assert "·" in rendu
        assert "confirmer" not in rendu


class TestChoixEntrePlusieursLocalites:
    """Deux localités citées : la seconde ne l'emporte pas toujours."""

    def test_la_localite_la_plus_tardive_l_emporte(self) -> None:
        # Le fil de la phrase compte : « je viens de Daloa, je suis à Soubré » parle
        # de Soubré. La règle existait, son issue inverse n'était pas exercée.
        assert localites.detecter("je viens de Daloa mais je suis à Soubré") == "Soubré"

    def test_une_seconde_mention_moins_tardive_ne_supplante_pas(self) -> None:
        assert localites.detecter("je suis à Soubré, pas à Daloa") == "Daloa"

    def test_aucune_localite_reconnue(self) -> None:
        assert localites.detecter("bonjour, comment ça va ?") is None
