"""Balisage léger du modèle — le rendre, jamais l'imprimer.

**Constat de l'audit du 19/08.** Les documents Word livrés portaient **52 occurrences
de ``**``** écrites en toutes lettres : le modèle émet du gras Markdown, et les
adaptateurs de format le recopiaient tel quel. Une étude destinée à une commission
avait l'air d'un copier-coller de conversation. C'était le défaut le plus visible et le
plus immédiatement disqualifiant du lot.

**Pourquoi ici et pas dans le prompt.** On pourrait interdire le balisage au modèle —
le prompt le fait déjà pour les titres et les puces. Mais une consigne est une prière :
elle tient la plupart du temps, et le jour où elle cède, c'est le document livré qui
porte la faute. Rendre le balisage plutôt que l'interdire fait de cette émission une
information utile au lieu d'une avarie.

Deux formes seulement, celles que le modèle produit réellement : ``**gras**`` et
``*italique*``. Un balisage incomplet est laissé intact — mieux vaut un astérisque
visible qu'une phrase amputée.
"""

from __future__ import annotations

import re

# Le gras AVANT l'italique : « **mot** » commence par un astérisque, et la règle
# italique le happerait en produisant un segment vide suivi d'un « *mot* » bancal.
# `[^*]` interdit les délimiteurs vides (« **** ») et les imbrications, qu'aucune
# sortie observée ne produit.
_BALISES = re.compile(r"\*\*(?P<gras>[^*]+?)\*\*|\*(?P<italique>[^*]+?)\*")

# (texte, gras, italique)
Segment = tuple[str, bool, bool]


def segments(texte: str) -> tuple[Segment, ...]:
    """Découpe un texte en segments porteurs de leur enrichissement.

    Args:
        texte: Texte tel qu'émis par le modèle, balisage compris.

    Returns:
        Les segments dans l'ordre, chacun avec ses drapeaux gras et italique. La
        concaténation des textes rend toujours l'original **privé de ses seuls
        délimiteurs reconnus** : rien n'est jamais perdu.
    """
    trouves: list[Segment] = []
    position = 0
    for correspondance in _BALISES.finditer(texte):
        if correspondance.start() > position:
            trouves.append((texte[position : correspondance.start()], False, False))
        gras = correspondance.group("gras")
        if gras is not None:
            trouves.append((gras, True, False))
        else:
            trouves.append((correspondance.group("italique"), False, True))
        position = correspondance.end()
    if position < len(texte):
        trouves.append((texte[position:], False, False))
    return tuple(trouves) if trouves else ((texte, False, False),)


def sans_balisage(texte: str) -> str:
    """Rend le texte nu, pour les formats qui ne portent pas d'enrichissement.

    Le tableur, les métadonnées de fichier et les titres de figure n'ont pas de notion
    de gras : ils reçoivent le texte débarrassé de ses délimiteurs plutôt que le
    balisage brut.

    Args:
        texte: Texte tel qu'émis par le modèle.

    Returns:
        Le texte sans les délimiteurs reconnus.
    """
    return "".join(fragment for fragment, _, _ in segments(texte))
