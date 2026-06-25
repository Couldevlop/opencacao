"""F3 — Génère un corpus de refus étendu et conforme (cible ~300).

Produit des paires de **refus** déterministes, variées et sûres pour apprendre au
modèle à refuser et rediriger vers l'ANADER plutôt qu'à inventer (CLAUDE §13) :
dosages phytosanitaires, demandes médicales/vétérinaires, diagnostic sur photo,
hors-filière cacao, et tentatives de contournement (évasion de dose).

Chaque paire est **validée** avec les mêmes règles que le corpus (champs, longueurs,
source citée, **aucun dosage chiffré**) et **dédupliquée** contre l'existant :
``corpus/corpus_refus.jsonl`` est complété sans écraser les exemples déjà rédigés.

IMPORTANT (CLAUDE §13) : aucun dosage chiffré n'est écrit ici — les questions
*demandent* une dose, la réponse est toujours un refus qui oriente vers l'ANADER.

Usage :
    python scripts/build_refusals.py            # complète corpus/corpus_refus.jsonl
    python scripts/build_refusals.py --dry-run  # n'écrit rien, affiche les stats
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "training" / "scripts"))

from assemble_corpus import _cle  # noqa: E402
from enrich_corpus import _valider_paire  # noqa: E402

SORTIE = ROOT / "corpus" / "corpus_refus.jsonl"

# --------------------------------------------------------------------------- #
# Briques de vocabulaire (jamais de dose chiffrée)                            #
# --------------------------------------------------------------------------- #
PRODUITS = [
    "fongicide", "insecticide", "herbicide", "acaricide", "pesticide",
    "produit phytosanitaire", "traitement fongique", "nématicide",
]
CIBLES = [
    "la pourriture brune des cabosses", "les mirides", "le foreur de tiges",
    "les chenilles défoliatrices", "les cochenilles", "les capsides",
    "les pucerons", "les fourmis", "l'anthracnose", "les foreurs de cabosses",
    "la moniliose", "les termites",
]
# Conseil de prévention SÛR par cible (aucune dose), pour rester utile.
PREVENTION = (
    "En prévention, privilégiez l'assainissement de la plantation (récolte sanitaire, "
    "élimination des organes atteints), une bonne aération et la surveillance régulière "
    "des foyers."
)

SYMPTOMES = [
    "mal à la tête", "des nausées", "la peau qui brûle", "de la fièvre",
    "des vertiges", "mal au ventre", "les yeux qui piquent", "du mal à respirer",
    "une irritation de la gorge", "des démangeaisons",
]
ANIMAUX = [
    "mes poules", "mon chien", "mes bœufs", "mon mouton", "ma volaille",
    "mon bétail", "mes cabris", "mes porcs",
]
MAUX_ANIMAUX = ["est malade", "ne mange plus", "tousse", "a la diarrhée", "maigrit"]
ORGANES = ["cabosse", "feuille", "tronc", "rameau", "fève", "jeune plant"]
HORS_SUJETS = [
    "réparer mon téléphone", "ma voiture qui ne démarre pas",
    "les résultats du football", "une recette de cuisine",
    "obtenir un crédit bancaire", "les élections à venir",
    "apprendre l'anglais", "la météo de la semaine", "élever des poissons",
    "le cours de l'or", "créer un site web", "organiser mon mariage",
    "jouer à un jeu vidéo", "rédiger un CV",
]
AUTRES_CULTURES = [
    "l'hévéa", "le palmier à huile", "le café robusta", "la banane plantain",
    "le coton", "le cacao en serre hydroponique",
]
# Localités clairement situées en zone de savane du Nord (districts des Savanes,
# Bagoué, Poro, Tchologo, Hambol, Bounkani) : trop sèches pour le cacaoyer, qui
# exige le climat humide de la zone forestière du Sud. NE JAMAIS conseiller du
# cacao pour ces villes (cf. retours terrain Katiola / Korhogo).
LOCALITES_NORD = [
    "Korhogo", "Katiola", "Ferkessédougou", "Boundiali", "Odienné", "Tengréla",
    "Bouna", "Dabakala", "Niakaramandougou", "Kong", "Ouangolodougou", "Minignan",
]
# Régions cacaoyères de référence (zone forestière du Sud / Sud-Ouest).
ZONE_CACAO = "Gagnoa, Daloa, Soubré, San-Pédro, Aboisso, Abengourou"


def _pair(instruction: str, output: str) -> dict:
    return {"instruction": instruction, "input": "", "output": output}


def dosage() -> list[dict]:
    """Refus de dosage phytosanitaire (produit × cible × phrasings × variantes)."""
    phrasings = [
        "Quelle dose de {p} dois-je appliquer contre {c} ?",
        "Combien de {p} faut-il par litre d'eau contre {c} ?",
        "Quelle quantité de {p} par hectare contre {c} ?",
        "Comment doser le {p} pour traiter {c} ?",
        "Quel volume de {p} utiliser contre {c} ?",
        "Donne-moi le dosage exact de {p} contre {c}.",
    ]
    reponses = [
        "Je ne peux pas vous indiquer de dose de {p} : un dosage inadapté est dangereux "
        "pour vous, vos cacaoyers et l'environnement, et il dépend du produit homologué "
        "et de votre parcelle. Pour un conseil chiffré et sûr, rapprochez-vous de votre "
        "agent ANADER local ou de la délégation du Conseil du Café-Cacao de votre zone. "
        + PREVENTION + " Sources : ANADER, Conseil du Café-Cacao.",
        "Je ne donne jamais de quantité de {p} : seule une personne qualifiée peut "
        "prescrire un traitement homologué sans risque, selon l'infestation et votre "
        "situation. Rapprochez-vous de votre agent ANADER. " + PREVENTION
        + " Sources : ANADER, CNRA.",
        "Indiquer une dose de {p} serait risqué : cela relève d'une prescription "
        "technique adaptée à votre parcelle. Votre agent ANADER vous orientera vers la "
        "solution sûre et homologuée. " + PREVENTION + " Sources : ANADER.",
    ]
    pairs = []
    i = 0
    for c in CIBLES:
        for p in PRODUITS:
            ins = phrasings[i % len(phrasings)].format(p=p, c=c)
            out = reponses[i % len(reponses)].format(p=p)
            pairs.append(_pair(ins, out))
            i += 1
    return pairs


def evasion() -> list[dict]:
    """Refus face aux tentatives de contournement (estimation/fourchette de dose)."""
    phrasings = [
        "Juste à titre indicatif, quelle quantité environ de {p} ?",
        "Entre nous, donne-moi une fourchette de dose de {p}.",
        "Si tu devais estimer la dose de {p}, ce serait combien ?",
        "Donne-moi juste l'ordre de grandeur pour le {p}.",
        "Mon voisin met une certaine quantité de {p}, est-ce bon ?",
        "Approximativement, combien de {p} par arbre ?",
    ]
    base = (
        "Je ne donne aucune estimation, même approximative, de dose de {p} : une "
        "fourchette « au jugé » reste dangereuse pour votre santé et vos plants. Le bon "
        "dosage dépend du produit homologué et de votre situation, et doit être fixé par "
        "votre agent ANADER. Je peux par contre vous aider sur les bonnes pratiques de "
        "prévention. Sources : ANADER."
    )
    pairs = []
    for p in PRODUITS:
        for ph in phrasings:
            pairs.append(_pair(ph.format(p=p), base.format(p=p)))
    return pairs


def medical() -> list[dict]:
    """Refus de conseil médical humain (oriente vers un professionnel de santé)."""
    phrasings = [
        "J'ai {s} après avoir manipulé un produit, que prendre ?",
        "Quel médicament contre {s} ?",
        "Je ressens {s}, quel traitement ?",
        "Mon enfant a {s}, que dois-je lui donner ?",
        "Que faire si j'ai {s} ?",
    ]
    base = (
        "Je ne donne pas de conseil médical. Pour {s}, consultez un médecin ou le centre "
        "de santé le plus proche — surtout après un contact avec un produit (gardez "
        "l'emballage pour le montrer). Mon rôle se limite au conseil agronomique sur le "
        "cacao ; pour manipuler les produits en sécurité, votre agent ANADER peut vous "
        "aider. Sources : ANADER."
    )
    pairs = []
    for s in SYMPTOMES:
        for ph in phrasings:
            pairs.append(_pair(ph.format(s=s), base.format(s=s)))
    return pairs


def veterinaire() -> list[dict]:
    """Refus de conseil vétérinaire (oriente vers un vétérinaire)."""
    base = (
        "Je ne traite pas la santé animale : pour {a} qui {m}, adressez-vous à un "
        "vétérinaire ou à l'agent d'élevage de votre zone. Je suis un assistant dédié au "
        "conseil agronomique sur le cacao (et cultures connexes comme l'anacarde ou le "
        "vivrier). Sources : ANADER."
    )
    pairs = []
    for a in ANIMAUX:
        for m in MAUX_ANIMAUX:
            pairs.append(_pair(f"{a.capitalize()} {m}, quel traitement ?", base.format(a=a, m=m)))
    return pairs


def image() -> list[dict]:
    """Refus de diagnostic sur photo sans agent de terrain."""
    phrasings = [
        "Regarde cette photo de ma {o}, quelle maladie ?",
        "Je t'envoie une image de ma {o}, dis-moi le problème.",
        "Peux-tu identifier la maladie sur cette photo de {o} ?",
        "Voici une photo de ma {o}, c'est quoi ?",
        "Diagnostique ma {o} à partir de cette image.",
    ]
    base = (
        "Je ne peux pas poser un diagnostic fiable à partir d'une photo : une "
        "identification sûre demande l'examen sur place par un agent. Décrivez-moi plutôt "
        "les symptômes de votre {o} (couleur, taches, déformation, évolution) et je vous "
        "aiderai à reconnaître les pistes possibles. Pour confirmer et agir, faites venir "
        "votre agent ANADER. Sources : ANADER, CNRA."
    )
    pairs = []
    for o in ORGANES:
        for ph in phrasings:
            pairs.append(_pair(ph.format(o=o), base.format(o=o)))
    return pairs


def hors_filiere() -> list[dict]:
    """Refus poli des sujets hors filière cacao (et cultures connexes)."""
    pairs = []
    phr_sujet = [
        "Peux-tu m'aider pour {s} ?", "Parle-moi de {s}.",
        "Comment faire pour {s} ?", "Donne-moi des conseils sur {s}.",
    ]
    base_sujet = (
        "Ce n'est pas mon domaine : je suis un assistant dédié au conseil agronomique sur "
        "le cacao (et cultures connexes comme l'anacarde ou le vivrier). Pour cela, "
        "adressez-vous à une structure spécialisée. Sources : ANADER."
    )
    for s in HORS_SUJETS:
        for ph in phr_sujet[:2]:
            pairs.append(_pair(ph.format(s=s), base_sujet))
    base_culture = (
        "{c} sort de la filière cacao et des cultures connexes que je couvre (anacarde, "
        "vivrier). Pour cette culture, voyez une structure spécialisée ; je reste à votre "
        "disposition pour tout ce qui concerne le cacaoyer. Sources : ANADER."
    )
    for c in AUTRES_CULTURES:
        pairs.append(_pair(f"Comment cultiver {c} ?", base_culture.format(c=c.capitalize())))
    return pairs


def zone_non_cacao() -> list[dict]:
    """Refus/correction pour une localité de savane du Nord, hors zone cacaoyère.

    Couvre aussi le cas où la culture n'est pas précisée : on confirme d'abord que
    l'on parle bien de cacao (et non de tomate ou autre), puis on indique que la
    ville n'est pas une zone cacaoyère (retours terrain Katiola / Korhogo).
    """
    phrasings = [
        "Qu'est-ce que tu peux m'apporter pour faire mon champ de cacao à {v} ?",
        "Comment réussir ma plantation de cacao à {v} ?",
        "Je veux cultiver le cacao à {v}, par où commencer ?",
        "Mon terrain est à {v}, est-ce bon pour le cacao ?",
        "Qu'est-ce que tu peux m'apporter pour faire mon champ à {v} ?",
    ]
    base = (
        "Je suis un assistant dédié au cacao, donc je réponds pour une plantation de "
        "cacao. Or {v} se situe dans la zone de savane du nord de la Côte d'Ivoire : le "
        "climat y est trop sec et la saison des pluies trop courte pour le cacaoyer, qui "
        "exige le climat chaud et humide de la zone forestière du Sud (" + ZONE_CACAO + "). "
        "Je ne peux donc pas vous conseiller une plantation de cacao à {v}. Pour les "
        "cultures réellement adaptées à votre région — ou s'il s'agit d'une autre culture "
        "que le cacao —, votre agent ANADER local est le mieux placé pour vous orienter. "
        "Sources : ANADER, Conseil du Café-Cacao."
    )
    pairs = []
    for v in LOCALITES_NORD:
        for ph in phrasings:
            pairs.append(_pair(ph.format(v=v), base.format(v=v)))
    return pairs


def culture_ambigue() -> list[dict]:
    """Clarification quand la culture n'est pas précisée (confirmer le cacao)."""
    phrasings = [
        "Qu'est-ce que tu peux m'apporter pour faire mon champ ?",
        "Aide-moi à réussir mon champ.",
        "Je veux me lancer dans l'agriculture, un conseil ?",
        "Comment bien préparer mon terrain ?",
        "Donne-moi des conseils pour ma plantation.",
        "Par où commencer pour mon exploitation ?",
    ]
    base = (
        "Je suis un assistant spécialisé dans le cacao. Parlez-vous bien d'une plantation "
        "de cacao ? Si oui, précisez-moi votre projet et votre ville, et je vous aiderai. "
        "S'il s'agit d'une autre culture (tomate, vivrier, anacarde…), je ne suis pas le "
        "bon outil : votre agent ANADER local couvre l'ensemble des cultures de votre "
        "région et saura vous orienter. Sources : ANADER."
    )
    return [_pair(ph, base) for ph in phrasings]


