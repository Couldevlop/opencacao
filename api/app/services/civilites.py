"""Civilités : les tours de parole qui n'appellent aucun conseil agronomique.

Un « Bonjour » n'est pas une question. Sans ce module, il traversait tout le
pipeline (garde-fous → clarification → RAG → inférence) et le modèle, sommé de
conseiller, **fabriquait une consultation** : en production, un simple bonjour
répondait par des maladies jamais évoquées et une source « CNRA » inventée.

Ce module reconnaît les tours de pure sociabilité — salutation, remerciement,
congé, acquiescement, question d'identité — et y répond par une **chaîne constante**.
Deux propriétés en découlent :

- **Instantané** : aucune inférence, donc aucune des ~38 s de génération CPU.
- **Sûr en amont des garde-fous** : ce chemin ne produit AUCUN texte de modèle, il
  n'y a donc rien à filtrer en sortie. C'est ce qui autorise à le placer avant eux.

La détection est **fermée et étroite** : un tour n'est une civilité que s'il ne reste
plus rien une fois les formules retirées. « Bonjour, mes cabosses noircissent » et
« Bonjour, quelle dose de fongicide ? » suivent donc le chemin normal — le second
reste soumis au garde-fou phytosanitaire.
"""

from __future__ import annotations

import re
import unicodedata
from enum import Enum


class Civilite(str, Enum):
    """Nature d'un tour de parole sans demande de conseil."""

    SALUTATION = "salutation"
    REMERCIEMENT = "remerciement"
    ADIEU = "adieu"
    IDENTITE = "identite"
    ACQUIESCEMENT = "acquiescement"


# Questions d'identité : reconnues sur la TOTALITÉ du tour (elles ne se combinent pas
# avec autre chose), contrairement aux formules ci-dessous qui, elles, se retirent.
_IDENTITE: frozenset[str] = frozenset(
    {
        "qui es tu",
        "qui est tu",
        "tu es qui",
        "qui etes vous",
        "vous etes qui",
        "c est quoi opencacao",
        "qu est ce qu opencacao",
        "qu est ce que opencacao",
        "opencacao c est quoi",
        "que peux tu faire",
        "que pouvez vous faire",
        "tu sers a quoi",
        "presente toi",
        "presentez vous",
    }
)

# Formules retirables, de la plus longue à la plus courte (« merci beaucoup » avant
# « merci ») pour que le retrait ne laisse pas de résidu parasite.
# Volontairement ABSENTS des acquiescements : « oui » et « bien » seuls, qui sont des
# réponses de fond à une question de clarification (« Observez-vous des maladies ? »).
_FORMULES: tuple[tuple[str, Civilite], ...] = (
    ("je vous remercie", Civilite.REMERCIEMENT),
    ("merci beaucoup", Civilite.REMERCIEMENT),
    ("merci bien", Civilite.REMERCIEMENT),
    ("merci", Civilite.REMERCIEMENT),
    ("au revoir", Civilite.ADIEU),
    ("a bientot", Civilite.ADIEU),
    ("a plus tard", Civilite.ADIEU),
    ("bonne journee", Civilite.ADIEU),
    ("bonne soiree", Civilite.ADIEU),
    ("bonne continuation", Civilite.ADIEU),
    ("bonne recolte", Civilite.ADIEU),
    ("adieu", Civilite.ADIEU),
    ("bye", Civilite.ADIEU),
    ("j espere que vous allez bien", Civilite.SALUTATION),
    ("comment allez vous", Civilite.SALUTATION),
    ("comment ca va", Civilite.SALUTATION),
    ("vous allez bien", Civilite.SALUTATION),
    ("ca va", Civilite.SALUTATION),
    ("bonjour", Civilite.SALUTATION),
    ("bonsoir", Civilite.SALUTATION),
    ("salut", Civilite.SALUTATION),
    ("coucou", Civilite.SALUTATION),
    ("hello", Civilite.SALUTATION),
    ("d accord", Civilite.ACQUIESCEMENT),
    ("tres bien", Civilite.ACQUIESCEMENT),
    ("ca marche", Civilite.ACQUIESCEMENT),
    ("compris", Civilite.ACQUIESCEMENT),
    ("entendu", Civilite.ACQUIESCEMENT),
    ("parfait", Civilite.ACQUIESCEMENT),
    ("ok", Civilite.ACQUIESCEMENT),
)

