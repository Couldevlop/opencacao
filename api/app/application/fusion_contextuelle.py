"""Étage 4 de la cascade — croise l'observation visuelle et le contexte de la parcelle.

**C'est ce qu'aucun classifieur publié sur le cacao ne fait**, faute d'agents météo et
de RAG derrière lui. Une observation évoquant une pourriture après trois semaines
sèches est douteuse : la pourriture brune se développe avec l'humidité prolongée. On
ne conclut rien — on **dégrade la confiance** et on **écrit pourquoi**.

Orchestration pure : aucun réseau, aucun accès disque, entièrement testable.

**D3 rappelé** : les facteurs produits ici expliquent un contexte. Ils ne nomment
jamais une maladie, et un test le vérifie.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.constat import NiveauConfiance

# Cumul de pluie sur 14 jours au-delà duquel l'humidité est jugée prolongée.
PLUIE_HUMIDE_MM = 60.0

# Cumul en deçà duquel la période est jugée sèche.
PLUIE_SECHE_MM = 5.0

# Termes descriptifs (jamais un nom de maladie) qui, dans une description, évoquent
# une atteinte favorisée par l'humidité. On reste au niveau du SYMPTÔME OBSERVÉ.
_SIGNES_HUMIDITE = ("pourri", "tache brune", "taches brunes", "moisiss", "chancre")


@dataclass(frozen=True)
class ContexteParcelle:
    """Ce que la plateforme sait déjà de la parcelle, au moment du constat."""

    pluie_mm_14j: float | None
    saison: str
    localite: str
    alertes_deforestation: int | None


@dataclass(frozen=True)
class Fusion:
    """Confiance après croisement, et les facteurs qui l'expliquent."""

    confiance: NiveauConfiance
    facteurs: tuple[str, ...]


def _evoque_une_atteinte_humide(description: str) -> bool:
    """Indique si la description mentionne un signe favorisé par l'humidité."""
    minuscules = description.lower()
    return any(signe in minuscules for signe in _SIGNES_HUMIDITE)


def fusionner(description: str, confiance: NiveauConfiance, contexte: ContexteParcelle) -> Fusion:
    """Croise une observation visuelle avec le contexte connu de la parcelle.

    Args:
        description: Texte descriptif produit par le modèle de vision.
        confiance: Confiance de l'observation avant croisement.
        contexte: Météo récente, saison, localité, alertes de la zone.

    Returns:
        La confiance après croisement et les facteurs, rédigés pour un producteur.
    """
    facteurs: list[str] = [f"Parcelle située à {contexte.localite}."]
    if contexte.saison:
        facteurs.append(f"Période : {contexte.saison}.")

    resultat = confiance
    if not _evoque_une_atteinte_humide(description):
        return Fusion(confiance=resultat, facteurs=tuple(facteurs))

    if contexte.pluie_mm_14j is None:
        facteurs.append(
            "Relevé de pluie indisponible pour cette zone : le constat n'a pas pu "
            "être recoupé avec la météo récente."
        )
        resultat = resultat.degrader()
    elif contexte.pluie_mm_14j >= PLUIE_HUMIDE_MM:
        facteurs.append(
            f"Il est tombé {contexte.pluie_mm_14j:.0f} mm de pluie sur les 14 derniers "
            "jours : une humidité prolongée favorise ce type d'atteinte."
        )
    elif contexte.pluie_mm_14j <= PLUIE_SECHE_MM:
        facteurs.append(
            f"Il n'est tombé que {contexte.pluie_mm_14j:.0f} mm de pluie sur les 14 "
            "derniers jours : ce temps sec cadre mal avec ce qui est observé, la "
            "confiance est abaissée."
        )
        resultat = resultat.degrader()

    return Fusion(confiance=resultat, facteurs=tuple(facteurs))
