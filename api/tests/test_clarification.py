"""Tests de la couche de clarification consultative."""

from __future__ import annotations

from app.services import clarification


def test_symptome_pose_des_questions() -> None:
    """Un symptôme au 1er tour déclenche des questions complémentaires (partie, durée, ville)."""
    msg = clarification.analyser("Mes feuilles de cacaoyer jaunissent", historique=None)
    assert msg is not None
    assert "partie" in msg.lower()
    assert "ville ou région" in msg.lower()


def test_ne_redemande_pas_la_ville_si_donnee() -> None:
    """Si la ville est déjà citée, la question de localité n'est pas reposée."""
    msg = clarification.analyser("Mes feuilles jaunissent, je suis à Daloa", historique=None)
    assert msg is not None
    assert "ville ou région" not in msg.lower()


def test_pas_de_clarification_en_cours_de_dialogue() -> None:
    """Dès qu'un historique existe, on répond (pas de nouvelle salve de questions)."""
    historique = [{"role": "user", "content": "Mes feuilles jaunissent"}]
    assert clarification.analyser("Sur les feuilles, à Daloa", historique) is None


def test_question_factuelle_repond_directement() -> None:
    """Une question claire et factuelle ne déclenche pas de clarification."""
    assert clarification.analyser("Quand récolter les cabosses de cacao ?", historique=None) is None
    assert (
        clarification.analyser("Combien de temps dure la fermentation ?", historique=None) is None
    )


def test_contact_sans_ville_demande_la_localite() -> None:
    """Une demande de contact sans ville déclenche une question de localité."""
    msg = clarification.analyser("Je veux le numéro de l'ANADER", historique=None)
    assert msg is not None
    assert "ville ou région" in msg.lower()


def test_contact_avec_ville_ne_clarifie_pas() -> None:
    """Si la ville est donnée, on ne clarifie pas (on répondra avec le bon contact)."""
    assert clarification.analyser("Le numéro de l'ANADER à Korhogo ?", historique=None) is None


def test_traitement_et_rendement_clarifies() -> None:
    assert (
        clarification.analyser("Comment lutter contre les mirides ?", historique=None) is not None
    )
    assert (
        clarification.analyser("Ma plantation produit peu, pourquoi ?", historique=None) is not None
    )


def test_question_informationnelle_repond_directement() -> None:
    """Prévenir/reconnaître une maladie nommée = question précise -> réponse directe."""
    assert (
        clarification.analyser(
            "Comment prévenir la pourriture brune des cabosses ?", historique=None
        )
        is None
    )
    assert (
        clarification.analyser("Comment reconnaître la pourriture brune ?", historique=None) is None
    )


def test_signature_swollen_shoot_repond_directement() -> None:
    """Feuilles jaunies + rameaux gonflés = swollen shoot reconnaissable -> réponse directe."""
    assert (
        clarification.analyser(
            "Mes feuilles jaunissent et les rameaux gonflent, que faire ?", historique=None
        )
        is None
    )
