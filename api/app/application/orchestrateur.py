"""Orchestrateur souverain : cas d'usage central de la plateforme agentique V3.

Enchaîne, autour du dispatch, les concerns transverses (parité avec ConseilService
V2) : garde-fous d'entrée → clarification consultative → cache exact → routage →
cache sémantique (hors intention de synthèse) → rate-limit → dispatch → garde-fou
de sortie → enrichissement contact ANADER → journalisation. Les garde-fous, la
clarification et l'enrichissement ne sont jamais
réimplémentés par agent : centralisés ici, ils s'appliquent à tous les agents —
actuels comme à venir (Maladie, Satellite, Réglementation EUDR…).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import replace

from app.application import conseil_commun, flux
from app.application.cache_semantique import CacheSemantique
from app.application.contexte import fil_ancre, texte_conversation
from app.application.routage import RouteurIntention
from app.core.logging import get_logger
from app.domain.agents import AgentPort, AgentReponse, AgentRequete
from app.domain.entities import Conseil
from app.domain.exceptions import RateLimitDepasse
from app.domain.ports import CachePort, JournalPort
from app.models.domain import Confiance, Langue
from app.services import clarification, guardrails, postprocess

logger = get_logger(__name__)

# Composition multi-agents : nombre maximal de contributions fusionnées par un
# synthétiseur. Borne la latence — l'inférence prod est CPU (~15-46 s/génération) et
# composer = N générations séquentielles + 1 synthèse. Plafond 2 → au plus 3 générations.
MAX_CONTRIBUTEURS = 2

# Message de progression émis en TÊTE de composition : garantit un PREMIER OCTET
# immédiat alors que le fan-out CPU dure ~1-2 min → évite le time-out edge Cloudflare
# (524, mesuré sur réponse synchrone). Un libellé est ré-émis avant chaque contribution
# (heartbeat anti-524) et avant la synthèse ; le front les affiche dans l'indicateur
# d'attente, les clients qui ignorent le type « progress » restent compatibles.
_PROGRES_COMPOSITION = "Je consulte les agents spécialisés…"

# Libellés de progression par agent : affichés pendant l'attente (dispatch mono-agent
# et contributions de composition). Un agent absent de la table retombe sur le libellé
# générique — jamais d'erreur pour un agent futur.
PROGRES_AGENTS: dict[str, str] = {
    "rag": "Je recherche dans la documentation cacao…",
    "meteo": "Je consulte les prévisions météo…",
    "prix": "Je vérifie le prix officiel du cacao…",
    "satellite": "Je consulte les données satellite (Global Forest Watch)…",
    "reglementation": "Je consulte la réglementation…",
    "normes": "Je consulte les référentiels de certification…",
    "reporting": "Je prépare votre bilan…",
}


class Orchestrateur:
    """Pilote le traitement d'une requête par les agents spécialisés."""

    def __init__(
        self,
        routeur: RouteurIntention,
        journal: JournalPort,
        cache: CachePort,
        agent_defaut: str = "rag",
        cache_semantique: CacheSemantique | None = None,
    ) -> None:
        """Initialise l'orchestrateur.

        Args:
            routeur: Routeur d'intention (classe les agents).
            journal: Port de journalisation des interactions.
            cache: Port de cache/rate-limit (rate-limit avant inférence réelle).
            agent_defaut: Nom de l'agent de repli si aucun routage n'aboutit.
            cache_semantique: Couche de cache sémantique (paraphrases), ou None → une
                instance inerte (exact-match seul, comme avant l'ajout de la couche).
        """
        self._routeur = routeur
        self._journal = journal
        self._cache = cache
        self._agent_defaut = agent_defaut
        self._semantique = cache_semantique or CacheSemantique(cache, embeddings=None)

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
        texte_conv = texte_conversation(question, historique)

        # 1. Garde-fous d'entrée CENTRALISÉS : refus sans solliciter d'agent.
        refus = guardrails.evaluer(fil)
        if refus is not None:
            logger.info("garde_fou_declenche", categorie=refus.categorie.value)
            conseil = conseil_commun.enrichir_contact(
                Conseil(refus.message, Confiance.ELEVEE, [], redirection_anader=True), texte_conv
            )
            return await self._journaliser(question, langue, conseil)

        # 2. Clarification consultative (1er tour) : poser des questions plutôt que
        #    répondre à l'aveugle. Parité V2 — instantané, aucune inférence.
        clarif = clarification.analyser(question, historique)
        if clarif is not None:
            logger.info("clarification_demandee")
            conseil = Conseil(clarif, Confiance.MOYENNE, [], redirection_anader=False)
            return await self._journaliser(question, langue, conseil)

        # 3. Cache EXACT (tour unique) : réponse instantanée, parité V2. Le cache stocke
        #    le conseil NON enrichi ; l'enrichissement contact, qui dépend de la
        #    conversation, est appliqué à chaque requête.
        if not historique:
            cached = await self._cache.get_cached(question, langue.value)
            if cached is not None:
                logger.info("cache_hit")
                conseil = conseil_commun.enrichir_contact(
                    conseil_commun.depuis_paquet(json.loads(cached)), texte_conv
                )
                return await self._journaliser(question, langue, conseil)

        requete = AgentRequete(
            question=question,
            langue=langue,
            fil_ancre=fil,
            client_ip=client_ip,
            historique=historique,
        )

        # 4. Routage d'intention AVANT le cache sémantique : le classement révèle une
        #    intention de SYNTHÈSE, qu'un voisin mono-agent caché ne doit jamais servir
        #    (vécu prod : « bilan météo+prix » servi par la seule réponse météo cachée).
        classement = await self._routeur.classer(requete)
        synthetiseur = self._synthetiseur(classement)

        # 5. Cache SÉMANTIQUE (tour unique, hors intention de synthèse) : sert une
        #    paraphrase cachée. L'embedding calculé ici est réutilisé à l'écriture
        #    (pas de double vectorisation) ; une intention de synthèse ne consulte ni
        #    n'alimente l'index (embedding laissé à None).
        embedding: list[float] | None = None
        if not historique and synthetiseur is None:
            embedding = await self._semantique.vecteur(question, historique)
            paquet = await self._semantique.hit(question, langue.value, embedding)
            if paquet is not None:
                logger.info("cache_semantique_hit")
                conseil = conseil_commun.enrichir_contact(
                    conseil_commun.depuis_paquet(paquet), texte_conv
                )
                return await self._journaliser(question, langue, conseil)

        # 6. Dispatch mono-agent (le mieux classé, repli sur l'agent par défaut).
        #    La composition multi-agents (synthèse) ne s'active QUE sur le flux
        #    (traiter_stream) : elle enchaîne plusieurs générations CPU (~3 min) qui
        #    dépasseraient le time-out edge Cloudflare (~100 s) sur une réponse
        #    SYNCHRONE → 524. Ici, un seul agent répond, même si un synthétiseur
        #    figure au classement.
        agent = classement[0][0] if classement else self._agent_de_repli()
        logger.info("dispatch", agent=agent.nom if agent else None)
        if agent is None:
            conseil = Conseil(
                "Service momentanément indisponible.",
                Confiance.FAIBLE,
                [],
                redirection_anader=True,
            )
            return await self._journaliser(question, langue, conseil)

        # 7. Rate-limit UNIQUEMENT avant l'inférence réelle (équité : refus/cache gratuits).
        if await self._cache.hit_rate_limit(client_ip):
            raise RateLimitDepasse

        # 8. Dispatch vers l'agent.
        reponse = await agent.traiter(requete)

        # 9. Garde-fou de SORTIE (défense en profondeur).
        if guardrails.verifier_reponse(reponse.texte) is not None:
            logger.warning("garde_fou_sortie_declenche", agent=agent.nom)
            conseil = conseil_commun.enrichir_contact(
                Conseil(guardrails.REFUS_PHYTO, Confiance.ELEVEE, [], redirection_anader=True),
                texte_conv,
            )
            return await self._journaliser(question, langue, conseil)

        conseil = Conseil(
            reponse=reponse.texte,
            confiance=reponse.confiance,
            sources=reponse.sources,
            redirection_anader=reponse.redirection_anader,
        )
        # 10. Cache exact + index sémantique (tour unique), enrichissement, journalisation.
        if not historique:
            await self._cache.set_cached(question, langue.value, conseil_commun.serialiser(conseil))
            await self._semantique.indexer(question, langue.value, embedding)
        return await self._journaliser(
            question, langue, conseil_commun.enrichir_contact(conseil, texte_conv)
        )

    async def traiter_stream(
        self,
        question: str,
        langue: Langue,
        client_ip: str,
        historique: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[dict]:
        """Variante flux de :meth:`traiter` (mêmes étapes, sortie progressive SSE).

        Émet des événements ``{"type": "token", ...}`` au fil de l'eau puis un
        ``{"type": "done", ...}`` final. Le garde-fou de sortie est appliqué phrase
        par phrase AVANT émission (aucun dosage diffusé). Refus, clarification et
        hits de cache sont émis d'un bloc. Parité avec ConseilService.conseiller_stream.

        Raises:
            RateLimitDepasse: Si le quota par IP est dépassé (avant l'inférence).
        """
        # 0. Progression immédiate : premier octet instantané sur TOUS les chemins
        #    (anti-524) + statut affiché par le front pendant l'attente.
        yield flux.progres(flux.PROGRES_ANALYSE)

        historique = historique or []
        fil = fil_ancre(question, historique)
        texte_conv = texte_conversation(question, historique)

        # 1. Garde-fou d'entrée (refus émis d'un bloc).
        refus = guardrails.evaluer(fil)
        if refus is not None:
            logger.info("garde_fou_declenche", categorie=refus.categorie.value)
            conseil = conseil_commun.enrichir_contact(
                Conseil(refus.message, Confiance.ELEVEE, [], redirection_anader=True), texte_conv
            )
            for ev in flux.evenements_token(refus.message, conseil.reponse):
                yield ev
            yield await flux.evenement_final(
                self._journal,
                question,
                langue,
                conseil.reponse,
                conseil.sources,
                conseil.confiance,
                redirection=conseil.redirection_anader,
            )
            return

        # 2. Clarification consultative (émise d'un bloc).
        clarif = clarification.analyser(question, historique)
        if clarif is not None:
            logger.info("clarification_demandee")
            yield {"type": "token", "text": clarif}
            yield await flux.evenement_final(
                self._journal, question, langue, clarif, [], Confiance.MOYENNE, redirection=False
            )
            return

        # 3. Cache EXACT (tour unique) : réponse cachée émise d'un bloc.
        paquet: dict | None = None
        if not historique:
            cached = await self._cache.get_cached(question, langue.value)
            if cached is not None:
                logger.info("cache_hit")
                paquet = json.loads(cached)

        requete = AgentRequete(
            question=question,
            langue=langue,
            fil_ancre=fil,
            client_ip=client_ip,
            historique=historique,
        )

        # 4. Routage AVANT le cache sémantique : le classement révèle une intention de
        #    SYNTHÈSE (synthétiseur présent), qu'un voisin mono-agent caché ne doit
        #    jamais servir (vécu prod : « bilan météo+prix » servi par la seule réponse
        #    météo cachée, composition court-circuitée). Inutile sur hit exact.
        classement: list = []
        synthetiseur = None
        embedding: list[float] | None = None
        if paquet is None:
            classement = await self._routeur.classer(requete)
            synthetiseur = self._synthetiseur(classement)

            # 5. Cache SÉMANTIQUE (tour unique, hors intention de synthèse). L'embedding
            #    est réutilisé à l'écriture ; une intention de synthèse ne consulte ni
            #    n'alimente l'index (embedding laissé à None).
            if not historique and synthetiseur is None:
                embedding = await self._semantique.vecteur(question, historique)
                paquet = await self._semantique.hit(question, langue.value, embedding)
                if paquet is not None:
                    logger.info("cache_semantique_hit")

        if paquet is not None:
            base = conseil_commun.depuis_paquet(paquet)
            conseil = conseil_commun.enrichir_contact(base, texte_conv)
            for ev in flux.evenements_token(base.reponse, conseil.reponse):
                yield ev
            yield await flux.evenement_final(
                self._journal,
                question,
                langue,
                conseil.reponse,
                conseil.sources,
                conseil.confiance,
                redirection=conseil.redirection_anader,
            )
            return

        agent = classement[0][0] if classement else self._agent_de_repli()
        cible = synthetiseur or agent
        logger.info(
            "dispatch", agent=cible.nom if cible else None, composition=synthetiseur is not None
        )
        if cible is None:
            indispo = "Service momentanément indisponible."
            yield {"type": "token", "text": indispo}
            yield await flux.evenement_final(
                self._journal, question, langue, indispo, [], Confiance.FAIBLE, redirection=True
            )
            return

        # 6. Rate-limit avant l'inférence réelle.
        if await self._cache.hit_rate_limit(client_ip):
            raise RateLimitDepasse

        # 7a. Composition multi-agents (flux) : fan-out avec PROGRESSION, puis synthèse
        #     streamée. Le premier octet (« progress ») part immédiatement → pas de 524
        #     Cloudflare même si le fan-out CPU dure ~1-2 min ; un heartbeat suit chaque
        #     contribution. La synthèse est ensuite streamée token par token, filtrée par
        #     le garde-fou de sortie phrase par phrase.
        if synthetiseur is not None:
            yield flux.progres(_PROGRES_COMPOSITION)
            contributions: list[AgentReponse] = []
            for contributeur in self._contributeurs(classement, synthetiseur):
                yield flux.progres(PROGRES_AGENTS.get(contributeur.nom, flux.PROGRES_REDACTION))
                contributions.append(await contributeur.traiter(requete))
            yield flux.progres(flux.PROGRES_REDACTION)
            logger.info(
                "composition",
                synthetiseur=synthetiseur.nom,
                contributeurs=[c.agent for c in contributions],
            )

            filtre = flux.FiltreSortie()
            async for phrase in filtre.diffuser(
                synthetiseur.synthetiser_stream(requete, contributions)  # type: ignore[attr-defined]
            ):
                yield {"type": "token", "text": phrase}

            if filtre.compromis:
                logger.warning("garde_fou_sortie_declenche", agent=synthetiseur.nom)
                conseil = conseil_commun.enrichir_contact(
                    Conseil(guardrails.REFUS_PHYTO, Confiance.ELEVEE, [], redirection_anader=True),
                    texte_conv,
                )
                yield {"type": "token", "text": " " + conseil.reponse}
                yield await flux.evenement_final(
                    self._journal,
                    question,
                    langue,
                    conseil.reponse,
                    conseil.sources,
                    conseil.confiance,
                    redirection=conseil.redirection_anader,
                )
                return

            texte = filtre.texte
            sources, confiance = synthetiseur.agreger(contributions)  # type: ignore[attr-defined]
            base = Conseil(texte, confiance, sources, redirection_anader=False)
            if not historique:
                await self._cache.set_cached(
                    question, langue.value, conseil_commun.serialiser(base)
                )
                await self._semantique.indexer(question, langue.value, embedding)
            conseil = conseil_commun.enrichir_contact(base, texte_conv)
            if conseil.reponse != texte:  # contact ajouté : on le diffuse aussi
                yield {"type": "token", "text": conseil.reponse[len(texte) :]}
            yield await flux.evenement_final(
                self._journal,
                question,
                langue,
                conseil.reponse,
                conseil.sources,
                confiance,
                redirection=conseil.redirection_anader,
            )
            return

        # 7b. Mono-agent : vrai streaming token-par-token + garde-fou de sortie phrase par phrase.
        # Le libellé de l'agent est émis AVANT le dispatch : il couvre l'appel d'outil
        # et le préremplissage CPU (~15-26 s), la plus longue attente silencieuse.
        # Contexte calculé une fois : passé au stream ET réutilisé pour ANCRER les
        # sources après coup (souveraineté : confiance non gonflée par une citation
        # de mémoire non ancrée).
        yield flux.progres(PROGRES_AGENTS.get(agent.nom, flux.PROGRES_REDACTION))
        contexte = await agent.contexte_pour(requete)
        filtre = flux.FiltreSortie()
        async for phrase in filtre.diffuser(agent.traiter_stream(requete, contexte)):
            yield {"type": "token", "text": phrase}

        if filtre.compromis:
            logger.warning("garde_fou_sortie_declenche", agent=agent.nom)
            conseil = conseil_commun.enrichir_contact(
                Conseil(guardrails.REFUS_PHYTO, Confiance.ELEVEE, [], redirection_anader=True),
                texte_conv,
            )
            yield {"type": "token", "text": " " + conseil.reponse}
            yield await flux.evenement_final(
                self._journal,
                question,
                langue,
                conseil.reponse,
                conseil.sources,
                conseil.confiance,
                redirection=conseil.redirection_anader,
            )
            return

        # 8. Post-traitement : sources, confiance, cache, enrichissement, événement final.
        texte = filtre.texte
        sources = postprocess.extraire_sources(texte, contexte)
        confiance = postprocess.estimer_confiance(sources)
        base = Conseil(texte, confiance, sources, redirection_anader=False)
        if not historique:
            await self._cache.set_cached(question, langue.value, conseil_commun.serialiser(base))
            await self._semantique.indexer(question, langue.value, embedding)
        conseil = conseil_commun.enrichir_contact(base, texte_conv)
        if conseil.reponse != texte:  # contact ajouté : on le diffuse aussi en flux
            yield {"type": "token", "text": conseil.reponse[len(texte) :]}
        yield await flux.evenement_final(
            self._journal,
            question,
            langue,
            conseil.reponse,
            conseil.sources,
            confiance,
            redirection=conseil.redirection_anader,
        )

    def _agent_de_repli(self) -> AgentPort | None:
        """Retourne l'agent de repli (RAG par défaut), ou None s'il est absent."""
        return self._routeur.registre.obtenir(self._agent_defaut)

    @staticmethod
    def _synthetiseur(classement: list[tuple[AgentPort, float]]) -> AgentPort | None:
        """Premier agent SYNTHÉTISEUR de flux présent dans le classement.

        Détecté par duck-typing sur ``synthetiser_stream`` — la méthode réellement
        appelée par la composition en flux — plutôt qu'un nom en dur : tout futur agent
        de synthèse streamable déclenche la composition sans modifier l'orchestrateur.
        On teste la PRÉSENCE dans le classement, pas la tête : « bilan météo et prix »
        fait souvent gagner un spécialiste au score, mais le mot de synthèse suffit à
        vouloir composer.
        """
        for agent, _ in classement:
            if hasattr(agent, "synthetiser_stream"):
                return agent
        return None

    def _contributeurs(
        self, classement: list[tuple[AgentPort, float]], synthetiseur: AgentPort
    ) -> list[AgentPort]:
        """Agents contributeurs d'une synthèse (fan-out), bornés pour la latence.

        Les autres agents du classement (hors synthétiseur), triés par score et
        plafonnés à :data:`MAX_CONTRIBUTEURS`. Si aucun n'est classé, on prend l'agent
        de repli — le synthétiseur a toujours au moins une analyse à fusionner.
        """
        contributeurs = [agent for agent, _ in classement if agent is not synthetiseur]
        contributeurs = contributeurs[:MAX_CONTRIBUTEURS]
        if not contributeurs:
            repli = self._agent_de_repli()
            contributeurs = [repli] if repli is not None else []
        return contributeurs

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
