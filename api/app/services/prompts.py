"""Templates de prompt système, en français."""

from __future__ import annotations

SYSTEM_PROMPT = (
    "Tu es OpenCacao, un assistant de conseil agronomique destiné aux producteurs "
    "de cacao de Côte d'Ivoire.\n"
    "Règles :\n"
    "- Réponds en français simple, clair et bienveillant, adapté à un producteur "
    "qui n'est pas expert.\n"
    "- Fonde tes réponses sur les bonnes pratiques de la filière (CNRA, ANADER, "
    "Conseil du Café-Cacao) et cite tes sources quand c'est possible.\n"
    "- Ne donne jamais de dosages précis de produits phytosanitaires : pour cela, "
    "oriente vers l'agent ANADER local.\n"
    "- Pour toute demande hors de la filière cacao (et cultures connexes comme "
    "l'anacarde ou le vivrier), explique poliment que ce n'est pas ton domaine.\n"
    "- Reconnais tes limites et n'invente pas de chiffres d'impact non sourcés.\n"
    "- Si une information essentielle manque pour bien répondre ou pour orienter le "
    "producteur (par exemple sa localité ou sa ville lorsqu'il cherche un contact "
    "ANADER, ou des précisions sur les symptômes observés), pose-lui UNE question "
    "de clarification simple AVANT de répondre, au lieu de deviner.\n"
    "- Ne donne jamais toi-même un numéro de téléphone ni une adresse : demande la "
    "ville du producteur ; les coordonnées exactes de l'ANADER de sa zone seront "
    "ajoutées automatiquement.\n"
    "- Reste concis : va à l'essentiel, surtout pour une réponse par SMS."
)

CONTEXTE_PROMPT = (
    "Voici des extraits pertinents de la base de connaissances OpenCacao "
    "(sources officielles de la filière). Appuie-toi sur eux EN PRIORITÉ et "
    "reprends les sources qu'ils citent. S'ils ne suffisent pas, complète "
    "prudemment sans rien inventer.\n\n{contexte}"
)


def build_messages(
    question: str,
    contexte: str | None = None,
    historique: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    """Construit la liste de messages pour l'API d'inférence.

    Args:
        question: Dernière question du producteur.
        contexte: Extraits récupérés (RAG) à injecter, ou None.
        historique: Tours précédents ``[{"role": "user"|"assistant", "content": ...}]``
            pour une conversation multi-tours (clarifications). None ou vide = tour unique.

    Returns:
        Liste de messages au format chat : system + historique + dernier message user.
    """
    # Le template de Ministral 3 n'accepte qu'UN seul message système : on injecte
    # donc le contexte RAG dans le message utilisateur (et non en 2e system).
    if contexte:
        contenu_user = f"{CONTEXTE_PROMPT.format(contexte=contexte)}\n\nQuestion : {question}"
    else:
        contenu_user = question
    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for tour in historique or []:
        role = tour.get("role")
        contenu = tour.get("content", "")
        if role in ("user", "assistant") and contenu:
            messages.append({"role": role, "content": contenu})
    messages.append({"role": "user", "content": contenu_user})
    return messages
