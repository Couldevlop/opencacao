"""Ce que XML 1.0 refuse — contrainte commune aux quatre formats.

Ce module ne contredit pas la règle du paquet (« chaque format porte SES garanties,
sans les faire fuir dans les autres ») : ce n'est pas une garantie propre à l'un des
formats, c'est **la même norme** pour tous. Word, Excel et PowerPoint sont du XML dans
un ZIP, et le Markdown est encodé en UTF-8 à la frontière HTTP — les quatre cassent
sur les mêmes caractères.

**Pourquoi ce n'est pas théorique.** ``json.loads('"\\ud800"')`` accepte un surrogate
isolé : c'est la voie de décodage de la sortie du modèle, et les passages du corpus
viennent d'un JSONL. Sans purge, l'encodage lève une ``UnicodeEncodeError`` — qui est
une ``ValueError``, donc attrapée par le routeur d'export et rendue en « format
inconnu ». Un rapport empoisonné devenait inexportable **dans les quatre formats**,
avec un message désignant le mauvais coupable.
"""

from __future__ import annotations

import re

# Interdits par XML 1.0 :
#  - contrôles C0 hors tabulation, saut de ligne et retour chariot ;
#  - surrogates isolés (U+D800–U+DFFF), qu'un JSON peut porter ;
#  - non-caractères U+FFFE et U+FFFF.
# Tout le reste passe : accents, hors-BMP (emoji), écritures RTL, espaces fines.
_INTERDITS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")


def texte_xml_sur(texte: str) -> str:
    """Retire d'un texte tout ce que XML 1.0 refuse.

    Args:
        texte: Texte brut, éventuellement issu du modèle ou d'une source externe.

    Returns:
        Un texte toujours encodable et toujours acceptable par les trois
        bibliothèques OOXML.
    """
    return _INTERDITS.sub("", texte)
