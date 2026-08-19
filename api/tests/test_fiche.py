"""Tests de la fiche du producteur (mémoire des faits durables du fil).

La fenêtre glissante de ``memoire.py`` garde 8 messages : une localité citée au 3e
tour a disparu au 12e. La fiche relit TOUT le fil et n'en retient que des faits
explicitement énoncés — jamais une déduction.
"""

from __future__ import annotations

from app.services import fiche


def _fil(*tours: str) -> list[dict[str, str]]:
    """Construit un historique alterné à partir des tours du producteur."""
    historique: list[dict[str, str]] = []
    for tour in tours:
        historique.append({"role": "user", "content": tour})
        historique.append({"role": "assistant", "content": "Entendu."})
    return historique


def test_la_localite_survit_a_un_fil_long() -> None:
    """Une ville citée au tout premier tour reste connue bien plus tard."""
    historique = _fil("Je suis à Soubré", *[f"question {i}" for i in range(10)])
    assert fiche.extraire("Que faire ?", historique).localite == "Soubré"


def test_la_superficie_est_relevee() -> None:
    """« 3 hectares » est un fait durable de la parcelle."""
    assert fiche.extraire("J'ai 3 hectares de cacao", None).superficie_ha == 3.0


def test_la_superficie_accepte_labreviation_et_la_virgule() -> None:
    """« 2,5 ha » s'écrit comme sur le terrain."""
    assert fiche.extraire("ma parcelle fait 2,5 ha", None).superficie_ha == 2.5


def test_lage_de_la_plantation_est_releve() -> None:
    """L'âge conditionne presque tout le conseil : il ne doit pas être redemandé."""
    assert fiche.extraire("Ma plantation a 15 ans", None).age_ans == 15


def test_lage_du_producteur_nest_pas_pris_pour_celui_de_la_plantation() -> None:
    """« J'ai 45 ans » parle de l'homme, pas des arbres — ne rien inventer."""
    assert fiche.extraire("Bonjour, j'ai 45 ans et je cultive le cacao", None).age_ans is None


def test_le_sujet_en_cours_est_releve() -> None:
    """Le thème de la conversation fait partie de ce qu'on doit retenir."""
    assert fiche.extraire("Mes cabosses pourrissent", None).sujet == "symptome"


def test_une_conversation_sans_fait_donne_une_fiche_vide() -> None:
    """Rien n'a été dit : on n'invente rien (c'est le garde-fou anti-fabrication)."""
    resultat = fiche.extraire("Quand récolter les cabosses ?", None)
    assert resultat.vide


def test_une_fiche_vide_ne_produit_aucun_bloc_de_memoire() -> None:
    """Sans fait connu, rien n'est injecté au prompt — pas de bloc vide décoratif."""
    assert fiche.bloc_memoire(fiche.extraire("Quand récolter ?", None)) == ""


def test_le_bloc_de_memoire_cite_les_faits_et_interdit_de_les_redemander() -> None:
    """Le bloc sert à deux choses : accuser réception, et ne pas se répéter."""
    bloc = fiche.bloc_memoire(fiche.extraire("Ma plantation de 3 ha a 15 ans, à Soubré", None))
    assert "Soubré" in bloc
    assert "3" in bloc
    assert "15" in bloc
    assert "redemand" in bloc.lower()


def test_le_rappel_court_resume_le_fil_pour_une_reprise() -> None:
    """Rouvrir une conversation : « Nous parlions de… »."""
    rappel = fiche.rappel_court(fiche.extraire("Mes cabosses pourrissent, je suis à Soubré", None))
    assert "Soubré" in rappel
    assert rappel.strip()


def test_le_rappel_court_est_vide_quand_rien_nest_connu() -> None:
    """Sans fil engagé, on ne prétend pas se souvenir de quelque chose."""
    assert fiche.rappel_court(fiche.extraire("Bonjour", None)) == ""


def test_seuls_les_tours_du_producteur_alimentent_la_fiche() -> None:
    """Une ville citée par l'ASSISTANT n'est pas un fait dit par le producteur.

    Sinon le contact ANADER d'Abengourou, ajouté automatiquement en fin de réponse,
    deviendrait la localité du producteur au tour suivant.
    """
    historique = [
        {"role": "user", "content": "Bonjour"},
        {"role": "assistant", "content": "Contactez la DR ANADER d'Abengourou."},
    ]
    assert fiche.extraire("Merci", historique).localite == ""


def test_un_age_aberrant_nest_pas_retenu_pour_la_plantation() -> None:
    """80 ans ne décrit pas une cacaoyère : mieux vaut ne rien retenir qu'un faux fait."""
    assert fiche.extraire("Mon village a 80 ans", None).age_ans is None


def test_le_bloc_ne_decrit_pas_une_parcelle_dont_on_ne_sait_rien() -> None:
    """Localité seule connue : aucune ligne « Plantation » inventée."""
    bloc = fiche.bloc_memoire(fiche.extraire("Je suis à Soubré", None))
    assert "Soubré" in bloc
    assert "Plantation" not in bloc


def test_le_bloc_tient_avec_le_seul_sujet_connu() -> None:
    """Sujet seul : le bloc existe, sans localité ni parcelle inventées."""
    bloc = fiche.bloc_memoire(fiche.extraire("Mes cabosses pourrissent", None))
    assert "Sujet en cours" in bloc
    assert "Localité" not in bloc


def test_la_surface_seule_decrit_la_parcelle_sans_age() -> None:
    """Surface connue, âge inconnu : on ne comble pas le trou."""
    bloc = fiche.bloc_memoire(fiche.extraire("J'ai 3 ha de cacao", None))
    assert "3 ha" in bloc
    assert "environ" not in bloc  # « environ N ans » est le rendu de l'âge


def test_lage_seul_decrit_la_parcelle_sans_surface() -> None:
    """Âge connu, surface inconnue : symétrique du cas précédent."""
    bloc = fiche.bloc_memoire(fiche.extraire("Ma plantation a 15 ans", None))
    assert "15 ans" in bloc
    assert " ha" not in bloc  # aucune surface inventée


def test_le_rappel_court_se_contente_du_sujet_sans_localite() -> None:
    """Sans ville citée, la reprise ne prétend pas savoir où se trouve le producteur."""
    rappel = fiche.rappel_court(fiche.extraire("Mes cabosses pourrissent", None))
    assert rappel
    assert "à " not in rappel
