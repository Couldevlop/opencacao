"""Fiche du producteur : les faits durables d'une conversation.

``memoire.py`` borne le contexte à une **fenêtre de 8 messages** pour tenir la latence
CPU. C'est nécessaire, mais cela efface les faits : une localité citée au 3e tour a
disparu au 12e, et le modèle la redemande — le producteur a le sentiment de parler à
quelqu'un qui ne l'écoute pas.

Ce module relit donc **tout** le fil et n'en extrait qu'une poignée de faits stables
(localité, âge et surface de la plantation, sujet en cours), condensés en un bloc
court injecté au prompt. Coût : quelques regex, aucune inférence — la fenêtre reste
petite, mais ce qui compte survit.

Deux garde-fous d'anti-fabrication gouvernent l'extraction :

- **Seuls les tours du PRODUCTEUR comptent.** Le contact ANADER ajouté en fin de
  réponse cite une ville ; la reprendre ferait de la DR d'Abengourou le domicile du
  producteur.
- **Rien de déduit.** Un fait non énoncé reste absent, et une fiche vide ne produit
  aucun bloc : le modèle ne reçoit alors rien à reformuler.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services import clarification, localites

# Surface : « 3 hectares », « 2,5 ha », « 4 h a » exclu (frontière de mot exigée).
_SUPERFICIE = re.compile(r"(\d{1,4}(?:[.,]\d{1,2})?)\s*(?:hectares?|ha)\b")
# Âge : « 15 ans ». Le motif capture aussi ce qui précède, pour écarter l'âge de
# l'homme (« j'ai 45 ans ») — un producteur parle de lui autant que de ses arbres.
_AGE = re.compile(r"(?:(j ai|jai|age de|agee? de)\s+)?(\d{1,3})\s*ans\b")
_AGE_HUMAIN = ("j ai", "jai", "age de", "agee de", "age de")
# Un cacaoyer dépasse rarement 60 ans ; au-delà, c'est un autre sujet que la parcelle.
_AGE_MAX_PLANTATION = 60

# Partie de l'arbre citée par le producteur. C'est l'une des deux moitiés du
# questionnaire « symptôme » — et en production le 11/09, « Les feuilles de mon
# cacaoyer jaunissent » recevait « Quelles parties sont touchées ? ». La réponse était
# dans la question : la fiche ne savait pas la lire, donc la consigne la redemandait.
#
# Les libellés sont ceux de la consigne de clarification, pour que le modèle reçoive un
# vocabulaire cohérent avec ce qu'il aurait demandé.
_PARTIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("les feuilles", ("feuille", "feuilles", "limbe", "feuillage")),
    ("les cabosses", ("cabosse", "cabosses", "fruit", "fruits", "fève", "feves", "fèves")),
    (
        "le tronc ou les rameaux",
        ("tronc", "rameau", "rameaux", "branche", "branches", "tige", "tiges", "ecorce"),
    ),
    ("les racines", ("racine", "racines", "pivot")),
    ("les fleurs", ("fleur", "fleurs", "floraison")),
)

# Ancienneté du symptôme : l'autre moitié du questionnaire. On garde la formulation du
# producteur (« depuis deux semaines »), pas une durée normalisée — on ne saurait pas
# quoi faire d'une valeur numérique, et la citer telle quelle prouve qu'on a écouté.
_ANCIENNETE = re.compile(
    r"depuis\s+((?:un|une|deux|trois|quatre|cinq|six|sept|huit|neuf|dix|\d{1,3})\s*"
    r"(?:jours?|semaines?|mois|ans?|annees?)|hier|peu|longtemps|toujours)"
)

_LIBELLES_SUJET: dict[str, str] = {
    "symptome": "les symptômes observés sur les cacaoyers",
    "traitement": "le traitement à mener",
    "rendement": "le rendement de la plantation",
    "fertilisation": "la fertilisation de la plantation",
    "plantation": "la création d'une plantation",
}


@dataclass(frozen=True)
class Fiche:
    """Faits durables énoncés par le producteur au fil de la conversation."""

    localite: str = ""
    age_ans: int | None = None
    superficie_ha: float | None = None
    sujet: str = ""
    partie: str = ""
    anciennete: str = ""

    @property
    def vide(self) -> bool:
        """Vrai si le producteur n'a encore rien dit de mémorisable."""
        return not (
            self.localite
            or self.age_ans
            or self.superficie_ha
            or self.sujet
            or self.partie
            or self.anciennete
        )


def _fil_producteur(question: str, historique: list[dict[str, str]] | None) -> str:
    """Concatène les tours du PRODUCTEUR (jamais ceux de l'assistant) et la question."""
    tours = [t.get("content", "") for t in (historique or []) if t.get("role") == "user"]
    return " ".join([*tours, question])


def _normaliser(texte: str) -> str:
    """Minuscule et ponctuation réduite, pour des regex insensibles à l'apostrophe.

    Le point et la virgule sont PRÉSERVÉS : les écraser coupait « 2,5 ha » en
    « 2 5 ha », et la parcelle passait de 2,5 à 5 hectares.
    """
    return re.sub(r"[^0-9a-zà-ÿ.,]+", " ", texte.lower()).strip()


def _superficie(texte: str) -> float | None:
    """Première surface citée, en hectares, ou ``None``."""
    trouve = _SUPERFICIE.search(texte)
    return float(trouve.group(1).replace(",", ".")) if trouve else None


def _age(texte: str) -> int | None:
    """Âge de la PLANTATION en années, ou ``None`` si seul l'âge de l'homme est cité."""
    for amorce, valeur in _AGE.findall(texte):
        if amorce.strip() in _AGE_HUMAIN:
            continue  # « j'ai 45 ans » : c'est le producteur, pas la parcelle
        age = int(valeur)
        if 0 < age <= _AGE_MAX_PLANTATION:
            return age
    return None


