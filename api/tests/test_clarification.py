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
    """La réponse à une salve de clarification reçoit une réponse (pas de re-salve)."""
    historique = [{"role": "user", "content": "Mes feuilles jaunissent"}]
    assert clarification.analyser("Sur les feuilles, à Daloa", historique) is None


def _echange(question: str, reponse: str) -> list[dict[str, str]]:
    return [{"role": "user", "content": question}, {"role": "assistant", "content": reponse}]


def test_nouveau_sujet_en_cours_de_conversation_declenche() -> None:
    """Un thème NOUVEAU en pleine conversation redéclenche le dialogue consultatif.

    Vécu navigateur (05/07) : la conversation restaurée n'a jamais un historique vide,
    la règle « premier tour uniquement » éteignait la clarification pour toujours.
    """
    historique = _echange("Quel est le prix du cacao ?", "Le prix bord-champ est 1200 FCFA/kg.")
    msg = clarification.analyser("Les feuilles de mes cacaoyers jaunissent", historique)
    assert msg is not None
    assert "partie" in msg.lower()


def test_pas_de_re_salve_apres_une_clarification() -> None:
    """Anti-boucle : si la dernière réponse de l'assistant était une clarification, on répond."""
    historique = _echange(
        "Je veux planter des cacaoyers",
        "Pour bien démarrer votre plantation :\n• Dans quelle ville ou région vous trouvez-vous ?"
        "\nRépondez-moi et je vous conseillerai au mieux.",
    )
    assert clarification.analyser("Je veux planter des cacaoyers bio", historique) is None


def test_ville_donnee_plus_tot_dans_la_conversation_pas_redemandee() -> None:
    """La localité citée plus tôt dans le fil n'est pas redemandée par une salve ultérieure."""
    historique = _echange("Quel temps à Daloa cette semaine ?", "Pluie modérée attendue à Daloa.")
    msg = clarification.analyser("Les feuilles de mes cacaoyers jaunissent", historique)
    assert msg is not None
    assert "ville ou région" not in msg.lower()


def test_contact_en_cours_de_conversation_avec_ville_connue() -> None:
    """Une demande de contact en cours de dialogue réutilise la ville déjà donnée."""
    historique = _echange("Quel temps à Korhogo ?", "Korhogo est en zone de savane.")
    assert clarification.analyser("Je veux le numéro de l'ANADER", historique) is None


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


def test_planter_des_cacaoyers_declenche_le_dialogue() -> None:
    """« Je veux planter des cacaoyers » déclenche le dialogue consultatif (localité incluse)."""
    msg = clarification.analyser("Je veux planter des cacaoyers", historique=None)
    assert msg is not None
    assert "ville ou région" in msg.lower()


def test_formulations_naturelles_de_plantation_clarifiees() -> None:
    """Les formulations courantes des producteurs déclenchent le thème plantation."""
    for question in (
        "Comment semer le cacao ?",
        "Je veux créer un champ de cacao",
        "Où installer ma pépinière de cacaoyers ?",
        "Je vais démarrer un champ de cacao",
    ):
        assert clarification.analyser(question, historique=None) is not None, question


def test_plantation_demande_la_localite_en_premier() -> None:
    """Pour le thème plantation, la localité est la première question posée."""
    msg = clarification.analyser("Je veux planter des cacaoyers", historique=None)
    assert msg is not None
    puces = [ligne for ligne in msg.splitlines() if ligne.startswith("•")]
    assert "ville ou région" in puces[0].lower()


def test_plantation_avec_ville_ne_redemande_pas_la_localite() -> None:
    """Ville déjà donnée : les questions surface/sol restent, sans redemander la localité."""
    msg = clarification.analyser("Je veux planter des cacaoyers à Daloa", historique=None)
    assert msg is not None
    assert "ville ou région" not in msg.lower()
    assert "surface" in msg.lower()


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


def test_detecter_theme_symptome() -> None:
    assert clarification.detecter_theme("Mes feuilles jaunissent", None) == "symptome"


def test_detecter_theme_contact_sans_ville() -> None:
    assert clarification.detecter_theme("Je veux le numéro de l'ANADER", None) == "contact"


def test_detecter_theme_question_factuelle_est_none() -> None:
    assert clarification.detecter_theme("Quand récolter les cabosses ?", None) is None


def test_detecter_theme_anti_boucle() -> None:
    historique = [
        {"role": "user", "content": "Mes feuilles jaunissent"},
        {"role": "assistant", "content": "Répondez-moi et je vous conseillerai au mieux."},
        {"role": "user", "content": "Sur les feuilles"},
    ]
    assert clarification.detecter_theme("Sur les feuilles", historique) is None


