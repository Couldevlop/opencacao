"""Garde-fous métier : refus systématique et orientation vers l'ANADER.

Voir CLAUDE_OpenCacao.md §4.3. Chaque règle de refus dispose d'un test dédié.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from app.models.domain import CategorieRefus
from app.services.localites import LOCALITES_NORD

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
    "Je suis spécialisé UNIQUEMENT dans la filière cacao. Votre question porte sur "
    "une autre culture ou un autre domaine ; pour cela, votre agent ANADER local "
    "couvre l'ensemble des cultures de votre région et pourra vous orienter."
)

REFUS_ZONE_NON_CACAO = (
    "Cette localité se situe dans la zone de savane du nord de la Côte d'Ivoire, au "
    "climat trop sec et à la saison des pluies trop courte pour le cacaoyer, qui a "
    "besoin du climat chaud et humide de la zone forestière du Sud (la « boucle du "
    "cacao » : Gagnoa, Daloa, Soubré, San-Pédro, Aboisso…). Le cacao n'y est donc pas "
    "adapté. Pour les cultures qui conviennent à votre région, votre agent ANADER "
    "local pourra vous orienter."
)

_REFUS_MESSAGES: dict[CategorieRefus, str] = {
    CategorieRefus.PHYTOSANITAIRE: REFUS_PHYTO,
    CategorieRefus.MEDICAL: REFUS_MEDICAL,
    CategorieRefus.DIAGNOSTIC_IMAGE: REFUS_DIAGNOSTIC_IMAGE,
    CategorieRefus.HORS_FILIERE: REFUS_HORS_FILIERE,
    CategorieRefus.ZONE_NON_CACAO: REFUS_ZONE_NON_CACAO,
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
    "desherbant",
    "acaricide",
    "nematicide",
    "engrais",
    "produit chimique",
    "produit de traitement",
    "traitement chimique",
)

_TERMES_DOSAGE = (
    "dose",
    "doser",
    "dosage",
    "quelle quantite",
    "combien de",
    "combien d",
    "combien faut il",
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
    "diluer",
    "dilution",
    "concentration",
)

# Médical et vétérinaire (santé humaine ou animale) — hors champ agronomique.
_TERMES_MEDICAL = (
    "maladie humaine",
    "ma sante",
    "mal de tete",
    "maux de tete",
    "mal a la tete",
    "fievre",
    "grippe",
    "paludisme",
    "palu",
    "toux",
    "tousse",
    "blessure",
    "douleur",
    "diarrhee",
    "vomir",
    "vomis",
    "medicament",
    "comprime",
    "ordonnance",
    "docteur",
    "medecin",
    "pharmacie",
    "veterinaire",
    "vaccin",
    "vacciner",
    "mon chien",
    "mon chat",
    "ma vache",
    "mes poules",
    "ma poule",
    "mon mouton",
    "ma chevre",
    "mon boeuf",
    "mon betail",
    "ma brebis",
    "mon porc",
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

# Périmètre STRICTEMENT cacao (décision Waopron, juin 2026) : toute autre culture,
# y compris le vivrier et l'anacarde, est désormais hors champ et redirigée vers
# l'ANADER. On garde des termes propres au cacao et à la conduite du verger (les
# mentions du cacaoyer doivent toujours être reconnues, même au pluriel/féminin).
_TERMES_FILIERE = (
    "cacao",
    "cacaoyer",
    "cacaoyere",
    "cacaoyeres",
    "cacaoyers",
    "cabosse",
    "cabosses",
    "feve",
    "feves",
    "fermentation",
    "swollen shoot",
    "cssv",
    "miride",
    "mirides",
    "capside",
    "capsides",
    "phytophthora",
    "pourriture brune",
    "verger",
    "pepiniere",
    "ombrage",
    "sechage",
    "recolte",
    "plantation",
    "anader",
)

# Indices forts qu'une question vise une AUTRE culture (vivrier, anacarde, autres
# filières) ou un tout autre domaine. Le refus hors-filière ne se déclenche que si
# aucun ancrage cacao n'est présent (cf. evaluer) : une question d'ombrage ou
# d'association mentionnant le cacao reste donc traitée.
_TERMES_HORS_FILIERE = (
    "bitcoin",
    "crypto",
    "football",
    "telephone",
    "smartphone",
    "ordinateur",
    "internet",
    "voiture",
    "elections",
    "politique",
    "religion",
    "banque",
    "recette de cuisine",
    "musique",
    "hevea",
    "palmier a huile",
    "coton",
    "tabac",
    "elevage",
    "poisson",
    "aquaculture",
    # Autres cultures (vivrier, anacarde, fruitiers) — hors champ cacao.
    "mais",
    "manioc",
    "igname",
    "ignames",
    "banane",
    "bananes",
    "plantain",
    "plantains",
    "taro",
    "tomate",
    "tomates",
    "gombo",
    "gombos",
    "piment",
    "piments",
    "riz",
    "arachide",
    "arachides",
    "mil",
    "sorgho",
    "ananas",
    "mangue",
    "mangues",
    "manguier",
    "manguiers",
    "anacarde",
    "anacardes",
    "anacardier",
    "anacardiers",
    "vivrier",
    "vivriere",
    "vivrieres",
    "vivriers",
)


# Termes signalant une intention de CULTURE de cacao (vs simple mention de la ville,
# ex. demande de contact ANADER) : la correction ne se déclenche que si l'un apparaît.
_TERMES_ZONE_DECLENCHEUR = (
    "cacao",
    "cacaoyer",
    "cacaoyere",
    "cultiver",
    "culture",
    "planter",
    "plantation",
    "champ",
    "pousser",
    "propice",
    "adapte",
    "convient",
)


def _normaliser(texte: str) -> str:
    """Minuscule + suppression des accents pour une détection robuste."""
    sans_accents = "".join(
        c for c in unicodedata.normalize("NFD", texte) if unicodedata.category(c) != "Mn"
    )
    return sans_accents.lower()


def _compiler(termes: tuple[str, ...]) -> tuple[re.Pattern, ...]:
    """Compile chaque terme en motif à frontière de mot (évite les faux positifs).

    La frontière ``\\b`` empêche par exemple « riz » de matcher « prix », ou « palu »
    de matcher « paludéen » au milieu d'un autre mot.
    """
    return tuple(re.compile(rf"\b{re.escape(t)}\b") for t in termes)


_RE_PHYTO = _compiler(_TERMES_PHYTO)
_RE_DOSAGE = _compiler(_TERMES_DOSAGE)
_RE_MEDICAL = _compiler(_TERMES_MEDICAL)
_RE_IMAGE = _compiler(_TERMES_IMAGE)
_RE_FILIERE = _compiler(_TERMES_FILIERE)
_RE_HORS_FILIERE = _compiler(_TERMES_HORS_FILIERE)
_RE_ZONE_DECLENCHEUR = _compiler(_TERMES_ZONE_DECLENCHEUR)
_RE_LOCALITES_NORD = tuple(
    (re.compile(rf"\b{re.escape(cle)}\b"), nom) for cle, nom in LOCALITES_NORD.items()
)


def _localite_nord_detectee(texte: str) -> str | None:
    """Renvoie le nom d'affichage d'une localité de savane du Nord citée, ou None."""
    for motif, nom in _RE_LOCALITES_NORD:
        if motif.search(texte):
            return nom
    return None


def _message_zone(nom: str) -> str:
    """Message de correction « zone non cacaoyère » nommant la localité détectée."""
    return REFUS_ZONE_NON_CACAO.replace("Cette localité", nom, 1)


def _contient(texte: str, motifs: tuple[re.Pattern, ...]) -> bool:
    """Vrai si l'un des motifs (à frontière de mot) apparaît dans le texte normalisé."""
    return any(m.search(texte) for m in motifs)


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
    if _contient(texte, _RE_PHYTO) and (
        _contient(texte, _RE_DOSAGE) or _contient_valeur_dosee(texte)
    ):
        return Refus(CategorieRefus.PHYTOSANITAIRE)

    # 2. Médical / vétérinaire
    if _contient(texte, _RE_MEDICAL):
        return Refus(CategorieRefus.MEDICAL)

    # 3. Identification de maladie sur image sans agent
    if _contient(texte, _RE_IMAGE):
        return Refus(CategorieRefus.DIAGNOSTIC_IMAGE)

    # 4. Hors filière : indice hors-sujet explicite ET aucun ancrage filière cacao.
    if _contient(texte, _RE_HORS_FILIERE) and not _contient(texte, _RE_FILIERE):
        return Refus(CategorieRefus.HORS_FILIERE)

    # 5. Localité de savane du Nord + intention de culture du cacao : on corrige
    #    (ce n'est PAS une zone cacaoyère) plutôt que de laisser le modèle l'affirmer.
    nord = _localite_nord_detectee(texte)
    if nord is not None and _contient(texte, _RE_ZONE_DECLENCHEUR):
        return Refus(CategorieRefus.ZONE_NON_CACAO, message=_message_zone(nord))

    return None


