"""Tests de la résolution d'une demande en langage naturel vers un gabarit.

On ne coche pas des cases pour demander une étude : on la demande. Ce module
traduit « fais-moi une étude de la filière sur la campagne 2025-2026 » en
``(etude_filiere, "la campagne 2025-2026")``, sans appeler l'inférence.
"""

from __future__ import annotations

import pytest

from app.application.intention_rapport import DEMANDE_MAX, Intention, resoudre_demande
from app.services.gabarits import Gabarit


def gabarit(identifiant: str, declencheurs: tuple[str, ...]) -> Gabarit:
    """Construit un gabarit minimal portant un vocabulaire déclencheur."""
    return Gabarit(
        identifiant=identifiant,
        titre=f"{identifiant} — {{sujet}}",
        sous_titre="",
        public="",
        mention="",
        sections=(),
        declencheurs=declencheurs,
    )


CATALOGUE = (
    gabarit("etude_filiere", ("etude", "analyse", "filiere")),
    gabarit("bulletin_regional", ("bulletin", "region", "hebdomadaire")),
    gabarit("dossier_parcelle", ("dossier", "parcelle", "tracabilite")),
)


class TestReconnaissanceDuType:
    """Le type de document se déduit de la demande."""

    @pytest.mark.parametrize(
        ("demande", "attendu"),
        [
            ("Fais-moi une étude de la filière cacao", "etude_filiere"),
            ("Je voudrais un bulletin pour la région de Daloa", "bulletin_regional"),
            ("Prépare le dossier de la parcelle de Kouadio", "dossier_parcelle"),
        ],
    )
    def test_reconnait_le_type_demande(self, demande: str, attendu: str) -> None:
        intention = resoudre_demande(demande, CATALOGUE)
        assert intention.certaine is True
        assert intention.gabarit == attendu

    def test_ignore_les_accents_et_la_casse(self) -> None:
        # « ÉTUDE », « etude », « Étude » désignent la même chose. Un producteur ne
        # tape pas les accents, et l'écran ne doit pas le lui reprocher.
        assert resoudre_demande("UNE ETUDE DU CACAO", CATALOGUE).gabarit == "etude_filiere"

    def test_ne_reconnait_pas_un_declencheur_au_milieu_d_un_mot(self) -> None:
        # « région » ne doit pas se déclencher sur « enregistrement ». Sans frontière
        # de mot, la reconnaissance devient du hasard.
        intention = resoudre_demande("un enregistrement des ventes", CATALOGUE)
        assert intention.certaine is False


class TestExtractionDuSujet:
    """Le sujet est ce qui reste une fois la commande retirée."""

    @pytest.mark.parametrize(
        ("demande", "attendu"),
        [
            ("Fais-moi une étude sur la campagne 2025-2026", "la campagne 2025-2026"),
            ("étude : le marché du cacao", "le marché du cacao"),
        ],
    )
    def test_retire_la_formule_de_commande(self, demande: str, attendu: str) -> None:
        assert resoudre_demande(demande, CATALOGUE).sujet == attendu

    @pytest.mark.parametrize(
        ("demande", "attendu"),
        [
            ("Je veux un bulletin pour la région de Daloa", "Daloa"),
            ("Peux-tu produire une étude de la filière cacao ?", "cacao"),
            ("Prépare le dossier de la parcelle de Kouadio", "Kouadio"),
        ],
    )
    def test_retire_ce_qui_ne_fait_que_renommer_le_type(self, demande: str, attendu: str) -> None:
        # Le titre du document porte déjà le type. « Bulletin régional — la région de
        # Daloa » bégaie ; « Bulletin régional — Daloa » dit la même chose une fois.
        assert resoudre_demande(demande, CATALOGUE).sujet == attendu

    def test_ne_retire_que_le_vocabulaire_du_type_retenu(self) -> None:
        # « région » appartient au bulletin, pas à l'étude : dans une étude, la région
        # d'Abengourou est un vrai sujet et doit le rester.
        assert resoudre_demande("une étude sur la région d'Abengourou", CATALOGUE).sujet == (
            "la région d'Abengourou"
        )

    def test_une_demande_qui_ne_nomme_que_le_type_reste_incertaine(self) -> None:
        # « fais une étude de la filière » ne dit pas de quoi : on demande, on ne devine pas.
        intention = resoudre_demande("fais une étude de la filière", CATALOGUE)
        assert intention.sujet == ""
        assert intention.certaine is False

    def test_conserve_les_accents_du_sujet(self) -> None:
        # La normalisation sert à RECONNAÎTRE, jamais à réécrire : le sujet part tel
        # quel dans le titre du document livré.
        assert resoudre_demande("une étude sur Abengourou", CATALOGUE).sujet == "Abengourou"

    def test_sans_sujet_la_demande_reste_incertaine(self) -> None:
        # « fais-moi une étude » ne dit pas de quoi. On ne devine pas un sujet.
        intention = resoudre_demande("fais-moi une étude", CATALOGUE)
        assert intention.certaine is False
        assert intention.sujet == ""
        assert intention.gabarit == "etude_filiere"

    def test_borne_la_longueur_du_sujet(self) -> None:
        # Le sujet part dans un titre de document et dans un prompt : il est borné à
        # la source, pas seulement par la validation d'entrée.
        intention = resoudre_demande("une étude sur " + "a" * 500, CATALOGUE)
        assert len(intention.sujet) <= 200


