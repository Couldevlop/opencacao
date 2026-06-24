"""Génération automatique du titre de conversation (B3, V2 conversationnelle).

Choix de conception : le titre est dérivé **de façon déterministe** de la première
question du producteur, sans appel supplémentaire au modèle. Sur le nœud CPU/GGUF
(CX53), chaque inférence coûte plusieurs secondes ; titrer via le modèle alourdirait
le premier tour sans bénéfice décisif. Un titre extrait de la question est instantané,
souverain et reproductible — cohérent avec la sobriété du projet (cf. risques R1/R2
de la roadmap V2). Côté infrastructure, le titre est persisté via
``SessionStore.renommer_session``.
"""

from __future__ import annotations

import re

from app.models.session import TITRE_PAR_DEFAUT

LONGUEUR_MAX = 60

# Espaces multiples / retours à la ligne à compacter en une seule espace.
_ESPACES = re.compile(r"\s+")
# Ponctuation et puces de fin à retirer pour un titre propre (« … ? » -> « … »).
_PONCT_FIN = re.compile(r"[\s\.\!\?…,;:«»\"'\-•]+$")
# Formules d'amorce sans valeur de titre, retirées en tête de la question.
_AMORCES = re.compile(
    r"^(?:bonjour|bonsoir|salut|svp|s'?il (?:te|vous) pla[iî]t|"
    r"je (?:voudrais|veux|souhaite) savoir|dis[- ]moi|peux[- ]tu me dire)\b[\s,:-]*",
    re.IGNORECASE,
)


def depuis_question(question: str, longueur_max: int = LONGUEUR_MAX) -> str:
    """Construit un titre lisible à partir de la première question d'une session.

    Compacte les espaces, retire une éventuelle formule d'amorce (« Bonjour, »),
    tronque sur une frontière de mot et nettoie la ponctuation terminale.

    Args:
        question: Première question du producteur dans la conversation.
        longueur_max: Longueur cible du titre avant troncature.

    Returns:
        Un titre court et propre ; ``TITRE_PAR_DEFAUT`` si la question est vide
        une fois nettoyée (jamais de titre vide, contrainte du modèle Session).
    """
    texte = _ESPACES.sub(" ", question).strip()
    texte = _AMORCES.sub("", texte).strip()
    if not texte:
        return TITRE_PAR_DEFAUT

    if len(texte) > longueur_max:
        coupe = texte[:longueur_max]
        espace = coupe.rfind(" ")
        # On coupe au dernier mot entier si l'espace n'est pas trop précoce.
        if espace >= longueur_max // 2:
            coupe = coupe[:espace]
        texte = _PONCT_FIN.sub("", coupe) + "…"
    else:
        texte = _PONCT_FIN.sub("", texte)

    if not texte:
        return TITRE_PAR_DEFAUT
    return texte[0].upper() + texte[1:]
