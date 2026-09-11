"""Couche de clarification : dialogue consultatif piloté par le système.

Au lieu de répondre d'emblée, OpenCacao **analyse** la première question : si une
réponse de qualité dépend d'un contexte manquant (partie atteinte, ancienneté,
ampleur, localité…), le système pose lui-même des **questions complémentaires**
ciblées. La réponse réfléchie n'est produite qu'au tour suivant, une fois le
contexte recueilli.

Déterministe (pas de dépendance au modèle) : le comportement consultatif est donc
fiable, même avec un petit modèle CPU. Anti-boucle : jamais deux salves de suite —
si la dernière réponse de l'assistant était déjà une clarification, on répond. Un
thème NOUVEAU en cours de conversation redéclenche en revanche le dialogue (décision
Waopron 06/07 : dans le navigateur, la conversation restaurée n'a jamais un
historique vide — la règle « premier tour uniquement » éteignait la clarification
pour toujours). La localité déjà citée dans le fil n'est pas redemandée.
"""

from __future__ import annotations

import unicodedata

from app.services import contacts

_LOCALITE = "Dans quelle ville ou région vous trouvez-vous ?"
_PIED = "Répondez-moi et je vous conseillerai au mieux."


def _normaliser(texte: str) -> str:
    """Minuscule + suppression des accents (détection robuste des thèmes)."""
    sans_accents = "".join(
        c for c in unicodedata.normalize("NFD", texte) if unicodedata.category(c) != "Mn"
    )
    return sans_accents.lower()


# Thèmes nécessitant un contexte avant de répondre, par ordre de priorité.
# (motifs normalisés, sans accents). Choisis pour ne PAS happer une question
# factuelle claire (ex. « quand récolter ») — celle-ci reçoit une réponse directe.
_SYMPTOME = (
    "jauniss",
    "tache",
    "pourri",
    "fane",
    "fleti",
    "gonfl",
    "troue",
    "chenille",
    "insecte",
    "malade",
    "attaque",
    "ravageur",
    "champignon",
    "moisi",
    "rouille",
    "noirci",
    "fleche",
    "cabosse noire",
    "deper",
    "deperiss",
    "symptome",
    "nuisible",
    # Chute d'organes : un symptôme classique. Motifs volontairement ÉTROITS — un
    # simple « tombe » happerait « le prix tombe », et la conversation se mettrait à
    # demander quelle partie de l'arbre est touchée à propos d'un cours du cacao.
    "feuilles tomb",
    "cabosses tomb",
    "fleurs tomb",
    "chute des feuilles",
    "chute des cabosses",
    "defoliation",
)
_TRAITEMENT = (
    "traiter",
    "traitement contre",
    "lutter",
    "lutte contre",
    "combattre",
    "soigner",
    "eliminer",
    "detruire",
    "se debarrasser",
    "contre les",
    "comment tuer",
)
_RENDEMENT = (
    "rendement",
    "produit peu",
    "peu de cabosses",
    "ne produit pas",
    "production faible",
    "faible production",
    "baisse de production",
    "plus de cabosses",
    "ameliorer ma production",
    "augmenter ma production",
    "augmenter le rendement",
)
_FERTILISATION = ("fertilis", "fumure", "engrais", "amender", "sol pauvre", "nutrition de")
_PLANTATION = (
    "creer une plantation",
    "creer ma plantation",
    "nouvelle plantation",
    "jeune plantation",
    "installer une plantation",
    "mettre en place une plantation",
    "ecartement",
    "densite de plantation",
    "planter",
    "semer",
    "pepiniere",
    "creer un champ",
    "demarrer un champ",
    "champ de cacao",
    # Écart de production du 11/09 : « je veux faire une plantation » et « je veux
    # faire de la culture de cacao » recevaient un conseil immédiat. Aucun motif ne
    # correspondait — la liste ne connaissait que « créer/installer une plantation ».
    "faire une plantation",
    "faire de la plantation",
    "faire une exploitation",
    "cultiver",
    "culture de cacao",
    "culture du cacao",
    "faire de la culture",
    "me lancer dans le cacao",
    "commencer le cacao",
    "demarrer une plantation",
)