# Mots de liaison sans valeur : leur présence ne fait pas d'un tour une demande.
_REMPLISSAGE: tuple[str, ...] = (
    "s il vous plait",
    "s il te plait",
    "beaucoup",
    "infiniment",
    "vraiment",
    "encore",
    "bien",
    "svp",
    "monsieur",
    "madame",
    "cher",
    "chere",
    "et",
    "a vous",
    "aussi",
    "pour tout",
    "tout",
    "alors",
)

# Priorité quand plusieurs formules coexistent (« Bonjour, merci ! ») : on répond à
# l'intention la plus engageante, la salutation ne primant jamais sur un remerciement.
_PRIORITE: tuple[Civilite, ...] = (
    Civilite.REMERCIEMENT,
    Civilite.ADIEU,
    Civilite.SALUTATION,
    Civilite.ACQUIESCEMENT,
)

_SALUTATION = (
    "Bonjour ! Je suis OpenCacao, votre assistant sur la culture du cacao. "
    "Que se passe-t-il dans votre plantation, et dans quelle ville ou région "
    "vous trouvez-vous ?"
)

_SALUTATION_REPRISE = (
    "Bonjour ! Nous parlions de {rappel}. Voulez-vous que nous reprenions là-dessus, "
    "ou avez-vous autre chose en tête ?"
)

_REPONSES: dict[Civilite, str] = {
    Civilite.SALUTATION: _SALUTATION,
    Civilite.REMERCIEMENT: (
        "Je vous en prie, c'est avec plaisir. Revenez vers moi dès que vous avez un "
        "doute sur votre plantation."
    ),
    Civilite.ADIEU: ("Au revoir, et bonne récolte ! Revenez quand vous voulez, je serai là."),
    Civilite.IDENTITE: (
        "Je suis OpenCacao, un assistant qui répond sur la culture du cacao en Côte "
        "d'Ivoire : entretien de la plantation, maladies et ravageurs, récolte, "
        "fermentation et séchage, prix et réglementation. Je ne remplace pas votre "
        "agent ANADER — c'est lui qui voit votre parcelle et confirme un diagnostic. "
        "Que puis-je faire pour vous ?"
    ),
    Civilite.ACQUIESCEMENT: (
        "Très bien. Dites-moi si vous voulez que je détaille un point, ou posez-moi "
        "votre question suivante."
    ),
}


def _normaliser(texte: str) -> str:
    """Minuscule, sans accents, ponctuation réduite à des espaces.

    « Bonjour, comment allez-vous ? » devient « bonjour comment allez vous », ce qui
    rend la reconnaissance insensible à la ponctuation et aux apostrophes.
    """
    sans_accents = "".join(
        c for c in unicodedata.normalize("NFD", texte) if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"[^a-z0-9]+", " ", sans_accents.lower()).strip()


def _retirer(texte: str, expression: str) -> tuple[str, bool]:
    """Retire toutes les occurrences d'une expression (sur frontières de mots)."""
    motif = re.compile(rf"\b{re.escape(expression)}\b")
    if not motif.search(texte):
        return texte, False
    return re.sub(r"\s+", " ", motif.sub(" ", texte)).strip(), True


def detecter(question: str) -> Civilite | None:
    """Reconnaît un tour de pure sociabilité.

    Args:
        question: Dernier tour du producteur, tel qu'il l'a écrit.

    Returns:
        La nature de la civilité, ou ``None`` dès qu'il subsiste la moindre demande —
        auquel cas le tour doit suivre le pipeline de conseil complet.
    """
    texte = _normaliser(question)
    if not texte:
        return None
    if texte in _IDENTITE:
        return Civilite.IDENTITE

    trouvees: set[Civilite] = set()
    for expression, civilite in _FORMULES:
        texte, retiree = _retirer(texte, expression)
        if retiree:
            trouvees.add(civilite)
    if not trouvees:
        return None

    for expression in _REMPLISSAGE:
        texte, _ = _retirer(texte, expression)
    if texte:
        return None  # il reste une demande : ce n'est pas une civilité

    return next(civilite for civilite in _PRIORITE if civilite in trouvees)


def repondre(civilite: Civilite, rappel: str = "") -> str:
    """Rend la réponse écrite d'avance correspondant à une civilité.

    Args:
        civilite: Nature du tour, telle que rendue par :func:`detecter`.
        rappel: Résumé court du fil déjà engagé (cf. ``fiche.rappel_court``). Utilisé
            pour rouvrir une conversation reprise ; ignoré sur les formules de
            clôture, où relancer quelqu'un qui prend congé serait déplacé.

    Returns:
        Un texte constant — jamais une génération du modèle.
    """
    if civilite is Civilite.SALUTATION and rappel:
        return _SALUTATION_REPRISE.format(rappel=rappel)
    return _REPONSES[civilite]
