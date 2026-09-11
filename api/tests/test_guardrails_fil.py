"""Les règles de CORRECTION ne doivent pas s'accrocher au fil de la conversation.

Incident observé en production le 19/08/2026 (capture de Waopron) :

    — « je veux faire une plantation de cacao à Katiola, que me conseilles-tu ? »
    → « Katiola se situe dans la zone de savane du nord… » (correct)

    — « c'est quoi le FIRCA ? »
    → « Katiola se situe dans la zone de savane du nord… » (LA MÊME RÉPONSE)

    — « c'est quoi le FIRCA ? » (redemandé)
    → réponse correcte sur le FIRCA

Cause : le garde-fou d'entrée est évalué sur ``fil_ancre(question, historique)``,
c'est-à-dire **dernier tour utilisateur + question courante**. La règle « zone non
cacaoyère » exige une localité du Nord ET une intention de culture ; les deux restent
présentes dans le fil au tour suivant, donc elle se redéclenche sur une question qui
n'a plus rien à voir. Au troisième tour, le message contenant « Katiola » sort de la
fenêtre et la réponse redevient correcte — ce qui donne ce comportement en dents de
scie, particulièrement destructeur devant un public.

Le correctif ne peut pas être de supprimer l'ancrage : il protège d'un contournement
réel (« je traite au fongicide » puis « quelle dose ? »). D'où la distinction que ces
tests verrouillent :

* règles de **protection** (dosage, médical) → évaluées sur le FIL, pour qu'une
  intention étalée sur deux tours soit quand même attrapée ;
* règles de **correction d'une prémisse** (zone non cacaoyère, pays hors Côte
  d'Ivoire) → évaluées sur la **question courante** uniquement : elles corrigent ce
  qu'on demande MAINTENANT, et n'ont aucune raison de survivre au changement de sujet.
"""

from __future__ import annotations

from app.application.contexte import fil_ancre, texte_conversation
from app.models.domain import CategorieRefus
from app.services import guardrails

KATIOLA = "je veux faire une plantation de cacao à Katiola, que me conseilles-tu ?"


def test_la_correction_de_zone_ne_survit_pas_au_changement_de_sujet() -> None:
    """L'incident du 19/08 : une question sans rapport recevait la réponse précédente."""
    historique = [{"role": "user", "content": KATIOLA}]
    question = "c'est quoi le FIRCA ?"

    refus = guardrails.evaluer(fil_ancre(question, historique), courante=question)

    assert refus is None


def test_la_correction_de_zone_s_applique_bien_a_la_question_qui_la_porte() -> None:
    """Contre-épreuve : sans elle, un code qui n'évalue plus rien resterait vert."""
    refus = guardrails.evaluer(KATIOLA, courante=KATIOLA)

    assert refus is not None
    assert refus.categorie is CategorieRefus.ZONE_NON_CACAO
    assert "Katiola" in refus.message


def test_la_localite_peut_venir_du_tour_precedent_si_l_intention_est_courante() -> None:
    """« à Katiola » puis « je veux y planter du cacao » doit toujours être corrigé :
    c'est le dialogue normal, et la localité n'a pas à être répétée."""
    historique = [{"role": "user", "content": "je suis à Katiola"}]
    question = "je veux y faire une plantation de cacao"

    refus = guardrails.evaluer(fil_ancre(question, historique), courante=question)

    assert refus is not None
    assert refus.categorie is CategorieRefus.ZONE_NON_CACAO


def test_le_refus_de_dosage_reste_evalue_sur_le_fil() -> None:
    """La protection, elle, DOIT survivre au découpage en deux tours — c'est sa raison
    d'être. Un correctif qui casserait ça rouvrirait un contournement connu."""
    historique = [{"role": "user", "content": "je traite mes cabosses au fongicide"}]
    question = "quelle dose faut-il ?"

    refus = guardrails.evaluer(fil_ancre(question, historique), courante=question)

    assert refus is not None
    assert refus.categorie is CategorieRefus.PHYTOSANITAIRE


def test_la_correction_hors_ci_ne_survit_pas_non_plus() -> None:
    """Même raisonnement pour « cacao au Ghana » : corriger la prémisse d'une question
    passée n'a aucun sens sur la suivante."""
    historique = [{"role": "user", "content": "comment cultive-t-on le cacao au Ghana ?"}]
    question = "c'est quoi le FIRCA ?"

    refus = guardrails.evaluer(fil_ancre(question, historique), courante=question)

    assert refus is None


def test_sans_question_courante_le_comportement_reste_celui_d_avant() -> None:
    """Compatibilité : les appels existants qui ne passent pas ``courante`` doivent
    continuer d'évaluer tout le texte reçu."""
    refus = guardrails.evaluer(KATIOLA)

    assert refus is not None
    assert refus.categorie is CategorieRefus.ZONE_NON_CACAO


def test_la_localite_survit_a_plus_d_un_tour_ecoule() -> None:
    """Écart de production du 11/09/2026, reproduit tel quel.

    « plantation de cacao à Odienné » → refus. « je veux faire une plantation » →
    refus (la ville est encore dans la fenêtre d'ancrage). Même question au tour
    suivant → **conseils de plantation**, la ville ayant quitté la fenêtre : le fil
    ancré ne retient que le DERNIER tour utilisateur. Le producteur obtenait donc,
    pour la même intention, un refus puis un conseil — en zone de savane.
    """
    historique = [
        {"role": "user", "content": "Je veux faire une plantation de cacao à Odienné"},
        {"role": "assistant", "content": "Odienné se situe dans la zone de savane…"},
        {"role": "user", "content": "je veux faire une plantation"},
        {"role": "assistant", "content": "Odienné se situe dans la zone de savane…"},
    ]
    question = "je veux faire une plantation"

    refus = guardrails.evaluer(
        fil_ancre(question, historique),
        courante=question,
        conversation=texte_conversation(question, historique),
    )

    assert refus is not None
    assert refus.categorie is CategorieRefus.ZONE_NON_CACAO
    assert "Odienné" in refus.message


def test_un_changement_de_sujet_ne_ressort_pas_la_correction_de_zone() -> None:
    """Contre-épreuve de l'incident du 19/08 : élargir la mémoire de la LOCALITÉ ne
    doit pas rendre la correction collante. L'INTENTION reste lue dans le tour courant.
    """
    historique = [
        {"role": "user", "content": "Je veux faire une plantation de cacao à Odienné"},
        {"role": "assistant", "content": "Odienné se situe dans la zone de savane…"},
        {"role": "user", "content": "je veux faire une plantation"},
        {"role": "assistant", "content": "Odienné se situe dans la zone de savane…"},
    ]
    question = "quel est le prix officiel du cacao ?"

    refus = guardrails.evaluer(
        fil_ancre(question, historique),
        courante=question,
        conversation=texte_conversation(question, historique),
    )

    assert refus is None


def test_le_producteur_qui_deplace_sa_parcelle_est_suivi() -> None:
    """Une ville plus récente prime : on corrigeait sinon sur un lieu abandonné."""
    historique = [
        {"role": "user", "content": "je suis à Katiola"},
        {"role": "assistant", "content": "Katiola se situe dans la zone de savane…"},
        {"role": "user", "content": "en fait ma parcelle est à Soubré"},
        {"role": "assistant", "content": "Très bien."},
    ]
    question = "je veux y faire une plantation"

    refus = guardrails.evaluer(
        fil_ancre(question, historique),
        courante=question,
        conversation=texte_conversation(question, historique),
    )

    assert refus is None
