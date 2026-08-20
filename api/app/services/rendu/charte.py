"""Charte graphique commune aux quatre formats — une seule source, jamais quatre.

**Pourquoi Office 2024 plutôt qu'une palette maison.** Ces documents sont lus dans
Word et PowerPoint, par des institutions qui y travaillent toute la journée. Reprendre
la palette et la typographie natives d'Office 2024 leur donne l'air d'un document du
métier plutôt que d'un export d'outil : familier, contemporain, et déjà éprouvé par
Microsoft pour les contrastes et les daltonismes les plus fréquents. Inventer notre
propre nuancier aurait coûté ce travail d'accessibilité pour un gain nul.

**Ce qu'on en écarte.** L'accent orange d'Office 2024 (``E97132``) est à un cheveu de
l'orange OpenCacao (``EA5B13``). Les garder tous deux donnerait deux oranges qui se
confondent en projection et à l'impression. On garde donc celui du projet, en tête de
palette, et on prend les cinq autres accents d'Office — l'identité mène, le reste suit.

**La typographie.** Aptos est la police par défaut d'Office depuis 2024. Sur un poste
plus ancien, Word et PowerPoint lui substituent automatiquement une sans-serif proche :
la dégradation est propre, ce qui n'aurait pas été le cas d'une police exotique.
"""

from __future__ import annotations

# --- Identité ---------------------------------------------------------------
ORANGE = "EA5B13"
SOMBRE = "1F1F1F"
GRIS = "606060"
BLANC = "FFFFFF"

# Aplats de fond. Trois teintes suffisent : l'orange pour ce qui structure, son
# dégradé très clair pour le zébrage, un gris pâle pour ce qui n'est qu'informatif.
FOND_ORANGE_PALE = "FDF0E6"
FOND_GRIS_PALE = "F2F2F2"

# --- Palette de séries ------------------------------------------------------
# L'orange du projet, puis les accents d'Office 2024 hormis son propre orange.
# Ordre choisi pour que deux séries voisines restent distinctes en niveaux de gris.
SERIES: tuple[str, ...] = (
    ORANGE,  # OpenCacao
    "156082",  # bleu-sarcelle  (accent1 Office 2024)
    "196B24",  # vert forêt     (accent3)
    "0F9ED5",  # bleu clair     (accent4)
    "A02B93",  # violet         (accent5)
    "4EA72E",  # vert           (accent6)
    "7F8C8D",  # gris de repli
    "F1C40F",  # jaune de repli
)

# --- Typographie ------------------------------------------------------------
POLICE_TITRES = "Aptos Display"
POLICE_CORPS = "Aptos"

# --- Format de présentation -------------------------------------------------
# 16:9. Le 4:3 laisse deux bandes noires sur tout vidéoprojecteur d'aujourd'hui et
# date un document de quinze ans avant qu'on ait lu la première ligne.
DIAPO_LARGEUR_PO = 13.333
DIAPO_HAUTEUR_PO = 7.5


def couleur_serie(rang: int) -> str:
    """Couleur d'une série ou d'une part, par son rang (cycle si dépassement)."""
    return SERIES[rang % len(SERIES)]