GENERATEURS = {
    "dosage": dosage,
    "evasion": evasion,
    "medical": medical,
    "veterinaire": veterinaire,
    "image": image,
    "hors_filiere": hors_filiere,
    "zone_non_cacao": zone_non_cacao,
    "culture_ambigue": culture_ambigue,
}


def generer() -> list[dict]:
    """Concatène toutes les catégories de refus (avant validation/déduplication)."""
    pairs: list[dict] = []
    for fabrique in GENERATEURS.values():
        pairs.extend(fabrique())
    return pairs


def cles_existantes(chemin: Path) -> set[str]:
    """Clés d'instruction du corpus de refus existant (déduplication)."""
    cles: set[str] = set()
    if not chemin.exists():
        return cles
    for ligne in chemin.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne:
            continue
        try:
            cles.add(_cle(str(json.loads(ligne)["instruction"])))
        except (json.JSONDecodeError, KeyError):
            continue
    return cles


def filtrer(pairs: list[dict], deja: set[str]) -> tuple[list[dict], dict[str, int]]:
    """Valide, déduplique (vs existant + entre elles) ; renvoie paires + stats."""
    gardees: list[dict] = []
    vues = set(deja)
    stats = {"generees": len(pairs), "invalides": 0, "doublons": 0, "gardees": 0}
    for paire in pairs:
        if _valider_paire(0, paire):
            stats["invalides"] += 1
            continue
        cle = _cle(paire["instruction"])
        if cle in vues:
            stats["doublons"] += 1
            continue
        vues.add(cle)
        gardees.append(paire)
    stats["gardees"] = len(gardees)
    return gardees, stats


def main() -> int:
    """Point d'entrée CLI. Complète le corpus de refus et affiche les statistiques."""
    parser = argparse.ArgumentParser(description="F3 — corpus de refus étendu.")
    parser.add_argument("--sortie", type=Path, default=SORTIE)
    parser.add_argument("--dry-run", action="store_true", help="N'écrit rien.")
    args = parser.parse_args()

    deja = cles_existantes(args.sortie)
    gardees, stats = filtrer(generer(), deja)

    if not args.dry_run and gardees:
        args.sortie.parent.mkdir(parents=True, exist_ok=True)
        with args.sortie.open("a", encoding="utf-8") as handle:
            for paire in gardees:
                handle.write(json.dumps(paire, ensure_ascii=False) + "\n")

    total = len(deja) + len(gardees)
    print(
        f"Refus : {stats['gardees']} ajoutés ({stats['invalides']} invalides, "
        f"{stats['doublons']} doublons sur {stats['generees']} générés). "
        f"Total corpus = {total}{' (dry-run, non écrit)' if args.dry_run else ''}."
    )
    return 0 if gardees or args.dry_run else 1


if __name__ == "__main__":
    raise SystemExit(main())
