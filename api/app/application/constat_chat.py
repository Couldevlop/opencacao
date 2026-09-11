"""La photo dans le chat : une porte d'entrée de plus vers la cascade existante.

Le parcours « Ma parcelle » sait déjà lire une image et en produire un **constat
descriptif** — ce qu'on voit, jamais la maladie qu'on en déduirait. Ce module ne
refait rien de tout cela : il branche le chat sur la même cascade, avec le même
contrat de sortie.

Trois choses s'y jouent, dans cet ordre, et l'ordre est le sujet :

1. **La recevabilité d'abord**, parce qu'elle seule dit s'il y a vraiment une image à
   analyser. Une photo floue ou à contre-jour rend un conseil de reprise — pas une
   erreur, pas une description hasardeuse : le producteur qui est dans sa plantation
   doit savoir quoi refaire.
2. **Les garde-fous métier ensuite, la vision en dernier.** Une photo ne devient jamais
   un passe-droit : « quelle dose de fongicide ? » accompagné d'une image reste un
   refus, et le modèle de vision n'est pas appelé.
3. **La sortie reste verrouillée.** ``ServiceConstatVisuel`` rejette toute description
   qui nomme une maladie ou prescrit un produit. Ce module n'y touche pas.

La règle lexicale d'entrée (le mot « photo » suffisait à refuser) n'est levée QUE
lorsqu'une image recevable est réellement analysée — ``image_analysee=True``. Sans
image, le refus reste entier : promettre une lecture qu'on ne fait pas serait pire
que refuser.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import structlog

from app.application.fusion_contextuelle import ContexteParcelle
from app.models.parcelle import ImageRequest
from app.services import guardrails, localites
from app.services.vision import recevabilite

logger = structlog.get_logger(__name__)

# La consigne du parcours parcelle parle de photos « rattachées à votre parcelle » :
# dans un fil de conversation, cette phrase est fausse. On dit la vérité de ce canal —
# rien n'est conservé, et on n'invente aucune description.
VISION_INDISPONIBLE_CHAT = (
    "L'analyse de photo n'est pas disponible en ce moment. Je préfère vous le dire "
    "plutôt que de décrire une image que je n'ai pas pu lire. Décrivez-moi ce que vous "
    "observez (sur quelle partie, depuis quand) et je vous aiderai ; pour un avis sur "
    "la photo elle-même, montrez-la à votre agent ANADER."
)

# Une photo par message. Le constat visuel est séquentiel et chaque image consomme
# beaucoup de contexte : accepter une rafale rendrait la réponse plus lente sans la
# rendre meilleure.
MAX_IMAGES = 1


class ConstatVisuelPort(Protocol):
    """Ce que ce service attend de la cascade de constat (mockable)."""

    async def analyser(
        self, images: tuple[tuple[bytes, str], ...], contexte: ContexteParcelle
    ) -> object | None:
        """Produit le constat d'un jeu d'images, ou ``None``."""
        ...


@dataclass(frozen=True)
class ResultatPhoto:
    """Ce que le chat doit afficher après une photo.

    Attributes:
        texte: Texte à servir au producteur (constat, refus ou consigne). Vide
            lorsqu'un conseil de reprise le remplace.
        refus: Vrai si un garde-fou métier a répondu à la place de la vision.
        indisponible: Vrai si la vision n'a produit aucune description exploitable.
        conseil_reprise: Conseil de reprise de la photo, ou chaîne vide.
    """

    texte: str = ""
    refus: bool = False
    indisponible: bool = False
    conseil_reprise: str = ""


def _decoder(image: ImageRequest) -> bytes | None:
    """Décode le contenu base64 d'une image, ou ``None`` s'il est illisible."""
    try:
        return base64.b64decode(image.contenu_base64, validate=True)
    except (binascii.Error, ValueError):
        logger.info("photo_chat_base64_invalide")
        return None


class ServiceConstatChat:
    """Produit le constat d'une photo envoyée dans le fil de conversation."""

    def __init__(self, constat_visuel: ConstatVisuelPort) -> None:
        """Initialise le service.

        Args:
            constat_visuel: Cascade de constat visuel, déjà utilisée par « Ma parcelle ».
        """
        self._constat = constat_visuel

    async def analyser(
        self, question: str, images: Sequence[ImageRequest], conversation: str
    ) -> ResultatPhoto:
        """Analyse une photo accompagnée d'une question.

        Args:
            question: Texte écrit par le producteur avec sa photo.
            images: Images jointes (seule la première est analysée).
            conversation: Tous les tours du producteur, pour y lire la localité.

        Returns:
            Le résultat à afficher : constat, refus, conseil de reprise ou
            indisponibilité. Jamais une description fabriquée.
        """
        if not images:
            return ResultatPhoto(indisponible=True, texte=VISION_INDISPONIBLE_CHAT)

        # 1. Recevabilité D'ABORD — parce que c'est elle qui dit s'il y a vraiment une
        #    image à analyser. Annoncer `image_analysee=True` avant de le savoir
        #    lèverait la règle lexicale sur la foi d'un fichier illisible : sans
        #    conséquence ici (aucun modèle n'est appelé), mais une affirmation fausse
        #    dans un garde-fou finit toujours par devenir un trou.
        retenues: list[tuple[bytes, str]] = []
        conseil = ""
        for image in list(images)[:MAX_IMAGES]:
            donnees = _decoder(image)
            if donnees is None:
                conseil = conseil or "Je n'ai pas pu lire cette image. Réessayez la prise de vue."
                continue
            # Le champ base64 n'est pas borné par le schéma : on refuse ici, comme le
            # fait déjà le dépôt de capture, plutôt que de porter 3 Mo jusqu'au modèle.
            if len(donnees) > recevabilite.TAILLE_MAX_OCTETS:
                logger.info("photo_chat_trop_lourde", octets=len(donnees))
                conseil = (
                    conseil or "Cette photo est trop lourde. Reprenez-la à une taille normale."
                )
                continue
            verdict = recevabilite.evaluer(image, donnees)
            if not verdict.recevable:
                logger.info("photo_chat_non_recevable", motif=verdict.motif.value)
                conseil = conseil or verdict.conseil
                continue
            retenues.append((donnees, hashlib.sha256(donnees).hexdigest()))

        # 2. Les garde-fous ensuite, la vision en dernier. `image_analysee` ne lève que
        #    la règle d'image, et seulement si une image sera RÉELLEMENT analysée ; le
        #    dosage, le médical et le hors-filière restent des refus dans tous les cas.
        refus = guardrails.evaluer(
            question,
            conversation=conversation or question,
            image_analysee=bool(retenues),
        )
        if refus is not None:
            logger.info("photo_chat_garde_fou", categorie=refus.categorie.value)
            return ResultatPhoto(texte=refus.message, refus=True)

        if not retenues:
            return ResultatPhoto(conseil_reprise=conseil)

        # 3. La cascade, inchangée. Son contexte : la localité déjà citée dans le fil.
        contexte = ContexteParcelle(
            pluie_mm_14j=None,
            saison="",
            localite=localites.detecter(conversation) or "",
            alertes_deforestation=None,
        )
        constat = await self._constat.analyser(tuple(retenues), contexte)
        if constat is None:
            logger.info("photo_chat_vision_indisponible")
            return ResultatPhoto(indisponible=True, texte=VISION_INDISPONIBLE_CHAT)

        return ResultatPhoto(texte=str(getattr(constat, "texte", "")).strip())
