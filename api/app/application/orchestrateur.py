"""Orchestrateur souverain : cas d'usage central de la plateforme agentique V3.

Enchaîne garde-fous d'entrée (centralisés) → routage d'intention → dispatch vers
l'agent retenu → garde-fou de sortie → journalisation. Les garde-fous métier ne
sont jamais réimplémentés par agent : centralisés ici, ils s'appliquent à tous les
agents — actuels comme à venir (Maladie, Satellite, Réglementation…).
"""

from __future__ import annotations

from dataclasses import replace

from app.application.contexte import fil_ancre
from app.application.routage import RouteurIntention
from app.core.logging import get_logger
from app.domain.agents import AgentPort, AgentRequete
from app.domain.entities import Conseil
from app.domain.exceptions import RateLimitDepasse
from app.domain.ports import CachePort, JournalPort
from app.models.domain import Confiance, Langue
from app.services import guardrails

logger = get_logger(__name__)


class Orchestrateur:
    """Pilote le traitement d'une requête par les agents spécialisés."""

    def __init__(
        self,
        routeur: RouteurIntention,
        journal: JournalPort,
        cache: CachePort,
        agent_defaut: str = "rag",
    ) -> None:
        """Initialise l'orchestrateur.

        Args:
            routeur: Routeur d'intention (classe les agents).
            journal: Port de journalisation des interactions.
            cache: Port de cache/rate-limit (rate-limit avant inférence réelle).
            agent_defaut: Nom de l'agent de repli si aucun routage n'aboutit.
        """
        self._routeur = routeur
        self._journal = journal
        self._cache = cache
        self._agent_defaut = agent_defaut

    async def traiter(
        self,
        question: str,
        langue: Langue,
        client_ip: str,
        historique: list[dict[str, str]] | None = None,
    ) -> Conseil:
        """Produit un conseil en routant la requête vers l'agent pertinent.

        Args:
            question: Dernière question du producteur.
            langue: Langue de la requête.
            client_ip: IP cliente (rate-limit).
            historique: Tours précédents de la conversation, ou None.

        Returns:
            Le conseil produit (refus, repli ou réponse d'agent), journalisé.

        Raises:
            RateLimitDepasse: Si le quota par IP est dépassé.
            AgentIndisponible: Si l'agent retenu échoue (propagée).
        """
        historique = historique or []
        fil = fil_ancre(question, historique)

        # 1. Garde-fous d'entrée CENTRALISÉS : refus sans solliciter d'agent.
        refus = guardrails.evaluer(fil)
        if refus is not None:
            logger.info("garde_fou_declenche", categorie=refus.categorie.value)
            conseil = Conseil(refus.message, Confiance.ELEVEE, [], redirection_anader=True)
            return await self._journaliser(question, langue, conseil)

        requete = AgentRequete(
            question=question,
            langue=langue,
            fil_ancre=fil,
            client_ip=client_ip,
            historique=historique,
        )

        # 2. Routage d'intention → agent (repli sur l'agent par défaut).
        agent = await self._routeur.meilleur(requete)
        if agent is None:
            agent = self._agent_de_repli()
        logger.info("dispatch", agent=agent.nom if agent else None)
        if agent is None:
            conseil = Conseil(
                "Service momentanément indisponible.",
                Confiance.FAIBLE,
                [],
                redirection_anader=True,
            )
            return await self._journaliser(question, langue, conseil)

        # 3. Rate-limit UNIQUEMENT avant l'inférence réelle (équité : refus gratuits).
        if await self._cache.hit_rate_limit(client_ip):
            raise RateLimitDepasse

        # 4. Dispatch vers l'agent.
        reponse = await agent.traiter(requete)

        # 5. Garde-fou de SORTIE (défense en profondeur).
        if guardrails.verifier_reponse(reponse.texte) is not None:
            logger.warning("garde_fou_sortie_declenche", agent=agent.nom)
            conseil = Conseil(guardrails.REFUS_PHYTO, Confiance.ELEVEE, [], redirection_anader=True)
            return await self._journaliser(question, langue, conseil)

        conseil = Conseil(
            reponse=reponse.texte,
            confiance=reponse.confiance,
            sources=reponse.sources,
            redirection_anader=reponse.redirection_anader,
        )
        return await self._journaliser(question, langue, conseil)

    def _agent_de_repli(self) -> AgentPort | None:
        """Retourne l'agent de repli (RAG par défaut), ou None s'il est absent."""
        return self._routeur._registre.obtenir(self._agent_defaut)  # noqa: SLF001

    async def _journaliser(self, question: str, langue: Langue, conseil: Conseil) -> Conseil:
        """Journalise l'interaction et renvoie le conseil enrichi de son id."""
        interaction_id = await self._journal.enregistrer_interaction(
            question,
            langue.value,
            conseil.reponse,
            conseil.confiance.value,
            conseil.sources,
            conseil.redirection_anader,
        )
        return replace(conseil, interaction_id=interaction_id)
