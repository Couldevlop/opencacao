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


def texte_conversation(question: str, historique: list[dict[str, str]]) -> str:
    """Concatène les messages utilisateur (historique + question) pour repérer une ville."""
    parties = [t.get("content", "") for t in historique if t.get("role") == "user"]
    parties.append(question)
    return " ".join(parties)