_BULLETS = {
    "symptome": [
        "Sur quelle partie l'observez-vous ? (feuilles, cabosses, tronc/rameaux, racines)",
        "Depuis combien de temps, et est-ce que cela s'étend ?",
    ],
    "traitement": [
        "Quel problème précis voulez-vous traiter (maladie, insecte, mauvaises herbes) ?",
        "Sur quelle partie, et quelle ampleur (quelques arbres ou toute la parcelle) ?",
    ],
    "rendement": [
        "Quel âge a votre plantation ?",
        "Avez-vous fait récemment la taille, le désherbage et l'égourmandage ?",
        "Observez-vous des maladies ou des ravageurs ?",
    ],
    "fertilisation": [
        "Quel âge a la plantation, et a-t-elle déjà été fertilisée ?",
        "Connaissez-vous le type de sol (sableux, argileux, latéritique) ?",
    ],
    "plantation": [
        "Confirmez-moi qu'il s'agit bien d'une plantation de CACAO : c'est la seule "
        "culture sur laquelle je peux vous accompagner.",
        "Quelle surface envisagez-vous, et quel type de sol ?",
    ],
}

_INTRO = {
    "symptome": "Pour bien analyser le problème, j'ai besoin de quelques précisions :",
    "traitement": "Avant de vous orienter, dites-moi :",
    "rendement": "Pour comprendre la baisse de rendement :",
    "fertilisation": "Pour vous conseiller sur la fertilité de votre sol :",
    "plantation": "Avec plaisir. Avant de vous orienter, deux précisions :",
}


# Tournures INFORMATIONNELLES (prévention, reconnaissance, définition) : la question
# est déjà précise, on répond directement au lieu de réclamer un contexte de
# diagnostic. (Distinct d'une intention de traitement « lutter contre / traiter »,
# qui, elle, mérite des précisions sur l'ampleur et la partie atteinte.)
_INFORMATIONNEL = (
    "prevenir",
    "prevention",
    "reconnaitre",
    "reconnait",
    "identifier",
    "se manifeste",
    "se transmet",
    "c'est quoi",
    "qu'est-ce",
    "definition",
)


def _repondre_directement(texte: str) -> bool:
    """Vrai si la question est assez précise pour répondre sans clarification.

    Couvre les questions informationnelles (prévenir/reconnaître/définir une maladie
    nommée) et la signature non équivoque du swollen shoot (rameaux/tiges gonflés,
    souvent avec jaunissement) — inutile alors de redemander la partie atteinte.
    """
    if any(m in texte for m in _INFORMATIONNEL):
        return True
    # Swollen shoot : rameaux/tiges gonflés (souvent avec jaunissement).
    return "gonfl" in texte and any(p in texte for p in ("rameau", "tige", "jauniss", "pousse"))


def _detecter(texte: str) -> str | None:
    """Retourne le thème nécessitant une clarification, ou None (réponse directe)."""
    for theme, motifs in (
        ("symptome", _SYMPTOME),
        ("traitement", _TRAITEMENT),
        ("rendement", _RENDEMENT),
        ("fertilisation", _FERTILISATION),
        ("plantation", _PLANTATION),
    ):
        if any(m in texte for m in motifs):
            return theme
    return None


def _salves_consecutives(historique: list[dict[str, str]]) -> int:
    """Nombre de clarifications posées D'AFFILÉE, en remontant depuis la fin du fil.

    On compte les salves CONSÉCUTIVES et non le total : dès qu'un vrai conseil a été
    donné, le compteur repart de zéro, de sorte qu'un thème nouveau rouvre un dialogue
    en cours de conversation (décision Waopron 06/07). C'est ce qui permet d'autoriser
    cinq échanges sur GPU sans qu'une conversation longue finisse par ne plus jamais
    poser de question.

    Args:
        historique: Tours précédents, du plus ancien au plus récent.

    Returns:
        Le nombre de questions de clarification qui terminent le fil.
    """
    salves = 0
    for tour in reversed(historique):
        if tour.get("role") != "assistant":
            continue
        texte = tour.get("content", "")
        if _PIED in texte or "dites-moi dans quelle ville ou région" in texte:
            salves += 1
            continue
        break
    return salves


def _derniere_reponse_est_clarification(historique: list[dict[str, str]]) -> bool:
    """Vrai si la dernière réponse de l'assistant était une salve de clarification.

    Reconnue par ses marqueurs déterministes (pied commun des salves à thème, ou
    question de localité du parcours contact). C'est l'anti-boucle : le producteur
    qui vient de recevoir des questions obtient une réponse, jamais une re-salve.
    """
    for tour in reversed(historique):
        if tour.get("role") == "assistant":
            texte = tour.get("content", "")
            return _PIED in texte or "dites-moi dans quelle ville ou région" in texte
    return False


def _fil_utilisateur(question: str, historique: list[dict[str, str]]) -> str:
    """Concatène les tours utilisateur et la question (la ville citée plus tôt compte)."""
    tours = [t.get("content", "") for t in historique if t.get("role") == "user"]
    return " ".join([*tours, question])


