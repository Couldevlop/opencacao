"""Résolution d'une demande en langage naturel vers un gabarit et un sujet.

On ne coche pas des cases pour demander une étude : on la demande. Ce module traduit
« fais-moi une étude de la filière sur la campagne 2025-2026 » en un couple
``(gabarit, sujet)`` exploitable par l'atelier.

**La résolution est déterministe, jamais générée.** Faire écrire au modèle « de quel
type de document s'agit-il ? » coûterait une génération complète AVANT que la
première section ne commence — des dizaines de secondes sur le profil CPU, pour une
question à laquelle un vocabulaire déclaré répond instantanément. Le vocabulaire vit
dans les gabarits (``declencheurs:``), pas ici : ajouter un type de document reste un
fichier YAML.

Ce qui n'est pas certain n'est pas deviné. Une demande ambiguë ressort avec ses
candidats, et c'est à l'appelant de poser UNE question — la même doctrine de
clarification que le reste d'OpenCacao.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from app.services.gabarits import Gabarit

# Plafond de la demande. Elle vient d'une saisie publique et ne sert qu'à choisir un
# type de document : au-delà, c'est du volume, pas de l'intention. Borné ici pour que
# le coût de la résolution ne dépende pas de la taille de l'entrée (OWASP API4), et
# repris tel quel par la couche HTTP.
DEMANDE_MAX = 2000

# Plafond du sujet. Il part dans le titre du document ET dans le prompt de chaque
# section : il est borné à la source, pas seulement à la validation d'entrée.
SUJET_MAX = 200

# Mots qui séparent la commande de son objet. « une étude SUR la campagne » : ce qui
# suit est le sujet, ce qui précède est la façon de le demander.
_PIVOTS = frozenset(
    {
        "sur",
        "pour",
        "de",
        "du",
        "des",
        "d",
        "concernant",
        "a",
        "au",
        "aux",
        "propos",
        "portant",
        "traitant",
        "about",
    }
)

# Déterminants qu'on garde avec le sujet — « la campagne 2025-2026 » se lit mieux que
# « campagne 2025-2026 » — mais qu'on traverse quand ils précèdent un mot de type.
_ARTICLES = frozenset(
    {"le", "la", "les", "l", "un", "une", "des", "du", "de", "ce", "cet", "cette"}
)

_MOT = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class Intention:
    """Ce qu'on a compris d'une demande.

    Attributes:
        gabarit: Identifiant du type de document reconnu, vide si aucun.
        sujet: Objet du document, vide s'il n'a pas été énoncé.
        certaine: Vrai seulement si le type ET le sujet sont établis sans ambiguïté.
        candidats: Types entre lesquels l'appelant doit faire trancher. Vide quand
            l'intention est certaine.
    """

    gabarit: str
    sujet: str
    certaine: bool
    candidats: tuple[str, ...]


def _plier(texte: str) -> str:
    """Replie un texte pour la RECONNAISSANCE, en conservant les positions.

    Accents retirés et casse abaissée caractère par caractère : la chaîne rendue a
    exactement la même longueur que l'entrée, ce qui permet de repérer un mot ici et
    de découper le texte d'origine là-bas — accents compris.

    Args:
        texte: Texte d'origine.

    Returns:
        Le texte replié, de même longueur.
    """
    return "".join(unicodedata.normalize("NFD", caractere)[0].lower() for caractere in texte)


def _assainir(texte: str) -> str:
    """Retire les caractères de contrôle d'une demande.

    Le sujet finit dans un fichier Word et dans un prompt : ni l'un ni l'autre ne
    doit recevoir un octet de contrôle. On remplace par une espace plutôt que de
    supprimer, pour ne pas souder deux mots.

    Args:
        texte: Demande brute.

    Returns:
        La demande assainie, tronquée à ``DEMANDE_MAX``.
    """
    # NFC AVANT tout le reste. Les clients macOS/iOS et plusieurs claviers Android
    # émettent du NFD : « étude » y est un « e » suivi d'un accent combinant isolé, que
    # `_MOT` traite en séparateur — le mot devient « e » + « tude » et aucun déclencheur
    # ne correspond plus. Toute cette population recevait systématiquement la question
    # de clarification, sans que les tests (écrits en NFC) puissent le voir.
    # La composition peut allonger, donc on tronque APRÈS.
    compose = unicodedata.normalize("NFC", texte)[:DEMANDE_MAX]
    return "".join(
        " " if unicodedata.category(caractere) in {"Cc", "Cf", "Cs"} else caractere
        for caractere in compose
    )


def assainir_sujet(texte: str) -> str:
    """Met un sujet en état d'être utilisé, d'où qu'il vienne.

    À appliquer sur la frontière de confiance de la GÉNÉRATION, pas seulement sur la
    route d'interprétation : rien n'oblige un client à passer par celle-ci, et le sujet
    atterrit verbatim dans le titre d'un document téléchargeable et dans le prompt de
    chaque section. Un saut de ligne y ouvrait un tour de consignes à part entière.

    Args:
        texte: Sujet brut, tel que reçu.

    Returns:
        Le sujet sans caractère de contrôle, sur une seule ligne, borné.
    """
    return _nettoyer_sujet(_assainir(texte))


def _nettoyer_sujet(texte: str) -> str:
    """Met un sujet en forme : espaces normalisés, ponctuation de fin retirée, borné.

    Args:
        texte: Fragment de la demande tenu pour le sujet.

    Returns:
        Le sujet présentable, éventuellement vide.
    """
    sujet = " ".join(texte.split()).strip(" ,;:.!?-—«»\"'")
    if len(sujet) <= SUJET_MAX:
        return sujet
    # Couper au mot : « la campagne 2025-20 » est pire que court.
    coupe = sujet[:SUJET_MAX]
    espace = coupe.rfind(" ")
    return (coupe[:espace] if espace > 0 else coupe).rstrip(" ,;:.!?-")


def _correspond(mot: str, declencheur: str) -> bool:
    """Dit si un mot de la demande désigne un déclencheur.

    Le pluriel est toléré — « bulletins », « études » — mais rien d'autre : une
    correspondance par sous-chaîne ferait répondre « région » à « enregistrement ».

    Args:
        mot: Mot replié issu de la demande.
        declencheur: Déclencheur replié issu du gabarit.

    Returns:
        Vrai si le mot désigne ce déclencheur.
    """
    return mot == declencheur or mot == declencheur + "s"


def resoudre_demande(demande: str, gabarits: Iterable[Gabarit]) -> Intention:
    """Résout une demande en langage naturel.

    Args:
        demande: Ce que la personne a écrit, tel quel.
        gabarits: Catalogue des types de documents disponibles.

    Returns:
        L'intention comprise. ``certaine`` ne vaut ``True`` que si le type et le
        sujet sont tous deux établis et qu'aucun autre type n'est à égalité.
    """
    catalogue = tuple(gabarits)
    if not catalogue:
        return Intention(gabarit="", sujet="", certaine=False, candidats=())

    texte = _assainir(demande)
    plie = _plier(texte)
    mots = [(trouve.group(), trouve.span()) for trouve in _MOT.finditer(plie)]

    # --- quel type de document ------------------------------------------------
    # Les mots sont indexés UNE fois. La version naïve repliait chaque déclencheur pour
    # chaque mot de chaque gabarit : à 1 000 mots, des dizaines de milliers de pliages
    # redondants, et un catalogue un peu gras — montable par ConfigMap — transformait
    # une seule requête en plusieurs secondes de CPU. On passe en O(mots + gabarits ×
    # déclencheurs).
    premiere_fin: dict[str, int] = {}
    for mot, (_, fin) in mots:
        premiere_fin.setdefault(mot, fin)

    scores: dict[str, int] = {}
    premier_declencheur = len(texte)
    for gabarit in catalogue:
        touches = 0
        for brut in gabarit.declencheurs:
            declencheur = _plier(brut)
            # Le pluriel est toléré, et rien d'autre : voir `_correspond`.
            fins = [
                fin
                for fin in (premiere_fin.get(declencheur), premiere_fin.get(declencheur + "s"))
                if fin is not None
            ]
            if fins:
                touches += 1
                premier_declencheur = min(premier_declencheur, *fins)
        scores[gabarit.identifiant] = touches

    meilleur = max(scores.values())
    en_tete = tuple(sorted(nom for nom, score in scores.items() if score == meilleur))

    # --- de quoi parle-t-elle -------------------------------------------------
    if meilleur == 0:
        # Aucun type reconnu : la demande ENTIÈRE est le sujet. Le type se demandera.
        sujet = _nettoyer_sujet(texte)
    else:
        sujet = _nettoyer_sujet(texte[_coupe_sujet(texte, plie, mots, premier_declencheur) :])

    if meilleur == 0:
        return Intention(
            gabarit="",
            sujet=sujet,
            certaine=False,
            candidats=tuple(sorted(scores)),
        )
    if len(en_tete) > 1:
        return Intention(gabarit="", sujet=sujet, certaine=False, candidats=en_tete)

    retenu = next(gabarit for gabarit in catalogue if gabarit.identifiant == en_tete[0])
    sujet = _degager_du_type(sujet, retenu.declencheurs)
    return Intention(
        gabarit=en_tete[0],
        sujet=sujet,
        certaine=bool(sujet),
        candidats=() if sujet else en_tete,
    )


def _degager_du_type(sujet: str, declencheurs: tuple[str, ...]) -> str:
    """Retire du sujet ce qui ne fait que renommer le type de document.

    « un bulletin pour la région de Daloa » donne d'abord « la région de Daloa », dont
    « la région » ne dit rien de plus que le mot « régional » déjà porté par le titre :
    « Bulletin régional — Daloa » se lit mieux que « — la région de Daloa ».

    On ne retire que le vocabulaire du gabarit RETENU : dans une étude, « la région
    d'Abengourou » est un vrai sujet, et doit le rester.

    Args:
        sujet: Sujet extrait de la demande.
        declencheurs: Vocabulaire du gabarit retenu.

    Returns:
        Le sujet dégagé, éventuellement vide si la demande ne nommait aucun objet.
    """
    mots_type = {_plier(brut) for brut in declencheurs}
    courant = sujet
    # Bornée : « dossier de parcelle de traçabilité de… » ne doit pas boucler.
    for _ in range(len(mots_type) + 1):
        mots = [(m.group(), m.span()) for m in _MOT.finditer(_plier(courant))]
        if not mots:
            return ""
        rang = 1 if mots[0][0] in _ARTICLES and len(mots) > 1 else 0
        if not any(_correspond(mots[rang][0], type_) for type_ in mots_type):
            return courant
        reste = rang + 1
        # Le pivot qui suit appartient à la formule, pas au sujet : « de la parcelle
        # DE Kouadio » laisse « Kouadio ».
        if reste < len(mots) and mots[reste][0] in _PIVOTS:
            reste += 1
        if reste >= len(mots):
            return ""
        courant = _nettoyer_sujet(courant[mots[reste][1][0] :])
    return courant


def _coupe_sujet(
    texte: str,
    plie: str,
    mots: list[tuple[str, tuple[int, int]]],
    apres: int,
) -> int:
    """Trouve où commence le sujet, une fois la formule de commande passée.

    Args:
        texte: Demande assainie.
        plie: Même demande repliée, positions identiques.
        mots: Mots repérés avec leurs bornes.
        apres: Position à partir de laquelle chercher (fin du déclencheur).

    Returns:
        L'indice de début du sujet, ou la fin du texte s'il n'y en a pas.
    """
    coupes = [fin for mot, (debut, fin) in mots if debut >= apres and mot in _PIVOTS]
    deux_points = plie.find(":", apres)
    if deux_points >= 0:
        coupes.append(deux_points + 1)
    return min(coupes) if coupes else len(texte)
