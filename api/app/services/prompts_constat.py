"""Consignes encadrant le constat visuel (étages 1 et 5 de la cascade).

Le levier est la **consigne**, pas le plafond de tokens — leçon acquise en juillet sur
le dialogue naturel. Ces textes sont donc écrits comme des interdits explicites, et
doublés d'un garde-fou de sortie (``guardrails.contient_diagnostic``) : on ne fait pas
confiance au modèle pour respecter une consigne, on vérifie.
"""

from __future__ import annotations

CONSIGNE_DESCRIPTION = (
    "Tu es un observateur agronome. Décris FACTUELLEMENT ce que montrent ces photos "
    "de cacaoyer : partie de la plante visible (cabosse, feuille, tronc, vue "
    "d'ensemble), couleurs, taches, textures, étendue approximative de ce que tu "
    "observes, état de l'ombrage et de l'entretien.\n"
    "INTERDITS ABSOLUS : ne nomme JAMAIS une maladie ni un ravageur. Ne propose "
    "JAMAIS un produit, un traitement ou une dose. N'affirme pas une cause. Si une "
    "photo est inexploitable, dis-le simplement.\n"
    "Réponds en français simple, en trois phrases au maximum."
)


def consigne_redaction(facteurs: tuple[str, ...]) -> str:
    """Construit la consigne de rédaction du constat destiné au producteur.

    Args:
        facteurs: Éléments de contexte issus de la fusion (étage 4), déjà rédigés.

    Returns:
        La consigne complète, contexte inclus.
    """
    contexte = "\n".join(f"- {facteur}" for facteur in facteurs)
    bloc = f"\nÉléments de contexte connus :\n{contexte}\n" if facteurs else "\n"
    return (
        "Rédige un constat court et clair pour un producteur de cacao ivoirien, à "
        "partir de l'observation ci-dessus."
        f"{bloc}"
        "Règles : ne nomme aucune maladie et aucun ravageur ; ne propose aucun produit "
        "ni aucune dose. Tu peux conseiller des gestes sans produit (récolte sanitaire, "
        "évacuation des cabosses atteintes, élagage, aération). Termine en invitant le "
        "producteur à montrer ces photos à son agent ANADER pour confirmation.\n"
        "Français simple, cinq phrases au maximum."
    )
