"""Garde-fous métier : refus systématique et orientation vers l'ANADER.

Voir CLAUDE_OpenCacao.md §4.3. Chaque règle de refus dispose d'un test dédié.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from app.models.domain import CategorieRefus
from app.services import localites

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

REFUS_ZONE_INDETERMINEE = (
    "Cette localité ne figure pas parmi les zones cacaoyères que je connais, et je ne "
    "peux donc pas confirmer que le cacaoyer y est adapté. Je préfère vous le dire "
    "plutôt que d'affirmer à tort : votre agent ANADER local connaît le terrain et "
    "pourra vous le confirmer, ainsi que les variétés qui conviennent."
)

REFUS_HORS_CI = (
    "Mes conseils sont calibrés pour la Côte d'Ivoire uniquement : les variétés, le "
    "calendrier cultural, la pression des maladies et les prix diffèrent d'un pays "
    "producteur à l'autre, et je ne dispose pas de données fiables pour ce pays. Je "
    "préfère ne rien affirmer plutôt que d'y transposer un conseil ivoirien. Le mieux "
    "est de vous rapprocher du service agricole ou de la filière cacao de votre pays."
)

REFUS_TRANSFORMATION = (
    "Je me concentre sur la culture du cacao et le travail du producteur, jusqu'à la "
    "fermentation et le séchage des fèves. La transformation en chocolat — ou en "
    "beurre, poudre et pâte de cacao — relève d'un autre métier, avec ses propres "
    "exigences techniques et sanitaires. C'est une filière d'avenir pour la valeur "
    "ajoutée locale en Côte d'Ivoire : pour vous y engager, rapprochez-vous du "
    "Conseil du Café-Cacao ou d'une structure d'accompagnement à la transformation."
)

_REFUS_MESSAGES: dict[CategorieRefus, str] = {
    CategorieRefus.PHYTOSANITAIRE: REFUS_PHYTO,
    CategorieRefus.MEDICAL: REFUS_MEDICAL,
    CategorieRefus.DIAGNOSTIC_IMAGE: REFUS_DIAGNOSTIC_IMAGE,
    CategorieRefus.HORS_FILIERE: REFUS_HORS_FILIERE,
    CategorieRefus.ZONE_NON_CACAO: REFUS_ZONE_NON_CACAO,
    CategorieRefus.ZONE_INDETERMINEE: REFUS_ZONE_INDETERMINEE,
    CategorieRefus.HORS_CI: REFUS_HORS_CI,
    CategorieRefus.TRANSFORMATION: REFUS_TRANSFORMATION,
}

# Refus pour lesquels l'ANADER (agence ivoirienne) n'a PAS de sens : un demandeur qui
# interroge sur un autre pays producteur ne la connaît pas et n'en relève pas. Ces refus
# ne doivent donc pas déclencher l'ajout d'un contact ANADER.
_CATEGORIES_SANS_ANADER: frozenset[CategorieRefus] = frozenset({CategorieRefus.HORS_CI})


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

    @property
    def redirige_anader(self) -> bool:
        """Vrai si ce refus oriente vers l'ANADER ivoirienne.

        Faux pour un refus hors Côte d'Ivoire : proposer l'ANADER à un demandeur
        étranger (ex. cacao du Ghana) n'a pas de sens — il ne la connaît pas et n'en
        relève pas. Sert à ne pas enrichir ces refus d'un contact ANADER.
        """
        return self.categorie not in _CATEGORIES_SANS_ANADER


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
    # Intrants (bio inclus) dosables : sans eux, une question de dose « X g/l de
    # bouillie bordelaise » passait entre les mailles (aucun terme phyto détecté).
    # La règle exige toujours un dosage EN PLUS -> pas de faux positif sur une simple
    # mention (« carence en cuivre »).
    "bouillie bordelaise",
    "cuivre",
    "soufre",
    "neem",
    "purin",
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
    "cajou",
    "noix de cajou",
    # Café : 2e filière ivoirienne, mais distincte du cacao -> redirigée vers l'ANADER.
    # L'ancrage cacao (_TERMES_FILIERE) protège les mentions du « Conseil du Café-Cacao ».
    "cafe",
    "cafeier",
    "cafeiers",
    "vivrier",
    "vivriere",
    "vivrieres",
    "vivriers",
    # Hors-sujet manifestes. Sans eux, le modèle refuse quand même — mais APRÈS une
    # génération complète, mesurée entre 24 et 42 s de CPU plein en prod (24/07/2026).
    # Le refus par règle est immédiat. Ces termes ne sont retenus QUE s'ils n'ont
    # aucun sens en cacaoculture : « application » (phytosanitaire), « cours » (prix),
    # « président » (du Conseil du Café-Cacao) en sont volontairement exclus.
    "capitale",
    "president",
    "presidente",
    "ministre",
    "cocktail",
    "biere",
    "whisky",
    "python",
    "javascript",
    "programmation",
    "programmer",
    "coder",
    "logiciel",
    "site web",
    "site internet",
    "base de donnees",
    "algorithme",
    "chatgpt",
    "intelligence artificielle",
    "application mobile",
    "traduis",
    "traduction",
    "poeme",
    "blague",
    "chanson",
    "mathematiques",
    "visa",
    "hotel",
)

# Pays producteurs de cacao AUTRES que la Côte d'Ivoire, et leurs gentilés. Le corpus
# (ANADER, CNRA, Conseil du Café-Cacao) est strictement ivoirien : transposer un
# conseil à un autre pays reviendrait à fabriquer. Clé = forme normalisée détectée,
# valeur = nom affiché dans le message de refus.
_PAYS_HORS_CI: dict[str, str] = {
    "ghana": "le Ghana",
    "ghaneen": "le Ghana",
    "ghaneens": "le Ghana",
    "ghaneenne": "le Ghana",
    "nigeria": "le Nigeria",
    "nigerian": "le Nigeria",
    "nigerians": "le Nigeria",
    "cameroun": "le Cameroun",
    "camerounais": "le Cameroun",
    "togo": "le Togo",
    "togolais": "le Togo",
    "benin": "le Bénin",
    "beninois": "le Bénin",
    "liberia": "le Liberia",
    "liberien": "le Liberia",
    "liberiens": "le Liberia",
    "sierra leone": "la Sierra Leone",
    "guinee": "la Guinée",
    "guineen": "la Guinée",
    "guineens": "la Guinée",
    "equateur": "l'Équateur",
    "equatorien": "l'Équateur",
    "equatoriens": "l'Équateur",
    "bresil": "le Brésil",
    "bresilien": "le Brésil",
    "bresiliens": "le Brésil",
    "perou": "le Pérou",
    "peruvien": "le Pérou",
    "peruviens": "le Pérou",
    "colombie": "la Colombie",
    "colombien": "la Colombie",
    "venezuela": "le Venezuela",
    "republique dominicaine": "la République dominicaine",
    "mexique": "le Mexique",
    "indonesie": "l'Indonésie",
    "indonesien": "l'Indonésie",
    "indonesiens": "l'Indonésie",
    "malaisie": "la Malaisie",
    "papouasie": "la Papouasie-Nouvelle-Guinée",
    "philippines": "les Philippines",
    "vietnam": "le Vietnam",
    "ouganda": "l'Ouganda",
    "tanzanie": "la Tanzanie",
    "madagascar": "Madagascar",
    "sao tome": "Sao Tomé-et-Principe",
    "trinidad": "Trinité-et-Tobago",
    "haiti": "Haïti",
    "bolivie": "la Bolivie",
    "costa rica": "le Costa Rica",
    "nicaragua": "le Nicaragua",
    "honduras": "le Honduras",
    "guatemala": "le Guatemala",
    "panama": "le Panama",
    "sri lanka": "le Sri Lanka",
    "inde": "l'Inde",
    "etats-unis": "les États-Unis",
    "etats unis": "les États-Unis",
}

# Transformation industrielle / artisanale : un AUTRE métier que la production. Le
# producteur s'arrête à la fève fermentée et séchée (fermentation/séchage/écabossage
# restent dans _TERMES_FILIERE et ne figurent donc PAS ici). Ces termes déclenchent le
# refus même quand la question mentionne « cacao » ou « fève », car la transformation
# porte précisément là-dessus.
_TERMES_TRANSFORMATION = (
    "chocolat",
    "chocolats",
    "chocolaterie",
    "chocolatier",
    "beurre de cacao",
    "poudre de cacao",
    "pate de cacao",
    "masse de cacao",
    "liqueur de cacao",
    "tourteau de cacao",
    "torrefaction",
    "torrefier",
    "conchage",
    "concher",
    "temperage",
    "temperer le chocolat",
    "broyage des feves",
    "nibs",
    "grue de cacao",
)

# Mentions explicites de la Côte d'Ivoire. Leur présence neutralise la règle hors-CI :
# une comparaison « le cacao ivoirien face au Ghana » reste une question ivoirienne.
_TERMES_ANCRAGE_CI = (
    "cote d'ivoire",
    "cote divoire",
    "cote d ivoire",
    "ivoirien",
    "ivoirienne",
    "ivoiriens",
    "ivoiriennes",
    "rci",
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
_RE_ANCRAGE_CI = _compiler(_TERMES_ANCRAGE_CI)
_RE_TRANSFORMATION = _compiler(_TERMES_TRANSFORMATION)
# Ordre décroissant de longueur : « sierra leone » prime sur « guinee » dans un texte
# qui contiendrait les deux, et le nom affiché reste le plus spécifique.
_RE_PAYS_HORS_CI: tuple[tuple[re.Pattern, str], ...] = tuple(
    (re.compile(rf"\b{re.escape(terme)}\b"), nom)
    for terme, nom in sorted(_PAYS_HORS_CI.items(), key=lambda p: len(p[0]), reverse=True)
)


def _pays_hors_ci_detecte(texte: str) -> str | None:
    """Nom affiché du premier pays producteur étranger cité, ou None."""
    for motif, nom in _RE_PAYS_HORS_CI:
        if motif.search(texte):
            return nom
    return None


def _ancrage_ivoirien(texte: str) -> bool:
    """Vrai si le texte rattache explicitement la question à la Côte d'Ivoire.

    Trois signaux, dans l'ordre du moins au plus coûteux : une mention directe du pays
    ou du gentilé, une localité cacaoyère ivoirienne, ou une localité de savane du Nord
    (auquel cas la règle « zone non cacaoyère », plus utile au producteur, s'applique).
    Réutilise ``localites`` — source unique, tolérante aux fautes de frappe.
    """
    return (
        _contient(texte, _RE_ANCRAGE_CI)
        or localites.detecter(texte) is not None
        or localites.detecter_nord(texte) is not None
    )


def _message_pays(nom: str) -> str:
    """Message de refus hors-CI nommant le pays détecté."""
    return REFUS_HORS_CI.replace("ce pays", nom, 1)


def _localite_nord_detectee(texte: str) -> str | None:
    """Renvoie le nom d'affichage d'une localité de savane du Nord citée, ou None.

    Délègue au module partagé ``localites`` (source unique) : la détection y est
    tolérante aux fautes de frappe (« korohgo » -> Korhogo), pour que le garde-fou
    « zone non cacaoyère » ne soit pas contourné par une coquille de saisie.
    """
    return localites.detecter_nord(texte)


def _message_zone(nom: str) -> str:
    """Message de correction « zone non cacaoyère » nommant la localité détectée."""
    return REFUS_ZONE_NON_CACAO.replace("Cette localité", nom, 1)


def _contient(texte: str, motifs: tuple[re.Pattern, ...]) -> bool:
    """Vrai si l'un des motifs (à frontière de mot) apparaît dans le texte normalisé."""
    return any(m.search(texte) for m in motifs)


def evaluer(question: str, *, courante: str | None = None) -> Refus | None:
    """Évalue une question et retourne un refus si une règle s'applique.

    Ordre de priorité : phytosanitaire, médical/vétérinaire, diagnostic sur image,
    hors-filière. Retourne None si la question peut être traitée par le modèle.

    **Deux familles de règles, et la distinction n'est pas cosmétique.** L'appelant
    passe ici le FIL (dernier tour + question courante, cf. ``contexte.fil_ancre``)
    pour que les règles de PROTECTION attrapent une intention étalée sur deux tours
    (« je traite au fongicide » puis « quelle dose ? »). Mais les règles de
    CORRECTION D'UNE PRÉMISSE — zone non cacaoyère, pays hors Côte d'Ivoire — n'ont
    aucune raison de survivre au changement de sujet : appliquées au fil, elles
    resservaient la réponse du tour précédent à une question sans rapport.

    Incident du 19/08/2026 : « plantation à Katiola ? » puis « c'est quoi le FIRCA ? »
    renvoyait deux fois la correction sur Katiola. Devant un public, c'est le genre de
    défaut qui décrédibilise tout le reste. Ces règles lisent donc ``courante``.

    Args:
        question: Texte évalué par les règles de protection (le fil, en multi-tours).
        courante: Question du tour en cours, pour les règles de correction. Par
            défaut, ``question`` — les appelants mono-tour n'ont rien à changer.

    Returns:
        Un objet Refus si un garde-fou se déclenche, sinon None.
    """
    texte = _normaliser(question)
    texte_courant = _normaliser(courante) if courante is not None else texte

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

    # 3 bis. Transformation (chocolat, beurre/poudre de cacao, torréfaction…) : un autre
    #        métier que la production. Vérifié AVANT le hors-filière car ces questions
    #        parlent bien de cacao — l'ancrage filière ne doit pas les laisser passer.
    if _contient(texte, _RE_TRANSFORMATION):
        return Refus(CategorieRefus.TRANSFORMATION)

    # 4. Hors filière : indice hors-sujet explicite ET aucun ancrage filière cacao.
    if _contient(texte, _RE_HORS_FILIERE) and not _contient(texte, _RE_FILIERE):
        return Refus(CategorieRefus.HORS_FILIERE)

    # 5. Localité de savane du Nord + intention de culture du cacao : on corrige
    #    (ce n'est PAS une zone cacaoyère) plutôt que de laisser le modèle l'affirmer.
    # La LOCALITÉ peut venir du tour précédent (« je suis à Katiola » puis « je veux y
    # planter ») : c'est le dialogue normal, on ne la fait pas répéter. Mais
    # l'INTENTION de culture doit être dans la question courante, sinon la correction
    # s'accroche au fil et répond à une question qui n'est plus posée.
    nord = _localite_nord_detectee(texte)
    if nord is not None and _contient(texte_courant, _RE_ZONE_DECLENCHEUR):
        return Refus(CategorieRefus.ZONE_NON_CACAO, message=_message_zone(nord))

    # 5 bis. Localité citée mais dont l'aptitude cacaoyère n'est PAS établie (zones de
    #        transition des DR Centre et Centre-Est, par exemple). Avant ce garde-fou,
    #        tout ce qui n'était pas explicitement refusé était présumé cacaoyer, et le
    #        modèle l'affirmait : « À Ouangolo, comme dans toute la zone forestière du
    #        Sud, le cacaoyer peut bien pousser » — sur une localité de l'extrême nord
    #        (écart constaté en production le 19/08/2026). On préfère dire qu'on ne
    #        sait pas : c'est la même doctrine que partout ailleurs dans ce projet.
    verdict = localites.aptitude_cacao(texte)
    if (
        verdict is not None
        and verdict[0] is localites.Aptitude.INDETERMINE
        and _contient(texte_courant, _RE_ZONE_DECLENCHEUR)
    ):
        return Refus(CategorieRefus.ZONE_INDETERMINEE)

    # 6. Cacao d'un AUTRE pays producteur : le corpus est strictement ivoirien, donc
    #    répondre reviendrait à transposer en silence. On ne refuse que si rien
    #    n'ancre la question en Côte d'Ivoire (mention du pays ou localité citée).
    # Même raisonnement : corriger la prémisse d'une question PASSÉE (« le cacao au
    # Ghana ») sur la question suivante n'a pas de sens. On lit le tour en cours.
    pays = _pays_hors_ci_detecte(texte_courant)
    if pays is not None and not _ancrage_ivoirien(texte_courant):
        return Refus(CategorieRefus.HORS_CI, message=_message_pays(pays))

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


def contient_prescription(texte: str) -> bool:
    """Indique si un texte PRESCRIT un traitement chiffré.

    Plus étroit que :func:`verifier_reponse`, et c'est délibéré. Le garde-fou de
    sortie du conseil se déclenche sur tout taux de dose, ``kg/ha`` compris : pour un
    producteur, un faux positif ne coûte qu'une redirection vers l'ANADER. Dans un
    **document d'analyse**, un rendement en kilogrammes par hectare est au contraire
    le chiffre le plus banal qui soit — l'y refuser viderait toute section agronomique
    de sa substance.

    Ce qui reste interdit partout, c'est la **prescription** : un dosage associé à un
    produit ou à un geste de traitement.

    Args:
        texte: Texte à vérifier.

    Returns:
        ``True`` si le texte prescrit un traitement chiffré.
    """
    normalise = _normaliser(texte)
    if _DILUTION.search(normalise):
        return True
    return bool(_TAUX_DOSE.search(normalise)) and _contient(normalise, _RE_PHYTO)


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


# Noms de maladies et de ravageurs du cacaoyer. Les prononcer, c'est diagnostiquer —
# ce que D3 interdit tant que le verrou de rappel par classe n'est pas franchi
# (spec §7.5). On décrit un symptôme observé ; on ne nomme jamais sa cause.
_NOMS_MALADIES = (
    "pourriture brune",
    "phytophthora",
    "swollen shoot",
    "swollen-shoot",
    "cssv",
    "mirides",
    "miride",
    "capsides",
    "anthracnose",
    "fusariose",
    "armillaire",
    "moniliose",
    "balai de sorciere",
)

# Un constat ne nomme pas davantage un PRODUIT qu'une maladie (D3) : « appliquez un
# fongicide » est déjà une prescription, même sans chiffre. Le vocabulaire phyto
# existant sert de source unique — ``verifier_reponse`` couvre le versant chiffré.
_TERMES_INTERDITS_CONSTAT = _NOMS_MALADIES + _TERMES_PHYTO

_RE_INTERDITS_CONSTAT = _compiler(_TERMES_INTERDITS_CONSTAT)


def contient_diagnostic(texte: str) -> str | None:
    """Retourne le terme interdit trouvé dans le texte, ou None.

    Garde-fou de sortie propre au constat visuel (D3, arbitrage du 28/07/2026) : le
    système décrit ce qu'il observe, ne nomme jamais la cause et ne prescrit aucun
    produit. Un constat qui franchit l'un de ces interdits est rejeté, pas corrigé —
    on ne réécrit pas une sortie compromise.

    Args:
        texte: Texte du constat à vérifier.

    Returns:
        Le terme fautif (pour la journalisation), ou ``None`` si le texte est sain.
    """
    normalise = _normaliser(texte)
    for terme, motif in zip(_TERMES_INTERDITS_CONSTAT, _RE_INTERDITS_CONSTAT, strict=True):
        if motif.search(normalise):
            return terme
    return None
