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
        "Quelle surface envisagez-vous, et quel type de sol ?",
        "Avez-vous déjà des plants ou semences sélectionnés ?",
    ],
}

_INTRO = {
    "symptome": "Pour bien analyser le problème, j'ai besoin de quelques précisions :",
    "traitement": "Avant de vous orienter, dites-moi :",
    "rendement": "Pour comprendre la baisse de rendement :",
    "fertilisation": "Pour vous conseiller sur la fertilité de votre sol :",
    "plantation": "Pour bien démarrer votre plantation :",
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
    "plantation": (
        "Pour bien accompagner une nouvelle plantation, il te faut la surface envisagée, "
        "le type de sol et si le producteur a déjà des plants sélectionnés. Pose UNE "
        "question brève et naturelle en ce sens, sans conseiller encore."
    ),
}


def detecter_theme(question: str, historique: list[dict[str, str]] | None) -> str | None:
    """Retourne le thème nécessitant une clarification, ou None (réponse directe).

    Même logique de déclenchement que :func:`analyser` (anti-boucle, contact sans
    ville, question informationnelle, détection de thème), mais renvoie le THÈME plutôt
    que le texte scripté — pour que l'appelant fasse formuler la question par le modèle.
    """
    historique = historique or []
    if _derniere_reponse_est_clarification(historique):
        return None
    fil = _fil_utilisateur(question, historique)
    if contacts.intention_contact(question) and contacts.chercher(fil) is None:
        return "contact"
    texte = _normaliser(question)
    if _repondre_directement(texte):
        return None
    return _detecter(texte)


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


def consigne_theme(theme: str, besoin_localite: bool) -> str:
    """Consigne au modèle pour formuler la question de clarification du thème.

    Args:
        theme: Thème renvoyé par :func:`detecter_theme`.
        besoin_localite: Si vrai (et thème != contact), on demande aussi la ville.
    """
    consigne = _CONSIGNES[theme]
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
