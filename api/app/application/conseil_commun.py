"""Post-traitement transverse partagé entre la V2 (ConseilService) et la V3.

Sérialisation du cache de réponses et enrichissement par le contact ANADER local.
Mutualisé pour que le chemin agentique (orchestrateur) ait la même parité
fonctionnelle que la V2, et que le cache soit interopérable entre les deux chemins
(une réponse mise en cache par le pré-chauffage V2 est lisible par la V3).
"""

from __future__ import annotations

import json
from dataclasses import replace

from app.domain.entities import Conseil
from app.models.domain import Confiance
from app.services import contacts


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
