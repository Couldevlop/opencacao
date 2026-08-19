"""Moteur de rédaction des livrables (spec §8.1) — orchestration pure, testable sans réseau.

Trois temps : **planifier** (le gabarit fournit le plan), **rédiger** (section par
section, chacune avec son contexte propre), **assembler** (un ``Document`` immuable).

**Pourquoi section par section, et pas d'un seul jet.** L'analyse du corpus du
28/07/2026 est sans ambiguïté : réponses de 583 caractères en médiane, 1 201 au
maximum, et 0,0 % de titres, de puces, de listes ou de tableaux. Un 8B qui n'a jamais
lu de document de 30 000 caractères ne peut pas en écrire un d'un seul jet ; il peut
écrire quarante paragraphes de 700 caractères, ce qui est le même document. Le
découpage n'est donc pas seulement une parade au time-out edge : **c'est ce qui rend
l'étude possible.**

**D4 — une section sans source ne mobilise pas le modèle.** Elle rend un constat de
lacune qui dit ce qui manque. Générer sans contexte est exactement la fabrication que
tout le reste du projet combat (v0.6.48).

**Un seul garde-fou de sortie, et c'est un choix.** ``contient_prescription``
s'applique : un document d'étude ne prescrit pas plus qu'un conseil au producteur.
Mais il refuse la *prescription*, pas le *chiffre* — un rendement en kg/ha est la
donnée la plus courante d'une analyse agronomique, et le garde-fou du conseil, qui se
déclenche sur tout taux de dose, aurait vidé chaque section de sa substance.

``contient_diagnostic`` **ne s'applique pas** (arbitrage Waopron du 29/07/2026) : ce
verrou est celui du constat visuel — nommer une atteinte
à partir d'une *photo*, sans jeu de données pour mesurer le rappel. Une étude qui
rapporte, sources à l'appui, qu'une maladie affecte la filière énonce un fait
documenté ; l'interdire rendrait toute section agronomique impossible à écrire.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from app.application.provenance import construire_manifeste, source_absente
from app.core.logging import get_logger
from app.domain.ports import InferencePort
from app.models.rapport import Affirmation, Document, Section
from app.services import guardrails
from app.services.gabarits import Gabarit, SectionGabarit
from app.services.prompts_redaction import (
    ENTETE_CONTEXTE_ANALYTIQUE,
    LIBELLE_SECTION,
    SYSTEM_PROMPT_REDACTION,
    consigne_section,
)
from app.services.rendu import graphiques

logger = get_logger(__name__)

# Une section tient en un paragraphe de 600 à 800 caractères : on borne la génération
# en conséquence. Le levier reste la consigne, ce plafond n'est qu'un garde-corps.
MAX_TOKENS_SECTION = 320

# Température basse : un document d'analyse doit être reproductible, pas créatif.
TEMPERATURE_SECTION = 0.3

# Bornes du contexte injecté par section. Sans elles, un collecteur verbeux ferait
# préremplir des milliers de tokens par section, sur CPU. Alignées sur les plafonds
# déjà retenus pour le RAG (``rag_top_k``, ``rag_passage_max_chars``).
MAX_AFFIRMATIONS_SECTION = 12
MAX_CARACTERES_AFFIRMATION = 480

# Catégories de refus qui s'appliquent au SUJET d'un livrable.
#
# Les garde-fous du projet ont été écrits pour la question d'un producteur ; toutes
# leurs catégories ne se transposent pas à un document d'analyse. Deux familles :
#
# * Ce qui ne doit JAMAIS être produit, quel que soit le lecteur — un dosage, un avis
#   médical, une autre culture que le cacao, un diagnostic sur image. Non négociable
#   (CLAUDE.md), et donc refusé ici.
# * Ce qui relève de « à qui l'assistant s'adresse » — un producteur à Korhogo est
#   redirigé parce qu'on n'y cultive pas de cacao, et on ne conseille pas sur la
#   transformation. Mais une ÉTUDE sur la filière à Korhogo, sur la limite nord de la
#   ceinture cacaoyère ou sur la valeur ajoutée locale est un travail d'analyse
#   parfaitement légitime — c'est même ce qu'un bailleur demande. Refuser rendrait le
#   bulletin régional d'une DR du Nord impossible à produire.
_REFUS_SUR_SUJET = frozenset(
    {
        guardrails.CategorieRefus.PHYTOSANITAIRE,
        guardrails.CategorieRefus.MEDICAL,
        guardrails.CategorieRefus.HORS_FILIERE,
        guardrails.CategorieRefus.DIAGNOSTIC_IMAGE,
    }
)

_LACUNE = (
    "Aucune source mobilisable n'a été trouvée pour cette section. Elle est laissée "
    "en l'état plutôt que renseignée par estimation : les éléments nécessaires "
    "devront être fournis pour la compléter."
)

_LACUNE_REFUSEE = (
    "La rédaction de cette section a été écartée par un garde-fou de sortie. Elle est "
    "laissée en l'état plutôt que corrigée : une sortie qui a franchi un interdit "
    "n'est pas réécrite."
)

# Le rappel reçoit la SECTION, pas seulement son titre : l'écran doit pouvoir
# afficher la prose au fil de l'eau — « le flux SSE écrit les sections à l'écran »
# (spec §8.7). Un titre seul ne permet que d'allumer une case.
ProgressionRappel = Callable[[int, int, Section], Awaitable[None]]


class SujetRefuse(Exception):
    """Le sujet demandé franchit un garde-fou métier.

    Attributes:
        message: Message de redirection destiné au demandeur.
    """

    def __init__(self, message: str) -> None:
        """Initialise l'exception avec le message de redirection."""
        super().__init__(message)
        self.message = message


