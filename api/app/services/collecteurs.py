"""Collecteurs — les sources existantes rendues citables (V3, chantier C3).

Le moteur de rédaction ne connaît que des ``Affirmation`` sourcées. Ce module fait la
conversion depuis ce que le projet possède déjà : le RAG et les outils météo, prix et
satellite.

**Un dict vide vaut « indisponible ».** C'est le contrat déjà tenu par tous les outils
(``outils/indisponible.py``) : la source ne ment pas, elle ne rend rien. Le moteur
traduit ce silence en constat de lacune — exactement le comportement voulu par D4 : on
dit ce qui manque, on n'estime pas.
"""

from __future__ import annotations

import hashlib
from typing import Protocol

from app.core.logging import get_logger
from app.models.constat import NiveauConfiance
from app.models.rapport import Affirmation

logger = get_logger(__name__)

# Longueur de l'empreinte d'un passage documentaire, reportée au manifeste. C'est elle
# qui rend la sélection documentaire rejouable.
_LONGUEUR_EMPREINTE = 12

# Au-delà de ce score de similarité, un passage est jugé solidement pertinent.
_SEUIL_CONFIANCE_ELEVEE = 0.75

# Clés qui décrivent la donnée plutôt qu'elles n'en portent une.
_CLES_METADONNEES = frozenset({"date", "horodatage", "source"})


class OutilPort(Protocol):
    """Contrat commun des outils du projet."""

    async def invoquer(self, **kwargs: object) -> dict[str, object]:
        """Retourne les données de la source, ou un dict vide si indisponible."""
        ...


def empreinte_passage(texte: str) -> str:
    """Réduit un passage documentaire à une empreinte stable.

    Args:
        texte: Contenu du passage mobilisé.

    Returns:
        Une empreinte courte, qui identifie l'extrait sans le recopier.
    """
    return hashlib.sha256(texte.encode("utf-8")).hexdigest()[:_LONGUEUR_EMPREINTE]


class CollecteurOutil:
    """Rend citable un outil existant (météo, prix, satellite)."""

    # Le prix officiel et la météo d'une zone sont les mêmes au chapitre 1 et au 5 :
    # une seule invocation sert tout le document. Et l'argument reste le SUJET, jamais
    # le titre de section — la détection de localité lit dedans (localites.py).
    varie_par_section = False

    def __init__(self, outil: OutilPort, source: str, cle_argument: str = "") -> None:
        """Initialise le collecteur.

        Args:
            outil: Outil à envelopper.
            source: Nom de la source à citer dans le document.
            cle_argument: Nom de l'argument recevant le sujet (« localite »), ou
                chaîne vide si l'outil n'en prend aucun (le prix, par exemple).
        """
        self._outil = outil
        self._source = source
        self._cle = cle_argument

    async def collecter(self, sujet: str, requete: str = "") -> tuple[Affirmation, ...]:
        """Invoque l'outil et convertit sa réponse en affirmations sourcées.

        Args:
            sujet: Sujet du document, passé à l'outil s'il attend un argument.
            requete: Ignoré — un outil ne se raffine pas par section, et lui passer un
                titre brouillerait la détection de localité.

        Returns:
            Une affirmation par donnée rendue, vide si la source est indisponible.
        """
        arguments = {self._cle: sujet} if self._cle else {}
        donnees = await self._outil.invoquer(**arguments)
        if not donnees:
            logger.info("collecteur_source_vide", source=self._source)
            return ()
        date = str(donnees.get("date") or donnees.get("horodatage") or "")
        return tuple(
            Affirmation(
                texte=f"{cle} : {valeur}",
                source=self._source,
                date=date,
                methode=f"outil:{self._source.lower()}",
                confiance=NiveauConfiance.MOYENNE,
            )
            for cle, valeur in donnees.items()
            if cle not in _CLES_METADONNEES and valeur not in (None, "", [], {})
        )


class CollecteurRag:
    """Rend citables les passages du corpus documentaire."""

    # Source DOCUMENTAIRE : elle se raffine par section. Interrogée avec le seul sujet,
    # les cinq sections d'une étude recevaient les mêmes passages, et il ne restait au
    # modèle qu'à inventer la différence entre elles.
    varie_par_section = True

    def __init__(self, recuperateur: object | None) -> None:
        """Initialise le collecteur.

        Args:
            recuperateur: ``RagRecuperateur`` exposant ``passages_pour``, ou ``None``
                si le RAG n'est pas configuré.
        """
        self._recuperateur = recuperateur

    async def collecter(self, sujet: str, requete: str = "") -> tuple[Affirmation, ...]:
        """Retourne les passages pertinents, chacun avec sa source et son empreinte.

        Args:
            sujet: Sujet du document, servant de requête à défaut de mieux.
            requete: Requête propre à la section — titre, consigne et sujet. C'est
                elle qui fait que deux sections ne reçoivent pas le même contexte.

        Returns:
            Une affirmation par passage retenu, vide si le RAG est absent.
        """
        if self._recuperateur is None:
            return ()
        # Requête de section pour le sens, sujet pour l'ancre lexicale : sans cette
        # dissociation, allonger la requête ferait tomber le recouvrement lexical sous
        # son seuil et tuerait la voie de secours qui rattrape les termes rares.
        passages = await self._recuperateur.passages_pour(requete or sujet, ancre=sujet)
        return tuple(
            Affirmation(
                texte=passage.texte,
                source=passage.source,
                date="",
                methode="rag",
                confiance=(
                    NiveauConfiance.ELEVEE
                    if passage.score >= _SEUIL_CONFIANCE_ELEVEE
                    else NiveauConfiance.MOYENNE
                ),
                empreinte=empreinte_passage(passage.texte),
            )
            for passage in passages
            if passage.source.strip()
        )