def test_consigne_theme_ajoute_la_localite_si_besoin() -> None:
    sans = clarification.consigne_theme("symptome", besoin_localite=False)
    avec = clarification.consigne_theme("symptome", besoin_localite=True)
    assert "localit" in avec.lower()
    assert "localit" not in sans.lower()


def test_besoin_localite_vrai_si_aucune_ville() -> None:
    assert clarification.besoin_localite("Mes feuilles jaunissent", None) is True
    assert clarification.besoin_localite("Mes feuilles jaunissent à Daloa", None) is False


# --- « Je veux faire une plantation » : confirmer le cacao, puis demander la zone ---
#
# Arbitrage Waopron du 11/09/2026. Une intention de plantation sans autre précision ne
# doit pas recevoir un conseil immédiat : le système confirme d'abord qu'il s'agit bien
# de cacao — c'est son seul périmètre — puis demande la zone, qui commande les variétés
# et le calendrier. Si ce n'est pas du cacao, il le dit franchement.


def test_une_intention_de_plantation_declenche_le_dialogue() -> None:
    """« je veux faire une plantation » ne correspondait à aucun motif : le système
    répondait directement, en zone de savane comme ailleurs."""
    assert clarification.detecter_theme("je veux faire une plantation", []) == "plantation"


def test_l_intention_de_cultiver_declenche_aussi() -> None:
    """Écart de production : « je veux faire de la culture de cacao »."""
    assert clarification.detecter_theme("je veux faire de la culture de cacao", []) == "plantation"


def test_la_consigne_de_plantation_fait_confirmer_le_cacao() -> None:
    """Le producteur doit pouvoir dire « non, c'est de l'hévéa » et être redirigé."""
    consigne = clarification.consigne_theme("plantation", besoin_localite=True)
    assert "cacao" in consigne.lower()
    assert "zone" in consigne.lower() or "localit" in consigne.lower()


def test_la_zone_n_est_pas_redemandee_si_elle_est_connue() -> None:
    """Redemander ce qui vient d'être dit est le défaut qu'on corrige, pas qu'on ajoute."""
    consigne = clarification.consigne_theme("plantation", besoin_localite=False)
    assert "dans quelle localité" not in consigne.lower()


# --- Profondeur du dialogue : courte sur CPU, jusqu'à cinq échanges sur GPU ---
#
# Arbitrage Waopron du 11/09/2026. Sur CPU chaque tour coûte des dizaines de secondes :
# une seule question de clarification, puis on répond. Sur GPU un tour coûte une à deux
# secondes, et un vrai dialogue consultatif devient possible — sans jamais boucler, ce
# que garantit la fiche (on ne redemande pas ce qui a été dit) et le plafond.


def _fil_clarifie(nb: int) -> list[dict[str, str]]:
    """Historique où l'assistant a déjà posé ``nb`` questions de clarification."""
    historique: list[dict[str, str]] = []
    for i in range(nb):
        historique.append({"role": "user", "content": f"mes feuilles jaunissent {i}"})
        historique.append(
            {"role": "assistant", "content": f"Sur quelle partie ? {clarification._PIED}"}
        )
    return historique


def test_sur_cpu_une_seule_salve_puis_on_repond() -> None:
    """Comportement historique, inchangé : le producteur obtient une réponse."""
    assert (
        clarification.detecter_theme("elles jaunissent aussi", _fil_clarifie(1), profondeur_max=1)
        is None
    )


def test_sur_gpu_le_dialogue_peut_aller_jusqu_a_cinq() -> None:
    """Quatre questions déjà posées : une cinquième reste permise."""
    assert (
        clarification.detecter_theme(
            "mes cabosses pourrissent aussi", _fil_clarifie(4), profondeur_max=5
        )
        == "symptome"
    )


def test_le_plafond_est_respecte_meme_sur_gpu() -> None:
    """Cinq questions posées : on répond, on ne questionne plus. Sans ce plafond, le
    producteur n'obtiendrait jamais de conseil."""
    assert (
        clarification.detecter_theme(
            "mes cabosses pourrissent aussi", _fil_clarifie(5), profondeur_max=5
        )
        is None
    )


def test_une_vraie_reponse_remet_le_compteur_a_zero() -> None:
    """Un thème nouveau après un conseil rouvre un dialogue : ce sont les salves
    CONSÉCUTIVES qui comptent, pas le total d'une conversation entière."""
    historique = [
        *_fil_clarifie(3),
        {"role": "user", "content": "merci"},
        {"role": "assistant", "content": "Taillez votre cacaoyer après la récolte."},
    ]
    assert (
        clarification.detecter_theme("mes cabosses pourrissent", historique, profondeur_max=1)
        == "symptome"
    )