_CONSIGNES: dict[str, str] = {
    "contact": (
        "Le producteur cherche un contact de l'ANADER mais n'a pas indiqué sa localité. "
        "Demande-lui simplement et chaleureusement dans quelle ville ou région il se "
        "trouve, sans rien affirmer d'autre et sans donner de numéro."
    ),
    "symptome": (
        "Il te manque, pour bien l'aider, la partie atteinte (feuilles, cabosses, "
        "tronc/rameaux, racines) et depuis combien de temps cela dure. Pose UNE question "
        "brève, naturelle et bienveillante pour l'obtenir, sans donner de conseil encore."
    ),
    "traitement": (
        "Avant d'orienter, il te faut savoir quel problème précis traiter (maladie, "
        "insecte, mauvaises herbes), sur quelle partie et quelle ampleur. Pose UNE "
        "question brève et naturelle pour le préciser, sans conseiller encore."
    ),
    "rendement": (
        "Pour comprendre la baisse de rendement, il te faut l'âge de la plantation, les "
        "entretiens récents (taille, désherbage, égourmandage) et la présence éventuelle "
        "de maladies. Pose UNE question brève et naturelle en ce sens, sans conclure encore."
    ),
    "fertilisation": (
        "Pour conseiller sur la fertilité, il te faut l'âge de la plantation, si elle a "
        "déjà été fertilisée et le type de sol. Pose UNE question brève et naturelle "
        "pour le savoir, sans donner de recommandation encore."
    ),
    # Arbitrage Waopron du 11/09/2026. Une intention de plantation est le seul cas où
    # l'on CONFIRME le périmètre avant de questionner : « plantation » tout court ne dit
    # pas quelle culture, et répondre cacao d'office reviendrait à supposer. On demande
    # donc les deux choses d'un coup — est-ce bien du cacao, et où — puis on annonce
    # franchement la limite si ce n'en est pas.
    "plantation": (
        "Le producteur veut créer une plantation mais n'a pas dit de quelle culture ni "
        "où. Demande-lui, en UNE phrase chaleureuse, de confirmer qu'il s'agit bien de "
        "CACAO et dans quelle ville ou zone il compte planter. Précise, en quelques mots, "
        "que tu ne peux l'accompagner que sur le cacao si c'est une autre culture. Ne "
        "donne aucun conseil de plantation à ce stade."
    ),
}


def detecter_theme(
    question: str,
    historique: list[dict[str, str]] | None,
    profondeur_max: int = 1,
    sujet_en_cours: str = "",
    faits_manquants: bool = False,
) -> str | None:
    """Retourne le thème nécessitant une clarification, ou None (réponse directe).

    Même logique de déclenchement que :func:`analyser` (anti-boucle, contact sans
    ville, question informationnelle, détection de thème), mais renvoie le THÈME plutôt
    que le texte scripté — pour que l'appelant fasse formuler la question par le modèle.

    Args:
        question: Dernière question du producteur.
        historique: Tours précédents de la conversation.
        profondeur_max: Nombre de questions de clarification tolérées d'affilée. **1 sur
            CPU** : chaque tour y coûte des dizaines de secondes, et enchaîner les
            questions ferait attendre le producteur sans rien lui apprendre. **Jusqu'à 5
            sur GPU**, où un tour coûte une à deux secondes et où le dialogue consultatif
            prend tout son sens. Le plafond est indispensable : sans lui, un producteur
            pourrait ne jamais obtenir de conseil. Ce qui empêche de tourner en rond,
            c'est la fiche — on ne redemande pas ce qui a été dit (cf. ``fiche.py``).
        sujet_en_cours: Thème déjà engagé dans la conversation, lu dans la fiche du
            producteur. Sert à POURSUIVRE un dialogue quand le tour courant ne renomme
            pas le thème.
        faits_manquants: Vrai s'il reste, pour ce sujet, des faits que la fiche sait
            lire et que le producteur n'a pas encore donnés.

    Returns:
        Le thème, ou ``None`` s'il faut répondre.
    """
    historique = historique or []
    if _salves_consecutives(historique) >= max(1, profondeur_max):
        return None
    fil = _fil_utilisateur(question, historique)
    if contacts.intention_contact(question) and contacts.chercher(fil) is None:
        return "contact"
    texte = _normaliser(question)
    if _repondre_directement(texte):
        return None
    theme = _detecter(texte)
    if theme is not None:
        # Le producteur nomme un thème : il prime, même en plein dialogue. Il a le
        # droit de changer de sujet, et le suivre vaut mieux que finir un questionnaire.
        return theme

    # Aucun thème dans le tour courant. S'il RÉPOND à une question qu'on vient de lui
    # poser, c'est le même sujet : « depuis deux semaines » n'est pas une question
    # neuve sans objet. Sans cette poursuite, le dialogue s'arrêtait au deuxième tour,
    # quelle que soit la profondeur autorisée — la capacité était livrée, pas l'usage.
    if _salves_consecutives(historique) >= 1 and sujet_en_cours and faits_manquants:
        return sujet_en_cours
    return None