_VALEUR_DOSEE = re.compile(r"\d+\s?(ml|l|cl|g|kg|grammes?|litres?|cc|cm3)\b")


def _contient_valeur_dosee(texte: str) -> bool:
    """Détecte un nombre suivi d'une unité de dose (ex. '50 ml', '2 litres')."""
    return bool(_VALEUR_DOSEE.search(texte))


# Taux de dose : nombre + unité de masse/volume PAR unité de volume/surface/plant.
# Signature non équivoque d'une prescription phytosanitaire (ex. « 1,25 g/L »,
# « 2 l/ha », « 50 ml par litre »), à bannir des réponses du modèle.
_TAUX_DOSE = re.compile(
    r"\d+(?:[.,]\d+)?\s?(?:mg|g|kg|ml|cl|l|cc|hl)\s*(?:/|\bpar\b)\s*"
    r"(?:l|ml|litres?|hl|ha|hectares?|m2|m²|plant|plants|pied|pieds|arbres?|"
    r"pulverisateur|pulverisateurs|arrosoir)\b"
)

# Dilution : « 50 ml dans 10 litres », « 2 bouchons pour un arrosoir »… autre forme
# courante d'une prescription chiffrée à bannir d'une réponse.
_DILUTION = re.compile(
    r"\d+(?:[.,]\d+)?\s?(?:mg|g|kg|ml|cl|l|cc|bouchons?|sachets?|capsules?|cuilleres?)\s+"
    r"(?:dans|pour)\s+(?:un|une|\d+)?\s?"
    r"(?:l\b|litres?|ml|pulverisateur|pulverisateurs|arrosoir|bidon|seau)"
)


def verifier_reponse(reponse: str) -> Refus | None:
    """Garde-fou de SORTIE : bloque une réponse contenant un dosage phytosanitaire.

    Défense en profondeur : même si la question a passé l'entrée, le modèle ne doit
    jamais livrer de dosage chiffré. Déclenche sur un taux de dose explicite (g/L,
    ml/ha…), une dilution chiffrée (« 50 ml dans 10 l »), ou une valeur dosée associée
    à un terme phytosanitaire.

    Args:
        reponse: Texte généré par le modèle.

    Returns:
        Un Refus PHYTOSANITAIRE si la réponse est compromise, sinon None.
    """
    texte = _normaliser(reponse)
    if (
        _TAUX_DOSE.search(texte)
        or _DILUTION.search(texte)
        or (_contient_valeur_dosee(texte) and _contient(texte, _RE_PHYTO))
    ):
        return Refus(CategorieRefus.PHYTOSANITAIRE)
    return None
