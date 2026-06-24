"""Mémoire conversationnelle : fenêtre glissante + résumé des tours anciens (B2, V2).

Reconstituer l'historique complet à chaque tour gonfle le contexte transmis au
modèle : sur le nœud CPU/GGUF (CX53), la latence croît avec la taille du prompt
(risque R1 de la roadmap V2). On borne donc le contexte par une **fenêtre glissante**
des messages récents, précédée d'un **résumé** condensé des échanges plus anciens.

Le résumé est **extractif et déterministe** (aucune inférence supplémentaire, donc
zéro latence et pleine reproductibilité — cohérent avec la sobriété du projet) : il
liste les questions déjà posées et le dernier conseil donné, de sorte que le modèle
conserve le fil sans relire tout l'échange. Il est injecté comme un message
``assistant`` de tête, devant la fenêtre des tours récents.
"""

from __future__ import annotations

# Au-delà de ce nombre de messages, on résume les plus anciens plutôt que de tout
# réinjecter. En deçà, l'historique tient et passe intégralement (comportement V1).
SEUIL_RESUME = 16
# Nombre de messages récents conservés mot pour mot dans la fenêtre glissante.
FENETRE_MESSAGES = 8
# Garde-fous de longueur du résumé (titres courts, prompt borné).
MAX_POINTS = 6
LONGUEUR_POINT = 160

Message = dict[str, str]


def fenetre_dialogue(
    historique: list[Message],
    fenetre: int = FENETRE_MESSAGES,
    seuil: int = SEUIL_RESUME,
) -> list[Message]:
    """Borne l'historique : résumé des tours anciens + fenêtre des tours récents.

    Args:
        historique: Messages de la session, du plus ancien au plus récent
            (``[{"role": "user"|"assistant", "content": ...}]``).
        fenetre: Nombre de messages récents conservés intégralement.
        seuil: Taille d'historique en deçà de laquelle rien n'est résumé.

    Returns:
        Soit l'historique inchangé (s'il tient sous le seuil), soit un résumé
        ``assistant`` suivi de la fenêtre des messages récents.
    """
    if len(historique) <= seuil:
        return list(historique)

    debut = len(historique) - fenetre
    # Caler le début de fenêtre sur un message utilisateur pour garder des tours
    # complets (un échange = une question puis une réponse).
    while debut < len(historique) and historique[debut].get("role") != "user":
        debut += 1
    anciens, recents = historique[:debut], historique[debut:]

    resume = _resumer(anciens)
    if not resume:
        return list(recents)
    return [{"role": "assistant", "content": resume}, *recents]


def _resumer(messages: list[Message]) -> str:
    """Construit un résumé extractif des messages anciens (déterministe).

    Reprend les dernières questions posées (le fil thématique) et le dernier conseil
    formulé, afin que le modèle garde la mémoire du sujet sans relire tout l'échange.
    """
    if not messages:
        return ""
    questions = [m.get("content", "") for m in messages if m.get("role") == "user"]
    dernier_conseil = next(
        (m.get("content", "") for m in reversed(messages) if m.get("role") == "assistant"),
        "",
    )
    lignes = ["Résumé de nos échanges précédents (mémoire de la conversation) :"]
    for question in questions[-MAX_POINTS:]:
        court = _court(question)
        if court:
            lignes.append(f"- Le producteur a demandé : {court}")
    conseil_court = _court(dernier_conseil)
    if conseil_court:
        lignes.append(f"- Dernier conseil donné : {conseil_court}")
    # Un résumé réduit au seul en-tête n'apporte rien : on s'abstient alors.
    return "\n".join(lignes) if len(lignes) > 1 else ""


def _court(texte: str, longueur_max: int = LONGUEUR_POINT) -> str:
    """Compacte les espaces et tronque proprement un fragment de résumé."""
    texte = " ".join(texte.split()).strip()
    if len(texte) <= longueur_max:
        return texte
    coupe = texte[:longueur_max]
    espace = coupe.rfind(" ")
    if espace >= longueur_max // 2:
        coupe = coupe[:espace]
    return coupe.rstrip(" .,;:!?") + "…"
