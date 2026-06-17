"""Garde-fous métier : refus systématique et orientation vers l'ANADER.

Voir CLAUDE_OpenCacao.md §4.3. Chaque règle de refus dispose d'un test dédié.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from app.models.domain import CategorieRefus

# --- Messages de refus standardisés (constantes) ---

REFUS_PHYTO = (
    "Pour des dosages précis de produits phytosanitaires, je vous oriente "
    "vers votre agent ANADER local ou la délégation du Conseil du Café-Cacao "
    "de votre zone. Je peux en revanche vous renseigner sur les bonnes "
    "pratiques générales et la reconnaissance des symptômes."
)

REFUS_MEDICAL = (
    "Je suis un assistant agronomique pour la filière cacao et je ne peux pas "
    "répondre à des questions médicales ou vétérinaires. Pour la santé humaine, "
    "consultez un professionnel de santé ; pour les animaux, un vétérinaire."
)

REFUS_DIAGNOSTIC_IMAGE = (
    "Je ne peux pas identifier une maladie à partir d'une photo sans confirmation "
    "d'un agent de terrain. Décrivez-moi les symptômes observés (feuilles, cabosses, "
    "rameaux) et, pour un diagnostic fiable, contactez votre agent ANADER local."
)

REFUS_HORS_FILIERE = (
    "Je suis spécialisé dans la filière cacao (et les cultures connexes comme "
    "l'anacarde et le vivrier). Votre question semble en dehors de ce domaine, "
    "je préfère ne pas y répondre pour éviter de vous induire en erreur."
)

_REFUS_MESSAGES: dict[CategorieRefus, str] = {
    CategorieRefus.PHYTOSANITAIRE: REFUS_PHYTO,
    CategorieRefus.MEDICAL: REFUS_MEDICAL,
    CategorieRefus.DIAGNOSTIC_IMAGE: REFUS_DIAGNOSTIC_IMAGE,
    CategorieRefus.HORS_FILIERE: REFUS_HORS_FILIERE,
}


@dataclass(frozen=True)
class Refus:
    """Résultat d'un garde-fou déclenché.

    Attributes:
        categorie: Catégorie de refus.
        message: Message de redirection à renvoyer au producteur.
    """

    categorie: CategorieRefus
    message: str = field(default="")

    def __post_init__(self) -> None:
        if not self.message:
            object.__setattr__(self, "message", _REFUS_MESSAGES[self.categorie])


# --- Vocabulaire de détection ---

_TERMES_PHYTO = (
    "phytosanitaire",
    "pesticide",
    "fongicide",
    "insecticide",
    "herbicide",
    "acaricide",
    "engrais",
    "produit chimique",
    "traitement chimique",
)

_TERMES_DOSAGE = (
    "dose",
    "dosage",
    "quelle quantite",
    "combien de",
    "combien d",
    "quelle dose",
    "ml par",
    "litres par",
    "litre par",
    "grammes par",
    "gramme par",
    "g par",
    "kg par",
    "par hectare",
    "par litre",
    "par pulverisateur",
    "melanger",
    "concentration",
)

_TERMES_MEDICAL = (
    "maladie humaine",
    "ma sante",
    "mal de tete",
    "fievre",
    "medicament",
    "docteur",
    "medecin",
    "veterinaire",
    "mon chien",
    "mon chat",
    "ma vache",
    "mes poules",
    "mon enfant",
)

_TERMES_IMAGE = (
    "photo",
    "image",
    "cette image",
    "sur la photo",
    "je joins",
    "ci-joint",
    "ci joint",
    "regarde la photo",
    "identifie sur",
)

# La filière cacao et les cultures explicitement connexes restent dans le périmètre.
_TERMES_FILIERE = (
    "cacao",
    "cacaoyer",
    "cabosse",
    "feve",
    "fermentation",
    "swollen shoot",
    "cssv",
    "miride",
    "capside",
    "phytophthora",
    "pourriture brune",
    "verger",
    "pepiniere",
    "ombrage",
    "sechage",
    "recolte",
    "plantation",
    "anacarde",
    "vivrier",
    "igname",
    "banane",
    "taro",
    "manioc",
    "ANADER",
    "verger",
)

# Indices forts qu'une question vise une toute autre filière / hors-sujet.
_TERMES_HORS_FILIERE = (
    "bitcoin",
    "football",
    "telephone",
    "ordinateur",
    "voiture",
    "elections",
    "recette de cuisine",
    "musique",
    "hevea",
    "palmier a huile",
    "coton",
)


def _normaliser(texte: str) -> str:
    """Minuscule + suppression des accents pour une détection robuste."""
    sans_accents = "".join(
        c for c in unicodedata.normalize("NFD", texte) if unicodedata.category(c) != "Mn"
    )
    return sans_accents.lower()


def _contient(texte: str, termes: tuple[str, ...]) -> bool:
    return any(terme in texte for terme in termes)


def evaluer(question: str) -> Refus | None:
    """Évalue une question et retourne un refus si une règle s'applique.

    Ordre de priorité : phytosanitaire, médical/vétérinaire, diagnostic sur image,
    hors-filière. Retourne None si la question peut être traitée par le modèle.

    Args:
        question: Question brute du producteur.

    Returns:
        Un objet Refus si un garde-fou se déclenche, sinon None.
    """
    texte = _normaliser(question)

    # 1. Dosages phytosanitaires : terme phyto + intention de dosage, ou présence
    #    d'une valeur chiffrée associée à une unité de dose.
    if _contient(texte, _TERMES_PHYTO) and (
        _contient(texte, _TERMES_DOSAGE) or _contient_valeur_dosee(texte)
    ):
        return Refus(CategorieRefus.PHYTOSANITAIRE)

    # 2. Médical / vétérinaire
    if _contient(texte, _TERMES_MEDICAL):
        return Refus(CategorieRefus.MEDICAL)

    # 3. Identification de maladie sur image sans agent
    if _contient(texte, _TERMES_IMAGE):
        return Refus(CategorieRefus.DIAGNOSTIC_IMAGE)

    # 4. Hors filière : indice hors-sujet explicite ET aucun ancrage filière cacao.
    if _contient(texte, _TERMES_HORS_FILIERE) and not _contient(texte, _TERMES_FILIERE):
        return Refus(CategorieRefus.HORS_FILIERE)

    return None


_VALEUR_DOSEE = re.compile(r"\d+\s?(ml|l|cl|g|kg|grammes?|litres?|cc|cm3)\b")


def _contient_valeur_dosee(texte: str) -> bool:
    """Détecte un nombre suivi d'une unité de dose (ex. '50 ml', '2 litres')."""
    return bool(_VALEUR_DOSEE.search(texte))


