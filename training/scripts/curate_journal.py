"""F11 — Boucle d'amélioration : juge GLM-5.2 sur le journal de prod → corpus curé.

Lit le journal anonymisé de production (``interactions.jsonl`` + ``feedback.jsonl``,
cf. ``api/app/core/journal.py``), joint les retours 👍/👎 aux interactions, puis fait
**curer** chaque cas par un modèle-maître (GLM-5.2 par défaut) qui réécrit une réponse
de qualité OU un refus conforme. Chaque paire produite est **validée** avec les mêmes
règles que le corpus (champs, longueurs, aucun dosage chiffré, source citée) puis
ajoutée — sans doublon — au **corpus curé** (``corpus/corpus_cure.jsonl``) qui alimente
le ré-entraînement LoRA (assemble_corpus → train_lora).

Souveraineté (CLAUDE §1.3, §13) : le maître n'intervient qu'**hors production**, comme
l'enrichissement du corpus et l'évaluation F1 — jamais dans le service. Le journal est
déjà anonymisé (aucune IP, aucune donnée personnelle).

Usage :
    ZAI_API_KEY=... python training/scripts/curate_journal.py \\
        --journal /data --sortie corpus/corpus_cure.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from assemble_corpus import _cle
from enrich_corpus import _valider_paire
from evaluate import (
    JUGE_ENDPOINT_DEFAUT,
    JUGE_MODELE_DEFAUT,
    _joindre_endpoint,
    contient_dosage,
)

FICHIER_INTERACTIONS = "interactions.jsonl"
FICHIER_FEEDBACK = "feedback.jsonl"
ACTIONS_PAIRE = ("corriger", "refus", "garder")  # produisent une paire ; "rejeter" non.


@dataclass
class StatsCuration:
    """Compteurs de la passe de curation (jamais de rejet silencieux)."""

    cas: int = 0
    pairs: int = 0
    rejetes: int = 0
    invalides: int = 0
    doublons: int = 0
    maitre_indisponible: int = 0
    motifs: dict[str, int] = field(default_factory=dict)

    def noter_rejet(self, motif: str) -> None:
        self.motifs[motif] = self.motifs.get(motif, 0) + 1


def charger_interactions(chemin: Path) -> dict[str, dict]:
    """Charge ``interactions.jsonl`` en dict indexé par identifiant d'interaction."""
    interactions: dict[str, dict] = {}
    if not chemin.exists():
        return interactions
    for ligne in chemin.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne:
            continue
        try:
            enr = json.loads(ligne)
        except json.JSONDecodeError:
            continue
        ident = enr.get("id")
        if ident:
            interactions[str(ident)] = enr
    return interactions


def dernier_vote(chemin: Path) -> dict[str, str]:
    """Renvoie le **dernier** vote (par ordre du fichier) pour chaque interaction.

    Args:
        chemin: Chemin de ``feedback.jsonl``.

    Returns:
        Dict ``id_interaction -> vote`` (``"up"`` / ``"down"``), le vote le plus
        récent l'emportant (un producteur peut changer d'avis).
    """
    votes: dict[str, str] = {}
    if not chemin.exists():
        return votes
    for ligne in chemin.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne:
            continue
        try:
            enr = json.loads(ligne)
        except json.JSONDecodeError:
            continue
        ident, vote = enr.get("id"), enr.get("vote")
        if ident and vote in ("up", "down"):
            votes[str(ident)] = vote  # écrase : la dernière ligne fait foi
    return votes


def joindre(interactions: dict[str, dict], votes: dict[str, str]) -> list[dict]:
    """Joint les votes aux interactions ; ne garde que les cas ayant un retour.

    Returns:
        Liste de cas ``{id, vote, question, reponse, sources, langue}`` dans l'ordre
        des votes (interactions sans feedback ignorées, vote orphelin ignoré).
    """
    cas: list[dict] = []
    for ident, vote in votes.items():
        inter = interactions.get(ident)
        if inter is None:
            continue
        cas.append(
            {
                "id": ident,
                "vote": vote,
                "question": str(inter.get("question", "")).strip(),
                "reponse": str(inter.get("reponse", "")).strip(),
                "sources": inter.get("sources", []),
                "langue": inter.get("langue", "fr"),
            }
        )
    return cas