def besoin_localite(question: str, historique: list[dict[str, str]] | None) -> bool:
    """Vrai si aucune ville connue n'apparaît dans le fil (localité à demander)."""
    fil = _fil_utilisateur(question, historique or [])
    return contacts.chercher(fil) is None


def theme_du_texte(texte: str) -> str | None:
    """Thème abordé par un texte, SANS la logique de déclenchement du dialogue.

    :func:`detecter_theme` répond « faut-il clarifier ? » et se tait donc dès que le
    dialogue est déjà engagé (anti-boucle) ou que la question est assez précise. La
    fiche du producteur, elle, veut savoir « de quoi parle-t-on ? » à tout moment du
    fil. Les deux s'appuient sur la même table de thèmes, seule source de vérité.

    Args:
        texte: Texte libre (typiquement tous les tours du producteur).

    Returns:
        Le thème (``symptome``, ``traitement``…), ou ``None`` si aucun ne ressort.
    """
    return _detecter(_normaliser(texte))


def consigne_theme(theme: str, besoin_localite: bool, deja_connu: str = "") -> str:
    """Consigne au modèle pour formuler la question de clarification du thème.

    Args:
        theme: Thème renvoyé par :func:`detecter_theme`.
        besoin_localite: Si vrai (et thème != contact), on demande aussi la ville.
        deja_connu: Faits déjà énoncés par le producteur (cf. ``fiche.faits_connus``).
            Vide → consigne strictement identique à celle d'avant.
    """
    consigne = _CONSIGNES[theme]
    if deja_connu:
        consigne = (
            f"Le producteur vous a DÉJÀ dit ceci : {deja_connu}. Ne le lui redemandez "
            f"sous aucun prétexte ; portez votre question sur ce qui manque encore. "
            f"{consigne}"
        )
    if besoin_localite and theme != "contact":
        consigne += (
            " Demande aussi, dans la même phrase et naturellement, dans quelle localité "
            "il se trouve."
        )
    # Latence : le modèle rédige sinon 50-70 tokens et la question est tronquée par le
    # plafond CPU. On exige une formulation TRÈS courte, complète en une phrase.
    consigne += (
        " Réponds par UNE SEULE question, en une phrase courte (20 mots maximum), "
        "sans préambule ni politesse d'introduction."
    )
    return consigne


def analyser(question: str, historique: list[dict[str, str]] | None) -> str | None:
    """Retourne des questions complémentaires à poser, ou None pour répondre directement.

    Args:
        question: Dernière question du producteur.
        historique: Tours précédents. Si la dernière réponse de l'assistant était une
            clarification, on répond (anti-boucle) ; sinon un thème nouveau peut
            déclencher une salve, même en cours de conversation.

    Returns:
        Le message de clarification (questions posées par le système), ou None.
    """
    historique = historique or []
    if _derniere_reponse_est_clarification(historique):
        return None  # le contexte vient d'être demandé : on répond

    texte = _normaliser(question)
    fil = _fil_utilisateur(question, historique)

    # Demande de contact sans ville NULLE PART dans le fil : on demande la localité
    # (réponse instantanée, sans modèle), pour donner ensuite le bon contact ANADER.
    if contacts.intention_contact(question) and contacts.chercher(fil) is None:
        return (
            "Avec plaisir. Pour vous donner le contact de l'ANADER de votre zone, "
            "dites-moi dans quelle ville ou région vous vous trouvez."
        )

    # Question informationnelle (prévenir/reconnaître/définir) ou signature claire
    # (swollen shoot) : on répond directement, sans salve de clarification.
    if _repondre_directement(texte):
        return None

    theme = _detecter(texte)
    if theme is None:
        return None  # question claire/factuelle -> réponse directe

    bullets = list(_BULLETS[theme])
    # N'ajoute la question de localité que si la ville n'est donnée nulle part dans
    # le fil. Pour une plantation à créer, la zone conditionne variétés et
    # calendrier : la localité passe en tête.
    if contacts.chercher(fil) is None:
        if theme == "plantation":
            bullets.insert(0, _LOCALITE)
        else:
            bullets.append(_LOCALITE)
    corps = "\n".join(f"• {b}" for b in bullets)
    return f"{_INTRO[theme]}\n{corps}\n{_PIED}"