# Taux de dose : nombre + unité de masse/volume PAR unité de volume/surface/plant.
# Signature non équivoque d'une prescription phytosanitaire (ex. « 1,25 g/L »,
# « 2 l/ha », « 50 ml par litre »), à bannir des réponses du modèle.
_TAUX_DOSE = re.compile(
    r"\d+(?:[.,]\d+)?\s?(?:mg|g|kg|ml|cl|l|cc)\s*(?:/|\bpar\b)\s*"
    r"(?:l|ml|litres?|ha|hectares?|m2|m²|plant|plants|pied|pieds|arbres?)\b"
)


def verifier_reponse(reponse: str) -> Refus | None:
    """Garde-fou de SORTIE : bloque une réponse contenant un dosage phytosanitaire.

    Défense en profondeur : même si la question a passé l'entrée, le modèle ne doit
    jamais livrer de dosage chiffré. Déclenche sur un taux de dose explicite (g/L,
    ml/ha…) ou sur une valeur dosée associée à un terme phytosanitaire.

    Args:
        reponse: Texte généré par le modèle.

    Returns:
        Un Refus PHYTOSANITAIRE si la réponse est compromise, sinon None.
    """
    texte = _normaliser(reponse)
    if _TAUX_DOSE.search(texte) or (
        _contient_valeur_dosee(texte) and _contient(texte, _TERMES_PHYTO)
    ):
        return Refus(CategorieRefus.PHYTOSANITAIRE)
    return None
