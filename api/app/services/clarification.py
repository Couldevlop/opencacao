"""Couche de clarification : dialogue consultatif piloté par le système.

Au lieu de répondre d'emblée, OpenCacao **analyse** la première question : si une
réponse de qualité dépend d'un contexte manquant (partie atteinte, ancienneté,
ampleur, localité…), le système pose lui-même des **questions complémentaires**
ciblées. La réponse réfléchie n'est produite qu'au tour suivant, une fois le
contexte recueilli.

Déterministe (pas de dépendance au modèle) : le comportement consultatif est donc
fiable, même avec un petit modèle CPU. Une seule salve de clarification par
conversation (dès que l'historique existe, on répond), pour ne jamais boucler.
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


def analyser(question: str, historique: list[dict[str, str]] | None) -> str | None:
    """Retourne des questions complémentaires à poser, ou None pour répondre directement.

    Args:
        question: Question du producteur (1er message).
        historique: Tours précédents. Non vide = on est déjà en dialogue, on répond.

    Returns:
        Le message de clarification (questions posées par le système), ou None.
    """
    if historique:  # déjà en discussion : le contexte a été demandé, on répond
        return None

    texte = _normaliser(question)

    # Demande de contact sans ville : on demande la localité (réponse instantanée,
    # sans modèle), pour donner ensuite le bon contact ANADER.
    if contacts.intention_contact(question) and contacts.chercher(question) is None:
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
    # N'ajoute la question de localité que si la ville n'est pas déjà donnée.
    if contacts.chercher(question) is None:
        bullets.append(_LOCALITE)
    corps = "\n".join(f"• {b}" for b in bullets)
    return f"{_INTRO[theme]}\n{corps}\n{_PIED}"