# Volontairement PAS `@runtime_checkable` : un Protocol portant un membre qui n'est pas
# une méthode — ici `varie_par_section` — fait lever `issubclass`. Le décorateur
# promettrait alors une capacité qu'il n'a plus. Aucun `isinstance`/`issubclass` ne vise
# ce port ; on retire la promesse plutôt que de la laisser mentir.
class CollecteurPort(Protocol):
    """Contrat d'une source mobilisable par une section.

    ``varie_par_section`` distingue les deux natures de source. Une source
    DOCUMENTAIRE (le RAG) doit être interrogée avec la requête propre à la section :
    sans quoi les cinq sections d'une étude reçoivent les mêmes passages, et il ne
    reste au modèle qu'à inventer la différence. Une source OUTIL (prix, météo) ne
    varie pas d'une section à l'autre — elle dépend de la localité, pas du titre — et
    la rappeler par section coûte autant d'appels réseau pour un seul résultat.
    """

    varie_par_section: bool

    async def collecter(self, sujet: str, requete: str = "") -> tuple[Affirmation, ...]:
        """Retourne les affirmations sourcées disponibles.

        Args:
            sujet: Sujet du document — c'est là que se lit la localité.
            requete: Requête propre à la section, pour les sources documentaires.
        """
        ...


# Plafond de la requête documentaire. Le titre et la consigne viennent d'un gabarit, et
# un gabarit est montable par ConfigMap : rien ne borne leur longueur au chargement. Sans
# ce plafond, une consigne démesurée déposée par erreur produirait un embedding refusé
# (donc N sections en lacune silencieuse) et un BM25 en O(corpus × mots) qui bloquerait
# la boucle d'événements. Même raison que `_MAX_SECTIONS` dans `services/gabarits.py`.
REQUETE_MAX = 400


def requete_de_section(section: SectionGabarit, sujet: str) -> str:
    """Compose la requête documentaire d'une section.

    Le titre et la consigne portent ce que la section cherche ; le sujet garde la
    recherche dans le bon périmètre — « production » seul ratisserait tout le corpus,
    toutes campagnes confondues.

    Cette requête sert au canal DENSE. Le canal lexical, lui, reste ancré sur le sujet
    seul : son seuil se calcule sur le nombre de mots de la requête, et l'allonger
    l'éteindrait (voir ``rag.passages_pour``).

    Args:
        section: Section déclarée par le gabarit.
        sujet: Sujet du document.

    Returns:
        La requête à soumettre à la source documentaire, bornée.
    """
    return " ".join(part for part in (section.titre, section.consigne, sujet) if part)[:REQUETE_MAX]


def _ligne_de_contexte(affirmation: Affirmation) -> str:
    """Rend une affirmation en une ligne bornée, sans consigne exécutable.

    Le texte vient du RAG ou d'un outil externe : on l'aplatit sur une ligne et on le
    tronque, pour qu'un passage verbeux ou mis en forme comme une instruction ne
    passe pas pour une consigne.

    Args:
        affirmation: Affirmation à présenter au modèle.

    Returns:
        La ligne de contexte, source comprise.
    """
    texte = " ".join(affirmation.texte.split())[:MAX_CARACTERES_AFFIRMATION]
    return f"- {texte} (source : {affirmation.source})"