def _partie(texte: str) -> str:
    """Partie de l'arbre citée par le producteur, ou ``""``.

    La PREMIÈRE citée dans le fil l'emporte : c'est celle qui a motivé la demande, et
    les organes énumérés ensuite sont le plus souvent ceux d'une comparaison.
    """
    meilleur: tuple[int, str] | None = None
    for libelle, mots in _PARTIES:
        for mot in mots:
            trouve = re.search(rf"\b{re.escape(mot)}\b", texte)
            if trouve and (meilleur is None or trouve.start() < meilleur[0]):
                meilleur = (trouve.start(), libelle)
    return meilleur[1] if meilleur else ""


def _anciennete(texte: str) -> str:
    """Ancienneté du symptôme telle que le producteur l'a dite, ou ``""``."""
    trouve = _ANCIENNETE.search(texte)
    return f"depuis {trouve.group(1).strip()}" if trouve else ""


def extraire(question: str, historique: list[dict[str, str]] | None) -> Fiche:
    """Construit la fiche à partir de TOUT le fil, hors tours de l'assistant.

    Args:
        question: Dernier tour du producteur.
        historique: Tours précédents de la conversation, ou ``None``.

    Returns:
        Une fiche ne contenant que des faits explicitement énoncés (jamais déduits).
    """
    fil = _fil_producteur(question, historique)
    norme = _normaliser(fil)
    sujet = clarification.theme_du_texte(fil) or ""
    # La partie atteinte et l'ancienneté n'ont de sens que pour un PROBLÈME observé.
    # « Quand récolter les cabosses ? » nomme un organe sans rien décrire : en tirer un
    # fait ferait dire au modèle « le problème touche les cabosses » devant une question
    # de calendrier. On ne déduit rien ailleurs, on ne déduit rien ici non plus.
    concerne_un_symptome = sujet in ("symptome", "traitement")
    return Fiche(
        localite=localites.detecter(fil) or "",
        age_ans=_age(norme),
        superficie_ha=_superficie(norme),
        sujet=sujet,
        partie=_partie(norme) if concerne_un_symptome else "",
        anciennete=_anciennete(norme) if concerne_un_symptome else "",
    )


def _plantation(fiche: Fiche) -> str:
    """Décrit la parcelle à partir des seuls éléments connus."""
    elements: list[str] = []
    if fiche.superficie_ha is not None:
        elements.append(f"{fiche.superficie_ha:g} ha")
    if fiche.age_ans is not None:
        elements.append(f"environ {fiche.age_ans} ans")
    return ", ".join(elements)


def bloc_memoire(fiche: Fiche) -> str:
    """Rend le bloc à injecter au prompt, ou ``""`` si rien n'est connu.

    Args:
        fiche: Fiche extraite du fil.

    Returns:
        Un bloc court listant les faits connus et interdisant de les redemander ;
        une chaîne vide si la fiche est vide (rien à reformuler, rien à inventer).
    """
    if fiche.vide:
        return ""
    lignes = [
        "MÉMOIRE DE LA CONVERSATION — faits que le producteur vous a lui-même donnés. "
        "Ils sont acquis, indépendamment des extraits documentaires : reprenez-les en une "
        "phrase courte au début de votre réponse pour montrer que vous avez écouté, ne les "
        "redemandez jamais, et ne les présentez pas comme une source.",
    ]
    if fiche.localite:
        lignes.append(f"- Localité : {fiche.localite}")
    plantation = _plantation(fiche)
    if plantation:
        lignes.append(f"- Plantation : {plantation}")
    if fiche.sujet:
        lignes.append(f"- Sujet en cours : {_LIBELLES_SUJET.get(fiche.sujet, fiche.sujet)}")
    return "\n".join(lignes)


def rappel_court(fiche: Fiche) -> str:
    """Résume le fil en une poignée de mots, pour rouvrir une conversation reprise.

    Args:
        fiche: Fiche extraite du fil.

    Returns:
        Un fragment du type « les symptômes observés sur les cacaoyers, à Soubré »,
        ou ``""`` si rien ne permet de prétendre se souvenir de quelque chose.
    """
    morceaux: list[str] = []
    if fiche.sujet:
        morceaux.append(_LIBELLES_SUJET.get(fiche.sujet, fiche.sujet))
    if fiche.localite:
        morceaux.append(f"à {fiche.localite}")
    return ", ".join(morceaux)


def faits_connus(fiche: Fiche) -> str:
    """Résume en une phrase ce que le producteur a déjà dit, pour ne plus le redemander.

    Destiné à la consigne de clarification : celle-ci est déterministe et réclamait son
    questionnaire complet sans savoir ce qui avait déjà été énoncé — en production, elle
    redemandait la ville et la surface alors qu'elles figuraient dans la fiche.

    Args:
        fiche: Fiche extraite du fil.

    Returns:
        Une énumération lisible (« il se trouve à Soubré ; sa plantation fait 3 ha »),
        ou ``""`` si rien n'est connu — la consigne reste alors celle d'avant.
    """
    morceaux: list[str] = []
    if fiche.localite:
        morceaux.append(f"il se trouve à {fiche.localite}")
    if fiche.superficie_ha is not None:
        morceaux.append(f"sa plantation fait {fiche.superficie_ha:g} ha")
    if fiche.age_ans is not None:
        morceaux.append(f"elle a environ {fiche.age_ans} ans")
    if fiche.partie:
        morceaux.append(f"le problème touche {fiche.partie}")
    if fiche.anciennete:
        morceaux.append(f"cela dure {fiche.anciennete}")
    return " ; ".join(morceaux)
