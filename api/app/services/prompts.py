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
    "- Reste concis : va à l'essentiel, surtout pour une réponse par SMS."
)

CONTEXTE_PROMPT = (
    "Voici des extraits pertinents de la base de connaissances OpenCacao "
    "(sources officielles de la filière). Appuie-toi sur eux EN PRIORITÉ et "
    "reprends les sources qu'ils citent. S'ils ne suffisent pas, complète "
    "prudemment sans rien inventer.\n\n{contexte}"
)


def build_messages(question: str, contexte: str | None = None) -> list[dict[str, str]]:
    """Construit la liste de messages pour l'API d'inférence.

    Args:
        question: Question du producteur.
        contexte: Extraits récupérés (RAG) à injecter, ou None.

    Returns:
        Liste de messages au format chat (system [+ contexte] + user).
    """
    # Le template de Ministral 3 n'accepte qu'UN seul message système : on injecte
    # donc le contexte RAG dans le message utilisateur (et non en 2e system).
    if contexte:
        contenu_user = f"{CONTEXTE_PROMPT.format(contexte=contexte)}\n\nQuestion : {question}"
    else:
        contenu_user = question
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": contenu_user},
    ]