def dedup_cas(cas: list[dict]) -> list[dict]:
    """Déduplique les cas par question normalisée.

    Une même question revenue plusieurs fois n'est curée qu'une fois ; un retour
    **négatif** prime sur un positif (plus informatif pour corriger le modèle).
    """
    par_cle: dict[str, dict] = {}
    for c in cas:
        cle = _cle(c["question"])
        if not cle:
            continue
        ancien = par_cle.get(cle)
        if ancien is None or (ancien["vote"] == "up" and c["vote"] == "down"):
            par_cle[cle] = c
    return list(par_cle.values())


_SYSTEME = (
    "Tu es un agronome ivoirien expérimenté qui CURE le jeu de données d'un assistant "
    "destiné aux producteurs de cacao. Tu produis des paires d'entraînement de haute "
    "qualité, sûres et conformes. Tu réponds UNIQUEMENT par un objet JSON valide."
)
_USER = (
    "Question d'un producteur :\n{question}\n\n"
    "Réponse de l'assistant (retour utilisateur : {avis}) :\n{reponse}\n\n"
    "Règles de la réponse attendue :\n"
    "- français simple, clair, concis (style SMS), adapté à un non-expert ;\n"
    "- cite AU MOINS une source reconnue (CNRA, ANADER, Conseil du Café-Cacao, FAO, "
    "FIRCA) et oriente vers l'ANADER ;\n"
    "- JAMAIS de dosage chiffré de produit phytosanitaire (pour cela, rediriger vers "
    "l'agent ANADER) ;\n"
    "- reste dans la filière cacao (anacarde/vivrier tolérés).\n\n"
    "Consigne :\n"
    "- Si la réponse a déplu (👎) mais la question est légitime : RÉÉCRIS une meilleure "
    "réponse (action « corriger »).\n"
    "- Si la question demande un dosage, un avis médical/vétérinaire, un diagnostic sur "
    "photo, ou sort de la filière : produis un REFUS poli qui oriente vers l'ANADER "
    "(action « refus »).\n"
    "- Si la réponse a plu (👍) et qu'elle est correcte et conforme : conserve-la en la "
    "nettoyant légèrement (action « garder »).\n"
    "- Si le cas est inexploitable : action « rejeter ».\n\n"
    "Cas particulier — zones/localités de culture : nomme les VRAIES régions "
    "cacaoyères (sud forestier, « boucle du cacao » : Gagnoa, Daloa, Soubré, San-Pédro, "
    "Aboisso, Abengourou…). N'affirme JAMAIS qu'une localité de savane du centre/nord "
    "(ex. Katiola) est propice au cacao. Si la ville du producteur est inconnue, "
    "DEMANDE-la et PROPOSE de lui transmettre le contact de l'agence ANADER la plus "
    "proche (le service rattache ensuite les coordonnées exactes).\n\n"
    "Réponds STRICTEMENT au format JSON :\n"
    '{{"action": "corriger|refus|garder|rejeter", "instruction": "...", "output": "..."}}'
)


