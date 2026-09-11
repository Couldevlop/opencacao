"""Contexte conversationnel partagé : ancrage multi-tours (anti-dérive).

Mutualise la logique d'ancrage entre le service de conseil V2 (``conseil_service``)
et l'orchestrateur agentique V3 (``orchestrateur``). Déterministe (aucun appel au
modèle) : on contextualise une question de suivi par le dernier tour utilisateur.
"""

from __future__ import annotations


def fil_ancre(question: str, historique: list[dict[str, str]]) -> str:
    """Ancre la question sur le dernier tour utilisateur (anti-dérive multi-tours).

    Une question de suivi (« Et à quelle fréquence ? », « quelle dose ? ») est, seule,
    dépourvue d'ancrage : elle perd le sujet engagé au tour précédent. On préfixe donc
    le dernier tour utilisateur de l'historique. Déterministe (aucun appel au modèle) ;
    en tour unique, retourne la question inchangée.

    Sert à deux usages :
      - la **requête RAG**, pour ne pas récupérer de passages hors sujet ;
      - le **garde-fou d'entrée**, pour qu'un refus (ex. demande de dosage) ne soit pas
        contourné en étalant l'intention sur deux tours (« je traite au fongicide » puis
        « quelle dose ? »).

    Args:
        question: Dernière question du producteur.
        historique: Tours précédents de la conversation.

    Returns:
        La question, contextualisée si un tour utilisateur précède.
    """
    dernier_user = next(
        (t.get("content", "") for t in reversed(historique) if t.get("role") == "user"),
        "",
    )
    return f"{dernier_user} {question}".strip() if dernier_user else question


# Au-delà, ce n'est plus une réponse de dialogue mais une demande à part entière.
# Six mots couvrent « sur toute la parcelle », « depuis environ deux semaines », « un
# peu partout sur la parcelle ».
_MOTS_MAX_REPONSE = 6


def fil_ancre_sujet(
    question: str,
    historique: list[dict[str, str]],
    connue: object | None,
) -> str:
    """Ancre la question sur le SUJET engagé quand elle n'en porte aucun elle-même.

    ``fil_ancre`` ne retient que le dernier tour utilisateur. Dans un dialogue, une
    réponse courte — « sur toute la parcelle » — s'y retrouve seule avec la réponse
    précédente, sans rien qui rappelle de quoi on parle. Vécu en production le
    11/09/2026 : la requête documentaire valait « depuis deux semaines sur toute la
    parcelle », et le mot « parcelle » ramenait des passages sur le choix du terrain.
    Le producteur décrivait un jaunissement des feuilles et recevait des conseils
    d'implantation.

    L'enrichissement est **étroit et court**, pour deux raisons opposées :

    * **étroit** — il n'a lieu que si le tour courant est une RÉPONSE de dialogue, et
      non une demande à part entière. Une question neuve (« quel est le prix du
      cacao ? ») doit être prise pour ce qu'elle est ; la ramener de force au sujet
      précédent serait le défaut symétrique, déjà corrigé le 19/08 sur les garde-fous
      de zone ;
    * **court** — allonger une requête éteint le canal lexical de la recherche
      hybride (mesuré en juillet : 0,75 -> 0,27, et 93 documents ramenés -> 0). On
      ajoute quelques mots, pas une phrase.

    Args:
        question: Dernière question du producteur.
        historique: Tours précédents de la conversation.
        connue: Fiche du producteur (``services.fiche.Fiche``), ou ``None``. Le défaut
            est de ne rien ajouter : un chemin qui n'a pas construit de fiche garde le
            comportement historique.

    Returns:
        Le fil ancré, éventuellement préfixé du sujet engagé.
    """
    ancre = fil_ancre(question, historique)
    sujet = getattr(connue, "sujet", "") or ""
    partie = getattr(connue, "partie", "") or ""
    if not sujet:
        return ancre

    # Le tour courant est-il une RÉPONSE, ou une question neuve ? Le critère est la
    # forme, pas le vocabulaire : un premier essai testait « ce tour nomme-t-il un
    # thème de clarification ? », et « quel est le prix du cacao ? » n'en nomme aucun —
    # il se serait donc fait rattacher au jaunissement des feuilles, ce qui est
    # exactement le défaut symétrique de celui qu'on corrige.
    #
    # Une réponse de dialogue est courte et n'interroge pas : « sur toute la parcelle »,
    # « depuis deux semaines », « à Soubré ». Une question neuve porte une marque
    # interrogative, ou déborde de quelques mots.
    if "?" in question or len(question.split()) > _MOTS_MAX_REPONSE:
        return ancre

    rappel = f"{sujet} {partie}".strip()
    return f"{rappel} : {ancre}" if rappel else ancre


def texte_conversation(question: str, historique: list[dict[str, str]]) -> str:
    """Concatène les messages utilisateur (historique + question) pour repérer une ville."""
    parties = [t.get("content", "") for t in historique if t.get("role") == "user"]
    parties.append(question)
    return " ".join(parties)
