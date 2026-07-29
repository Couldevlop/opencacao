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

**Un seul garde-fou de sortie, et c'est un choix.** ``verifier_reponse`` s'applique :
un document d'étude ne prescrit pas plus qu'un conseil au producteur, les dosages
restent non négociables. ``contient_diagnostic`` **ne s'applique pas** (arbitrage
Waopron du 29/07/2026) : ce verrou est celui du constat visuel — nommer une atteinte
à partir d'une *photo*, sans jeu de données pour mesurer le rappel. Une étude qui
rapporte, sources à l'appui, qu'une maladie affecte la filière énonce un fait
documenté ; l'interdire rendrait toute section agronomique impossible à écrire.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.application.provenance import construire_manifeste
from app.core.logging import get_logger
from app.domain.ports import InferencePort
from app.models.rapport import Affirmation, Document, Section
from app.services import guardrails
from app.services.gabarits import Gabarit, SectionGabarit
from app.services.prompts_redaction import SYSTEM_PROMPT_REDACTION, consigne_section

logger = get_logger(__name__)

# Une section tient en un paragraphe de 600 à 800 caractères : on borne la génération
# en conséquence. Le levier reste la consigne, ce plafond n'est qu'un garde-corps.
MAX_TOKENS_SECTION = 320

# Température basse : un document d'analyse doit être reproductible, pas créatif.
TEMPERATURE_SECTION = 0.3

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

ProgressionRappel = Callable[[int, int, str], Awaitable[None]]


@runtime_checkable
class CollecteurPort(Protocol):
    """Contrat d'une source mobilisable par une section."""

    async def collecter(self, sujet: str) -> tuple[Affirmation, ...]:
        """Retourne les affirmations sourcées disponibles pour ce sujet."""
        ...


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

    async def _collecter(self, section: SectionGabarit, sujet: str) -> tuple[Affirmation, ...]:
        """Rassemble les affirmations des sources déclarées par une section.

        Une source injoignable ne fait pas tomber le document : elle ne contribue
        rien, et la section bascule en lacune si plus rien ne reste.

        Args:
            section: Section déclarée par le gabarit.
            sujet: Sujet du document, transmis à chaque source.

        Returns:
            Les affirmations effectivement sourcées.
        """
        recoltees: list[Affirmation] = []
        for nom in section.sources:
            collecteur = self._collecteurs.get(nom)
            if collecteur is None:
                logger.warning("collecteur_absent", source=nom, section=section.titre)
                continue
            try:
                recoltees.extend(await collecteur.collecter(sujet))
            except Exception as exc:  # noqa: BLE001 — une source ne fait pas tomber le document
                logger.warning("collecteur_echoue", source=nom, error=str(exc))
        # Défense en profondeur : une affirmation sans source ne rentre pas, même si
        # un collecteur en produisait une par erreur.
        return tuple(affirmation for affirmation in recoltees if affirmation.source.strip())

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
            f"- {affirmation.texte} (source : {affirmation.source})" for affirmation in affirmations
        )
        corps = await self._inference.generer(
            question=consigne_section(section, sujet),
            contexte=contexte,
            system_prompt=SYSTEM_PROMPT_REDACTION,
            temperature=TEMPERATURE_SECTION,
            max_tokens=MAX_TOKENS_SECTION,
        )
        if guardrails.verifier_reponse(corps) is not None:
            logger.warning("section_sortie_refusee", section=section.titre)
            return Section(titre=section.titre, corps=_LACUNE_REFUSEE, affirmations=(), lacune=True)
        return Section(titre=section.titre, corps=corps.strip(), affirmations=affirmations)

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
            progression: Rappel appelé après chaque section — c'est lui qui alimente
                le flux SSE.

        Returns:
            Le document assemblé, manifeste compris.
        """
        sections: list[Section] = []
        total = len(gabarit.sections)
        for index, declaree in enumerate(gabarit.sections, start=1):
            affirmations = await self._collecter(declaree, sujet)
            sections.append(await self._rediger_section(declaree, sujet, affirmations))
            if progression is not None:
                await progression(index, total, declaree.titre)

        sources = tuple(
            dict.fromkeys(
                (affirmation.source, affirmation.date)
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
        return Document(
            titre=gabarit.titre.format(sujet=sujet),
            sous_titre=gabarit.sous_titre.format(sujet=sujet),
            sections=tuple(sections),
            tableaux=(),
            manifeste=manifeste,
            mention=gabarit.mention,
        )
