"""Post-traitement transverse partagé entre la V2 (ConseilService) et la V3.

Sérialisation du cache de réponses et enrichissement par le contact ANADER local.
Mutualisé pour que le chemin agentique (orchestrateur) ait la même parité
fonctionnelle que la V2, et que le cache soit interopérable entre les deux chemins
(une réponse mise en cache par le pré-chauffage V2 est lisible par la V3).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import replace

from app.domain.entities import Conseil
from app.models.domain import Confiance
from app.services import civilites, clarification, contacts, fiche

# Latence : une question de clarification tient en une phrase courte (la consigne
# l'exige). Le plafond n'est qu'un garde-fou anti-emballement : 40 tronquait des
# questions de 50-70 tokens EN plein milieu (mesuré 0.6.72) ; 64 laisse une phrase
# courte se terminer, la brièveté venant désormais de la consigne, pas de la coupe.
CLARIF_MAX_TOKENS = 64


def serialiser(conseil: Conseil) -> str:
    """Sérialise un conseil pour le cache (sans l'id de journalisation)."""
    return json.dumps(
        {
            "reponse": conseil.reponse,
            "confiance": conseil.confiance.value,
            "sources": conseil.sources,
            "redirection_anader": conseil.redirection_anader,
        }
    )


def depuis_paquet(donnees: dict) -> Conseil:
    """Reconstruit un Conseil depuis un paquet de cache sérialisé."""
    return Conseil(
        donnees["reponse"],
        Confiance(donnees["confiance"]),
        donnees["sources"],
        redirection_anader=donnees["redirection_anader"],
    )


def enrichir_contact(conseil: Conseil, texte_conversation: str) -> Conseil:
    """Ajoute le contact ANADER local exact si une mise en relation est pertinente.

    Le contact (numéro/adresse) provient de l'annuaire vérifié — jamais du modèle.
    Déclenché si la réponse oriente vers l'ANADER OU si l'utilisateur demande un
    contact, ET si une localité connue figure dans la conversation. Sinon (localité
    inconnue), on laisse le modèle demander la ville.

    Args:
        conseil: Conseil produit (avant enrichissement).
        texte_conversation: Texte cumulé de la conversation (pour repérer la ville).

    Returns:
        Le conseil, éventuellement enrichi du contact local.
    """
    if not (conseil.redirection_anader or contacts.intention_contact(texte_conversation)):
        return conseil
    lignes: list[str] = []
    contact = contacts.chercher(texte_conversation)
    if contact is not None:
        lignes.append(contacts.formater(contact))
    # Repli garanti : tant que la DR locale n'est pas vérifiée (ou inconnue), on
    # ajoute le siège (coordonnée confirmée) pour que le producteur ait toujours
    # une ligne fiable.
    if contact is None or not contact.verifie:
        siege = contacts.siege()
        if siege is not None:
            lignes.append(contacts.formater(siege))
    ajout = "\n".join(ligne for ligne in lignes if ligne and ligne not in conseil.reponse)
    if not ajout:
        return conseil
    sources = conseil.sources if "ANADER" in conseil.sources else [*conseil.sources, "ANADER"]
    return replace(
        conseil,
        reponse=f"{conseil.reponse}\n\n{ajout}",
        sources=sources,
        redirection_anader=True,
    )


async def question_clarification(
    inference: object, theme: str, question: str, historique: list[dict[str, str]] | None
) -> str:
    """Fait formuler par le modèle une question de clarification naturelle et brève."""
    consigne = clarification.consigne_theme(
        theme, clarification.besoin_localite(question, historique)
    )
    return await inference.generer(  # type: ignore[attr-defined]
        question, consigne=consigne, historique=historique, max_tokens=CLARIF_MAX_TOKENS
    )


async def question_clarification_stream(
    inference: object, theme: str, question: str, historique: list[dict[str, str]] | None
) -> AsyncIterator[str]:
    """Variante flux : diffuse la question de clarification au fil de l'eau."""
    consigne = clarification.consigne_theme(
        theme, clarification.besoin_localite(question, historique)
    )
    async for fragment in inference.generer_stream(  # type: ignore[attr-defined]
        question, consigne=consigne, historique=historique, max_tokens=CLARIF_MAX_TOKENS
    ):
        yield fragment


def civilite_ou_none(
    question: str, historique: list[dict[str, str]] | None, actif: bool
) -> str | None:
    """Réponse écrite d'avance si le tour est une pure civilité, sinon ``None``.

    Appelée AVANT les garde-fous : « Qui es-tu ? » y serait refusée comme hors
    filière, ce qui répond mal à une question de présentation. C'est sans risque
    parce que ce chemin ne produit aucun texte de modèle — rien à filtrer en sortie.

    Args:
        question: Dernier tour du producteur.
        historique: Tours précédents, ou ``None``.
        actif: Drapeau conversationnel. Faux → toujours ``None`` (repli sans effet).

    Returns:
        Le texte constant à servir, ou ``None`` si le tour doit suivre le pipeline.
    """
    if not actif:
        return None
    nature = civilites.detecter(question)
    if nature is None:
        return None
    return civilites.repondre(nature, fiche.rappel_court(fiche.extraire("", historique)))


def memoire_du_fil(question: str, historique: list[dict[str, str]] | None, actif: bool) -> str:
    """Faits déjà énoncés par le producteur, à rappeler au modèle.

    Args:
        question: Dernier tour du producteur.
        historique: Tours précédents, ou ``None``.
        actif: Drapeau conversationnel. Faux → ``""``, donc prompt système inchangé.

    Returns:
        Le bloc de mémoire, ou ``""`` si rien n'est connu (ou si le drapeau est bas).
    """
    if not actif:
        return ""
    return fiche.bloc_memoire(fiche.extraire(question, historique))
