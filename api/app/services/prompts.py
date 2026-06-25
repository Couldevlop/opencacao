"""Templates de prompt système, en français."""

from __future__ import annotations

SYSTEM_PROMPT = (
    "Tu es OpenCacao, un assistant de conseil agronomique destiné aux producteurs "
    "de cacao de Côte d'Ivoire.\n"
    "Règles :\n"
    "- Réponds en français simple, clair et bienveillant, adapté à un producteur "
    "qui n'est pas expert.\n"
    "- Fonde-toi sur les bonnes pratiques de la filière. Ne cite une source (CNRA, "
    "ANADER, Conseil du Café-Cacao, FAO, FIRCA) que si elle figure dans le contexte "
    "fourni ou si tu en es certain ; n'invente JAMAIS une source, une date, un chiffre "
    "ni un nom d'organisme.\n"
    "- Ne donne jamais de dosages précis de produits phytosanitaires : pour cela, "
    "oriente vers l'agent ANADER local.\n"
    "- Tu traites UNIQUEMENT le cacao. Pour toute autre culture (vivrier comme le "
    "maïs, le manioc, l'igname ou le riz, mais aussi l'anacarde, l'hévéa, le palmier…) "
    "ou tout autre sujet, explique poliment que ce n'est pas ton domaine et oriente "
    "vers l'agent ANADER local, qui couvre toutes les cultures. (Les arbres d'ombrage "
    "ou cultures associées restent acceptables UNIQUEMENT lorsqu'ils concernent la "
    "conduite d'une plantation de cacao.)\n"
    "- Réponds STRICTEMENT à la question posée, sans digression ni information non "
    "demandée. Si tu n'es pas sûr ou si l'information fiable te manque, dis-le "
    "simplement et oriente vers l'ANADER, au lieu d'inventer.\n"
    "- En conversation à plusieurs tours, garde le MÊME sujet que les tours "
    "précédents : résous les références (« le », « la », « ça », « ce traitement », "
    "« en faire », « à quelle fréquence ») d'après l'échange en cours et ne change "
    "jamais de thème de toi-même.\n"
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
    "Voici des extraits de la base de connaissances OpenCacao (sources officielles "
    "de la filière). Réponds en t'appuyant UNIQUEMENT sur ces extraits et ne cite "
    "que les sources qui y figurent. S'ils ne suffisent pas pour répondre, dis que "
    "tu ne disposes pas de l'information et oriente vers l'ANADER — n'invente rien.\n\n"
    "{contexte}"
)


def _dialogue_alternant(
    historique: list[dict[str, str]], question_finale: str
) -> list[dict[str, str]]:
    """Construit un dialogue à rôles STRICTEMENT alternés, finissant par l'utilisateur.

    Le template de chat de Ministral 3 lève une exception Jinja (« conversation roles
    must alternate ») si deux messages de même rôle se suivent ou si le dialogue ne
    commence pas par l'utilisateur. Un historique client mal formé (deux tours
    utilisateur d'affilée, assistant en tête…) provoquait alors un 500 de l'inférence
    → 503 côté API. On normalise donc : on fusionne les tours consécutifs de même
    rôle, on retire un éventuel assistant de tête, et on garantit que le dernier
    message est la question courante.

    Args:
        historique: Tours précédents (``role``/``content``), éventuellement mal formés.
        question_finale: Contenu du dernier message utilisateur (question + contexte).

    Returns:
        La liste des tours (hors message système) à rôles alternés, finissant par user.
    """
    tours: list[dict[str, str]] = []
    for tour in historique:
        role = tour.get("role")
        contenu = (tour.get("content") or "").strip()
        if role not in ("user", "assistant") or not contenu:
            continue
        if tours and tours[-1]["role"] == role:
            tours[-1]["content"] += "\n" + contenu  # fusionne deux tours de même rôle
        else:
            tours.append({"role": role, "content": contenu})
    while tours and tours[0]["role"] == "assistant":
        tours.pop(0)  # le dialogue doit commencer par l'utilisateur
    if tours and tours[-1]["role"] == "user":
        tours[-1]["content"] += "\n" + question_finale  # évite deux 'user' consécutifs
    else:
        tours.append({"role": "user", "content": question_finale})
    return tours


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
        Liste de messages au format chat : system + dialogue à rôles alternés finissant
        par le dernier message utilisateur (le template Ministral 3 l'exige).
    """
    # Le template de Ministral 3 n'accepte qu'UN seul message système : on injecte
    # donc le contexte RAG dans le message utilisateur (et non en 2e system).
    if contexte:
        contenu_user = f"{CONTEXTE_PROMPT.format(contexte=contexte)}\n\nQuestion : {question}"
    else:
        contenu_user = question
    dialogue = _dialogue_alternant(historique or [], contenu_user)
    return [{"role": "system", "content": SYSTEM_PROMPT}, *dialogue]