def _extraire_json(brut: str) -> dict | None:
    """Extrait le 1er objet JSON d'une sortie de modèle (tolérant au bruit)."""
    debut, fin = brut.find("{"), brut.rfind("}")
    if debut == -1 or fin <= debut:
        return None
    try:
        obj = json.loads(brut[debut : fin + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


class CurateurLLM:
    """Maître OpenAI-compatible (GLM-5.2, hors prod) qui cure une interaction.

    Réutilise la même plomberie HTTP que le juge d'évaluation (urllib, aucun SDK).
    """

    def __init__(
        self,
        endpoint: str,
        modele: str,
        api_key: str | None,
        *,
        timeout_s: float = 60.0,
    ) -> None:
        self._endpoint = _joindre_endpoint(endpoint, "chat/completions")
        self._modele = modele
        self._api_key = api_key
        self._timeout_s = timeout_s

    def curer(self, cas: dict) -> dict | None:
        """Demande au maître une paire curée pour un cas du journal.

        Returns:
            Le verdict ``{action, instruction, output}`` ou ``None`` si le maître est
            injoignable / illisible (le cas est alors compté comme indisponible).
        """
        avis = "👎 (insatisfait)" if cas["vote"] == "down" else "👍 (satisfait)"
        charge = json.dumps(
            {
                "model": self._modele,
                "messages": [
                    {"role": "system", "content": _SYSTEME},
                    {
                        "role": "user",
                        "content": _USER.format(
                            question=cas["question"], reponse=cas["reponse"], avis=avis
                        ),
                    },
                ],
                "temperature": 0.2,
                "max_tokens": 600,
            }
        ).encode("utf-8")
        entetes = {"Content-Type": "application/json"}
        if self._api_key:
            entetes["Authorization"] = f"Bearer {self._api_key}"
        requete = urllib.request.Request(  # noqa: S310 - endpoint contrôlé par l'option
            self._endpoint, data=charge, headers=entetes, method="POST"
        )
        try:
            with urllib.request.urlopen(requete, timeout=self._timeout_s) as rep:  # noqa: S310
                donnees = json.loads(rep.read().decode("utf-8"))
            brut = str(donnees["choices"][0]["message"]["content"])
        except (urllib.error.URLError, KeyError, IndexError, ValueError, TimeoutError):
            return None
        return _extraire_json(brut)


def construire_paire(verdict: dict | None) -> tuple[dict | None, str]:
    """Construit et valide une paire d'entraînement à partir d'un verdict du maître.

    Args:
        verdict: Objet ``{action, instruction, output}`` renvoyé par le maître.

    Returns:
        ``(paire, "")`` si valide, sinon ``(None, motif)`` indiquant pourquoi écartée.
    """
    if verdict is None:
        return None, "maître indisponible"
    action = str(verdict.get("action", "")).strip().lower()
    if action == "rejeter":
        return None, "rejeté par le maître"
    if action not in ACTIONS_PAIRE:
        return None, f"action inconnue : {action or 'vide'}"

    instruction = str(verdict.get("instruction", "")).strip()
    output = str(verdict.get("output", "")).strip()
    paire = {"instruction": instruction, "input": "", "output": output}

    problemes = _valider_paire(0, paire)
    if problemes:
        return None, problemes[0].message
    # Garde-fou explicite (déjà couvert par _valider_paire, mais on ne prend AUCUN
    # risque sur les dosages — CLAUDE §13).
    if contient_dosage(output):
        return None, "dosage phytosanitaire détecté"
    return paire, ""


def curer_journal(
    cas: list[dict], curateur: CurateurLLM, deja_vues: set[str]
) -> tuple[list[dict], StatsCuration]:
    """Cure une liste de cas en paires d'entraînement validées et dédupliquées.

    Args:
        cas: Cas joints (question + réponse + vote).
        curateur: Maître LLM produisant les réécritures.
        deja_vues: Clés d'instruction déjà présentes (corpus curé existant) — évite
            les doublons entre exécutions successives.

    Returns:
        ``(paires, stats)`` : les paires curées et les compteurs de la passe.
    """
    paires: list[dict] = []
    stats = StatsCuration()
    for c in cas:
        stats.cas += 1
        verdict = curateur.curer(c)
        if verdict is None:
            stats.maitre_indisponible += 1
            stats.noter_rejet("maître indisponible")
            continue
        paire, motif = construire_paire(verdict)
        if paire is None:
            if motif == "rejeté par le maître":
                stats.rejetes += 1
            else:
                stats.invalides += 1
            stats.noter_rejet(motif)
            continue
        cle = _cle(paire["instruction"])
        if cle in deja_vues:
            stats.doublons += 1
            continue
        deja_vues.add(cle)
        paires.append(paire)
        stats.pairs += 1
    return paires, stats


def cles_existantes(sortie: Path) -> set[str]:
    """Charge les clés d'instruction d'un corpus curé existant (pour la déduplication)."""
    cles: set[str] = set()
    if not sortie.exists():
        return cles
    for ligne in sortie.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne:
            continue
        try:
            paire = json.loads(ligne)
        except json.JSONDecodeError:
            continue
        if "instruction" in paire:
            cles.add(_cle(str(paire["instruction"])))
    return cles


def ajouter_corpus(paires: list[dict], sortie: Path) -> None:
    """Ajoute les paires curées au corpus (création du dossier si besoin)."""
    if not paires:
        return
    sortie.parent.mkdir(parents=True, exist_ok=True)
    with sortie.open("a", encoding="utf-8") as handle:
        for paire in paires:
            handle.write(json.dumps(paire, ensure_ascii=False) + "\n")


def main() -> int:
    """Point d'entrée CLI. Retourne 0 si au moins une paire est curée, 1 sinon."""
    racine = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description="F11 — curation du journal (juge GLM)."
    )
    parser.add_argument(
        "--journal",
        type=Path,
        default=Path("/data"),
        help="Dossier du journal (interactions.jsonl + feedback.jsonl).",
    )
    parser.add_argument(
        "--interactions", type=Path, default=None, help="Chemin explicite."
    )
    parser.add_argument("--feedback", type=Path, default=None, help="Chemin explicite.")
    parser.add_argument(
        "--sortie",
        type=Path,
        default=racine / "corpus" / "corpus_cure.jsonl",
        help="Corpus curé à compléter (append, dédupliqué).",
    )
    parser.add_argument(
        "--votes",
        default="up,down",
        help="Votes à curer (défaut : up,down). Ex. : down pour ne corriger que les 👎.",
    )
    parser.add_argument("--max", type=int, default=0, help="Plafond de cas (0 = tous).")
    parser.add_argument(
        "--juge-endpoint", default=os.environ.get("JUGE_ENDPOINT", JUGE_ENDPOINT_DEFAUT)
    )
    parser.add_argument(
        "--juge-model", default=os.environ.get("JUGE_MODEL", JUGE_MODELE_DEFAUT)
    )
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()

    cle = os.environ.get("ZAI_API_KEY") or os.environ.get("CORPUS_LLM_API_KEY")
    if not cle:
        print(
            "ZAI_API_KEY (ou CORPUS_LLM_API_KEY) requise pour le maître.",
            file=sys.stderr,
        )
        return 1

    inter_path = args.interactions or (args.journal / FICHIER_INTERACTIONS)
    fb_path = args.feedback or (args.journal / FICHIER_FEEDBACK)
    interactions = charger_interactions(inter_path)
    votes = dernier_vote(fb_path)
    cas = joindre(interactions, votes)

    votes_voulus = {v.strip() for v in args.votes.split(",") if v.strip()}
    cas = [c for c in cas if c["vote"] in votes_voulus]
    cas = dedup_cas(cas)
    if args.max > 0:
        cas = cas[: args.max]

    if not cas:
        print(
            f"Aucun cas à curer (journal : {inter_path}, {fb_path}).", file=sys.stderr
        )
        return 1

    curateur = CurateurLLM(
        args.juge_endpoint, args.juge_model, cle, timeout_s=args.timeout
    )
    deja = cles_existantes(args.sortie)
    paires, stats = curer_journal(cas, curateur, deja)
    ajouter_corpus(paires, args.sortie)

    print(
        f"Curation : {stats.pairs} paires ajoutées sur {stats.cas} cas "
        f"({stats.rejetes} rejetés, {stats.invalides} invalides, {stats.doublons} doublons, "
        f"{stats.maitre_indisponible} maître indispo) -> {args.sortie}"
    )
    for motif, n in sorted(stats.motifs.items(), key=lambda kv: -kv[1]):
        print(f"  - {motif} : {n}")
    return 0 if stats.pairs else 1


if __name__ == "__main__":
    raise SystemExit(main())