@dataclass(frozen=True)
class ContexteGeneration:
    """Ce que le manifeste doit savoir de l'exécution en cours."""

    modele: str
    version_modele: str
    version_app: str
    profil_materiel: str


class MoteurRedaction:
    """Produit un ``Document`` à partir d'un gabarit et d'un sujet."""

    def __init__(
        self,
        inference: InferencePort,
        collecteurs: dict[str, CollecteurPort],
        contexte: ContexteGeneration,
    ) -> None:
        """Initialise le moteur.

        Args:
            inference: Port du modèle de langage qui rédige les sections.
            collecteurs: Sources mobilisables, indexées par le nom déclaré au gabarit.
            contexte: Éléments d'exécution reportés dans le manifeste.
        """
        self._inference = inference
        self._collecteurs = collecteurs
        self._contexte = contexte

    @staticmethod
    async def _appeler(
        collecteur: CollecteurPort, sujet: str, requete: str
    ) -> tuple[Affirmation, ...]:
        """Appelle un collecteur, qu'il connaisse ou non la requête de section.

        Un collecteur écrit avant l'introduction de la requête documentaire n'accepte
        qu'un argument. On ne casse pas le contrat sous ses pieds : on retombe sur
        l'ancien appel plutôt que de l'obliger à changer.

        Args:
            collecteur: Source à interroger.
            sujet: Sujet du document.
            requete: Requête propre à la section.

        Returns:
            Les affirmations rendues par la source.
        """
        # On INSPECTE la signature, on n'attrape pas une TypeError : une TypeError levée
        # à l'intérieur du collecteur ferait rejouer l'appel, effets de bord compris,
        # avant de finir en lacune silencieuse.
        parametres = inspect.signature(collecteur.collecter).parameters
        variadiques = {inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL}
        accepte_requete = len(parametres) >= 2 or any(
            parametre.kind in variadiques for parametre in parametres.values()
        )
        if accepte_requete:
            # Appel TOUT EN MOTS-CLÉS. En positionnel, une signature `(sujet, *, requete)`
            # ou `(**kwargs)` — le style déjà employé par `OutilPort` dans ce dépôt —
            # lève une TypeError, que le `except Exception` de `_collecter` transforme
            # en lacune silencieuse. En mots-clés, les trois formes passent, et une
            # signature qui n'attend pas `requete` échoue bruyamment au lieu de recevoir
            # la requête dans un paramètre qui ne l'attendait pas.
            return await collecteur.collecter(sujet=sujet, requete=requete)
        # Collecteur d'avant la requête documentaire : il reçoit le sujet seul, et on le
        # dit — une requête perdue en silence est invisible jusqu'au document produit.
        logger.warning("collecteur_sans_requete", collecteur=type(collecteur).__name__)
        return await collecteur.collecter(sujet)

    async def _collecter(
        self,
        section: SectionGabarit,
        sujet: str,
        partagees: dict[str, tuple[Affirmation, ...]],
    ) -> tuple[Affirmation, ...]:
        """Rassemble les affirmations des sources déclarées par une section.

        Une source injoignable ne fait pas tomber le document : elle ne contribue
        rien, et la section bascule en lacune si plus rien ne reste.

        Args:
            section: Section déclarée par le gabarit.
            sujet: Sujet du document.
            partagees: Résultats déjà obtenus des sources qui ne varient pas d'une
                section à l'autre, réutilisés au lieu d'être redemandés.

        Returns:
            Les affirmations effectivement sourcées.
        """
        recoltees: list[Affirmation] = []
        for nom in section.sources:
            collecteur = self._collecteurs.get(nom)
            if collecteur is None:
                logger.warning("collecteur_absent", source=nom, section=section.titre)
                continue
            # Une source qui ne varie pas par section n'est interrogée qu'une fois pour
            # tout le document — le prix officiel est le même au chapitre 1 et au 5.
            #
            # Le défaut est « varie », et c'est délibéré : une source qui oublie de se
            # déclarer coûte quelques appels de trop, alors que l'inverse ramènerait en
            # silence le défaut que ce mécanisme corrige — toutes les sections nourries
            # du même contexte. On se trompe du côté du correct, pas de l'économe.
            par_section = getattr(collecteur, "varie_par_section", True)
            if not par_section and nom in partagees:
                recoltees.extend(partagees[nom])
                continue
            try:
                obtenues = await self._appeler(
                    collecteur, sujet, requete_de_section(section, sujet)
                )
            except Exception as exc:  # noqa: BLE001 — une source ne fait pas tomber le document
                # Le type suffit au diagnostic ; le message d'une exception peut
                # embarquer une URL ou le sujet, qui n'ont rien à faire dans un log.
                logger.warning("collecteur_echoue", source=nom, error=type(exc).__name__)
                continue
            if not par_section:
                partagees[nom] = obtenues
            recoltees.extend(obtenues)
        # Défense en profondeur : une affirmation sans source ne rentre pas, même si
        # un collecteur en produisait une par erreur. On applique la règle CANONIQUE
        # du projet, pas un ``strip()`` plus faible : une source « n/a » traverserait,
        # entrerait au manifeste et ferait échouer ``affirmations_sans_source``.
        return tuple(
            affirmation for affirmation in recoltees if not source_absente(affirmation.source)
        )

    async def _rediger_section(
        self, section: SectionGabarit, sujet: str, affirmations: tuple[Affirmation, ...]
    ) -> Section:
        """Rédige une section, ou rend son constat de lacune.

        Args:
            section: Section déclarée par le gabarit.
            sujet: Sujet du document.
            affirmations: Affirmations collectées pour cette section.

        Returns:
            La section rédigée, ou une section en lacune.
        """
        if not affirmations:
            logger.info("section_en_lacune", section=section.titre)
            return Section(titre=section.titre, corps=_LACUNE, affirmations=(), lacune=True)

        contexte = "\n".join(
            _ligne_de_contexte(affirmation)
            for affirmation in affirmations[:MAX_AFFIRMATIONS_SECTION]
        )
        corps = await self._inference.generer(
            question=consigne_section(section, sujet),
            contexte=contexte,
            system_prompt=SYSTEM_PROMPT_REDACTION,
            # Sans ces deux-là, l'en-tête par défaut réinstallerait dans le tour
            # utilisateur ce que le prompt système vient d'interdire : « oriente vers
            # l'ANADER » et le cadrage « Question : ». Le tour utilisateur étant plus
            # proche de la génération, c'est lui que le modèle aurait suivi.
            entete_contexte=ENTETE_CONTEXTE_ANALYTIQUE,
            libelle_question=LIBELLE_SECTION,
            temperature=TEMPERATURE_SECTION,
            max_tokens=MAX_TOKENS_SECTION,
        )
        # On refuse la PRESCRIPTION, pas le chiffre. « verifier_reponse » se
        # declenche sur tout taux de dose, kg/ha compris : juste pour un conseil au
        # producteur, ou un faux positif ne coute qu'une redirection ; ruineux pour
        # une etude, ou un rendement est la donnee la plus courante qui soit.
        if guardrails.contient_prescription(corps):
            logger.warning("section_sortie_refusee", section=section.titre)
            return Section(titre=section.titre, corps=_LACUNE_REFUSEE, affirmations=(), lacune=True)
        return Section(titre=section.titre, corps=corps.strip(), affirmations=affirmations)

    async def _synthetiser(self, sections: list[Section], consigne: str) -> str:
        """Rédige une synthèse (résumé ou conclusion) à partir des sections ÉCRITES.

        Le contexte n'est pas le corpus mais le document lui-même : on ne redemande
        rien au RAG. C'est ce qui garantit qu'aucun fait nouveau n'apparaît en
        ouverture ou en clôture — les deux endroits qu'un lecteur pressé lit en
        premier, et donc les deux où une invention ferait le plus de dégâts.

        Args:
            sections: Sections déjà rédigées, lacunes comprises.
            consigne: Demande adressée au modèle (résumé ou conclusion).

        Returns:
            La synthèse, ou une chaîne vide si elle échoue ou est refusée — le rendu
            omet alors la rubrique plutôt que d'afficher un titre creux.
        """
        retenues = [s for s in sections if not s.lacune]
        if not retenues:
            return ""
        contexte = "\n\n".join(f"{s.titre} : {s.corps}" for s in retenues)
        try:
            texte = await self._inference.generer(
                question=consigne,
                contexte=contexte,
                system_prompt=SYSTEM_PROMPT_REDACTION,
                entete_contexte=ENTETE_CONTEXTE_ANALYTIQUE,
                libelle_question=LIBELLE_SECTION,
                temperature=TEMPERATURE_SECTION,
                max_tokens=MAX_TOKENS_SECTION,
            )
        except Exception as exc:  # noqa: BLE001 — une synthèse manquante n'invalide pas
            logger.warning("synthese_echec", error=str(exc))
            return ""
        if guardrails.contient_prescription(texte):
            logger.warning("synthese_refusee")
            return ""
        return texte.strip()

    async def rediger(
        self,
        gabarit: Gabarit,
        sujet: str,
        demandeur: str,
        progression: ProgressionRappel | None = None,
    ) -> Document:
        """Produit le document complet.

        Args:
            gabarit: Gabarit déclaratif fournissant le plan.
            sujet: Sujet du document, substitué dans le titre.
            demandeur: Identifiant du demandeur — seule son empreinte entre au manifeste.
            progression: Rappel appelé après chaque section, avec la section produite —
                c'est lui qui alimente le flux SSE.

        Returns:
            Le document assemblé, manifeste compris.

        Raises:
            SujetRefuse: Le sujet franchit un garde-fou métier.
        """
        # Le sujet vient d'une requête HTTP et atterrit dans le TITRE du document,
        # sans jamais passer par le modèle : il échapperait donc à tout garde-fou de
        # sortie. Un sujet portant un dosage produirait un livrable dont le titre le
        # porte ; un sujet hors filière produirait une étude sur l'anacarde signée
        # OpenCacao. Les deux sont non négociables (CLAUDE.md).
        refus = guardrails.evaluer(sujet) or guardrails.verifier_reponse(sujet)
        if refus is not None and refus.categorie in _REFUS_SUR_SUJET:
            logger.warning("sujet_refuse", categorie=refus.categorie.value)
            raise SujetRefuse(refus.message)

        sections: list[Section] = []
        total = len(gabarit.sections)
        # Partagé pour tout le document : les sources qui ne varient pas par section
        # ne sont interrogées qu'une fois.
        partagees: dict[str, tuple[Affirmation, ...]] = {}
        for index, declaree in enumerate(gabarit.sections, start=1):
            affirmations = await self._collecter(declaree, sujet, partagees)
            section = await self._rediger_section(declaree, sujet, affirmations)
            sections.append(section)
            if progression is not None:
                await progression(index, total, section)

        # (source, empreinte) — le contrat du manifeste. L'empreinte identifie
        # l'extrait mobilisé, pas seulement son émetteur : c'est ce qui permet de
        # rejouer la sélection documentaire, et donc le document.
        sources = tuple(
            dict.fromkeys(
                (affirmation.source, affirmation.empreinte)
                for section in sections
                for affirmation in section.affirmations
            )
        )
        manifeste = construire_manifeste(
            modele=self._contexte.modele,
            version_modele=self._contexte.version_modele,
            version_app=self._contexte.version_app,
            profil_materiel=self._contexte.profil_materiel,
            demandeur=demandeur,
            documents_rag=sources,
        )
        logger.info(
            "document_redige",
            gabarit=gabarit.identifiant,
            sections=total,
            lacunes=sum(1 for section in sections if section.lacune),
        )
        # Résumé et conclusion en DERNIER : ils synthétisent un document qui existe.
        # Les produire avant reviendrait à annoncer ce qu'on n'a pas encore écrit.
        resume = await self._synthetiser(
            sections,
            "Rédige le résumé d'ouverture de ce document, en un seul paragraphe de 500 "
            "à 700 caractères. Reprends ce que les sections établissent, sans ajouter "
            "aucun fait, aucun chiffre ni aucune date qui n'y figure pas.",
        )
        lacunes = sum(1 for section in sections if section.lacune)
        conclusion = await self._synthetiser(
            sections,
            "Rédige la conclusion de ce document, en un seul paragraphe de 500 à 700 "
            "caractères. Dis ce que le document établit, puis ce qu'il ne permet PAS "
            "d'établir"
            + (f" (dont {lacunes} section(s) sans source mobilisable)" if lacunes else "")
            + ". N'ajoute aucun fait nouveau et ne formule aucune recommandation "
            "d'action.",
        )
        return Document(
            titre=gabarit.titre.format(sujet=sujet),
            sous_titre=gabarit.sous_titre.format(sujet=sujet),
            sections=tuple(sections),
            # Socle analytique commun à TOUTE étude : la base probante du document,
            # comptée sur les affirmations réellement collectées. Rien n'est demandé au
            # modèle et rien n'est estimé — c'est ce qui permet d'en mettre partout.
            tableaux=tuple(
                tableau
                for tableau in (graphiques.tableau_des_sources(tuple(sections)),)
                if tableau is not None
            ),
            graphiques=graphiques.socle_analytique(tuple(sections)),
            manifeste=manifeste,
            mention=gabarit.mention,
            resume=resume,
            conclusion=conclusion,
        )