class TestAmbiguite:
    """Ce qui n'est pas certain se demande, ne se devine pas."""

    def test_deux_types_a_egalite_restent_incertains(self) -> None:
        intention = resoudre_demande("une étude et un bulletin sur Daloa", CATALOGUE)
        assert intention.certaine is False
        assert set(intention.candidats) == {"etude_filiere", "bulletin_regional"}

    def test_un_declencheur_declare_deux_fois_ne_compte_qu_une_fois(self) -> None:
        # « étude » et « etude » declares ensemble designent le meme mot. Les compter
        # deux fois ferait gagner ce gabarit sur une egalite qui n en est pas une —
        # donc trancher la ou il fallait poser une question.
        catalogue = (
            gabarit("etude_filiere", ("étude", "etude", "analyse")),
            gabarit("bulletin_regional", ("bulletin",)),
        )
        intention = resoudre_demande("une étude et un bulletin sur Daloa", catalogue)
        assert intention.certaine is False
        assert set(intention.candidats) == {"etude_filiere", "bulletin_regional"}

    def test_un_type_dominant_l_emporte_sur_une_mention_isolee(self) -> None:
        # Deux déclencheurs contre un : ce n'est plus une égalité.
        intention = resoudre_demande("une analyse de la filière, façon bulletin", CATALOGUE)
        assert intention.certaine is True
        assert intention.gabarit == "etude_filiere"

    def test_aucun_type_reconnu_propose_tous_les_candidats(self) -> None:
        intention = resoudre_demande("les cours mondiaux du cacao", CATALOGUE)
        assert intention.certaine is False
        assert intention.gabarit == ""
        # L'écran doit pouvoir proposer un choix plutôt qu'un message d'échec.
        assert len(intention.candidats) == 3
        # Le sujet, lui, est bien vu : seul le type manque.
        assert intention.sujet == "les cours mondiaux du cacao"

    def test_catalogue_vide_ne_leve_pas(self) -> None:
        intention = resoudre_demande("une étude", ())
        assert intention == Intention(gabarit="", sujet="", certaine=False, candidats=())


class TestRobustesse:
    """Une demande est une entrée publique."""

    def test_demande_vide(self) -> None:
        assert resoudre_demande("   ", CATALOGUE).certaine is False

    def test_tronque_une_demande_demesuree(self) -> None:
        # Le coût de la résolution ne doit pas dépendre de la taille de l'entrée.
        intention = resoudre_demande("une étude sur " + "x " * 100_000, CATALOGUE)
        assert intention.gabarit == "etude_filiere"
        assert len(intention.sujet) <= 200

    def test_neutralise_les_caracteres_de_controle(self) -> None:
        # Le sujet finit dans un fichier Word et dans un prompt : ni l'un ni l'autre
        # ne doit recevoir un octet de contrôle.
        intention = resoudre_demande("une étude sur\x00 le cacao\x07", CATALOGUE)
        assert "\x00" not in intention.sujet
        assert "\x07" not in intention.sujet
        assert "cacao" in intention.sujet

    def test_l_epluchage_du_type_est_borne(self) -> None:
        # Une demande qui empile les mots de type ne doit pas boucler : le dégagement
        # s'arrête, quitte à laisser un sujet imparfait.
        intention = resoudre_demande("une étude sur étude étude étude étude étude cacao", CATALOGUE)
        assert intention.gabarit == "etude_filiere"
        assert "cacao" in intention.sujet

    def test_le_plafond_est_expose(self) -> None:
        # La couche HTTP borne l'entrée avec la MÊME valeur : elle doit venir d'ici.
        assert DEMANDE_MAX == 2000
