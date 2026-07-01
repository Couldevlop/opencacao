"""Cas d'usage : produire un conseil agronomique.

Orchestre rate-limit, garde-fous, cache, inférence et journalisation en ne
dépendant que des ports du domaine. Testable sans FastAPI ni Redis.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from dataclasses import replace

from app.application import conseil_commun
from app.application.contexte import fil_ancre as _fil_ancre
from app.application.contexte import texte_conversation as _texte_conversation
from app.core.logging import get_logger
from app.domain.entities import Conseil
from app.domain.exceptions import RateLimitDepasse
from app.domain.ports import CachePort, EmbeddingsPort, InferencePort, JournalPort
from app.models.chat import DISCLAIMER
from app.models.domain import Confiance, Langue
from app.services import clarification, guardrails, postprocess
from app.services.rag import RagRecuperateur, couverture_lexicale

logger = get_logger(__name__)

# Fin de phrase suivie d'une espace : sert à ne livrer en streaming que des
# phrases complètes, scannées par le garde-fou de sortie AVANT émission.
_FIN_PHRASE = re.compile(r"[.!?…](?=\s)")


class ConseilService:
    """Cas d'usage central du conseil agronomique."""

    def __init__(
        self,
        inference: InferencePort,
        cache: CachePort,
        journal: JournalPort,
        rag: RagRecuperateur | None = None,
        embeddings: EmbeddingsPort | None = None,
        semantic_cache_threshold: float = 0.92,
        semantic_cache_lexical_min: float = 0.75,
    ) -> None:
        """Initialise le service avec ses dépendances (ports).

        Args:
            inference: Port d'inférence.
            cache: Port de cache/rate-limit.
            journal: Port de journalisation (jeu de données d'amélioration).
            rag: Récupérateur RAG optionnel (contexte injecté au prompt), ou None.
            embeddings: Service d'embeddings pour le cache sémantique, ou None
                (désactive la couche sémantique : repli sur l'exact-match seul).
            semantic_cache_threshold: Similarité cosinus minimale pour servir une
                réponse cachée sémantiquement proche.
            semantic_cache_lexical_min: Couverture lexicale minimale (garde-fou) des
                mots-clés de la question cachée par la question entrante.
        """
        self._inference = inference
        self._cache = cache
        self._journal = journal
        self._rag = rag
        self._embeddings = embeddings
        self._seuil_semantique = semantic_cache_threshold
        self._seuil_lexical = semantic_cache_lexical_min

    async def _vecteur_question(
        self, question: str, historique: list[dict[str, str]]
    ) -> list[float] | None:
        """Vectorise la question pour le cache sémantique (tour unique uniquement).

        Retourne None si la couche sémantique est désactivée, si on est en
        multi-tours (le cache ne sert pas en dialogue) ou si l'embedding échoue.
        """
        if self._embeddings is None or historique:
            return None
        vecteurs = await self._embeddings.embed([question])
        if not vecteurs:
            return None
        return vecteurs[0]

    async def _hit_semantique(
        self, question: str, langue: str, embedding: list[float] | None
    ) -> dict | None:
        """Réponse cachée sémantiquement proche ET lexicalement compatible, ou None.

        Deux conditions : similarité cosinus >= seuil (assurée par ``get_semantic``)
        ET garde-fou lexical — la question entrante doit reprendre les mots-clés de la
        question cachée. Ce dernier bloque un voisin sémantique au qualificatif
        divergent (« cacaoyer adulte » vs « cacaoyer jeune »), dont la réponse diffère.
        """
        if embedding is None:
            return None
        trouve = await self._cache.get_semantic(langue, embedding, self._seuil_semantique)
        if trouve is None:
            return None
        payload, question_cachee = trouve
        if couverture_lexicale(question_cachee, question) < self._seuil_lexical:
            logger.info("cache_semantique_rejet_lexical")
            return None
        return json.loads(payload)

    @staticmethod
    def _conseil_depuis_paquet(donnees: dict) -> Conseil:
        """Reconstruit un Conseil depuis un paquet de cache sérialisé."""
        return conseil_commun.depuis_paquet(donnees)

    async def _diffuser_cache(
        self, donnees: dict, texte_conv: str, question: str, langue: Langue
    ) -> AsyncIterator[dict]:
        """Diffuse en flux une réponse de cache (exact ou sémantique) puis le final."""
        conseil = self._enrichir_contact(self._conseil_depuis_paquet(donnees), texte_conv)
        for ev in _evenements_token(donnees["reponse"], conseil.reponse):
            yield ev
        yield await self._evenement_final(
            question,
            langue,
            conseil.reponse,
            conseil.sources,
            conseil.confiance,
            redirection=conseil.redirection_anader,
        )

    async def _contexte(self, requete: str) -> str | None:
        """Récupère le contexte RAG si activé (best-effort), sinon None.

        Args:
            requete: Requête de récupération (contextualisée en multi-tours).
        """
        if self._rag is None:
            return None
        return await self._rag.contexte_pour(requete)

    def _enrichir_contact(self, conseil: Conseil, texte_conversation: str) -> Conseil:
        """Ajoute le contact ANADER local exact si une mise en relation est pertinente.

        Délègue à :func:`conseil_commun.enrichir_contact` (logique partagée avec la
        plateforme agentique V3).
        """
        return conseil_commun.enrichir_contact(conseil, texte_conversation)

    async def conseiller(
        self,
        question: str,
        langue: Langue,
        client_ip: str,
        historique: list[dict[str, str]] | None = None,
    ) -> Conseil:
        """Produit un conseil pour la question donnée.

        Args:
            question: Question du producteur (déjà validée par le DTO).
            langue: Langue de la requête.
            client_ip: IP cliente, pour le rate-limit.
            historique: Tours précédents de la conversation (clarifications), ou None.

        Returns:
            Un objet Conseil (avec son interaction_id de journalisation).

        Raises:
            RateLimitDepasse: Si le quota par IP est dépassé.
            InferenceUnavailable: Si l'inférence échoue (propagée par le port).
        """
        historique = historique or []
        texte_conv = _texte_conversation(question, historique)

        # Garde-fous métier : refus sans appeler le modèle (réponse instantanée).
        # Évalués sur la question ANCRÉE au fil (B4) : une intention de dosage étalée
        # sur deux tours est ainsi interceptée comme si elle était posée d'un bloc.
        refus = guardrails.evaluer(_fil_ancre(question, historique))
        if refus is not None:
            logger.info("garde_fou_declenche", categorie=refus.categorie.value)
            conseil = Conseil(refus.message, Confiance.ELEVEE, [], redirection_anader=True)
            return await self._journaliser(
                question, langue, self._enrichir_contact(conseil, texte_conv)
            )

        # Clarification consultative : au 1er tour, on analyse et on pose des questions
        # complémentaires plutôt que de répondre à l'aveugle (réponse instantanée).
        clarif = clarification.analyser(question, historique)
        if clarif is not None:
            logger.info("clarification_demandee")
            conseil = Conseil(clarif, Confiance.MOYENNE, [], redirection_anader=False)
            return await self._journaliser(question, langue, conseil)

        # Cache de réponses (instantané) — uniquement en tour unique : une réponse
        # multi-tours dépend du contexte et ne doit pas polluer/servir le cache.
        # Exact d'abord (chemin le moins cher, sans embedding), puis sémantique.
        embedding: list[float] | None = None
        if not historique:
            cached = await self._cache.get_cached(question, langue.value)
            if cached is not None:
                conseil = self._conseil_depuis_paquet(json.loads(cached))
                return await self._journaliser(
                    question, langue, self._enrichir_contact(conseil, texte_conv)
                )
            embedding = await self._vecteur_question(question, historique)
            paquet = await self._hit_semantique(question, langue.value, embedding)
            if paquet is not None:
                logger.info("cache_semantique_hit")
                conseil = self._conseil_depuis_paquet(paquet)
                return await self._journaliser(
                    question, langue, self._enrichir_contact(conseil, texte_conv)
                )

        # Rate-limit UNIQUEMENT avant l'inférence réelle : un hit de cache ou un
        # refus instantané ne doit pas consommer le quota (équité).
        if await self._cache.hit_rate_limit(client_ip):
            raise RateLimitDepasse

        # Inférence (peut lever InferenceUnavailable), augmentée par RAG si activé.
        # La requête RAG est ré-ancrée sur le thème en cours (multi-tours) pour ne pas
        # récupérer des passages hors sujet sur une question de suivi.
        contexte = await self._contexte(_fil_ancre(question, historique))
        texte = await self._inference.generer(question, contexte=contexte, historique=historique)

        # Garde-fou de SORTIE (défense en profondeur) : ne jamais livrer un dosage.
        if guardrails.verifier_reponse(texte) is not None:
            logger.warning("garde_fou_sortie_declenche")
            conseil = Conseil(guardrails.REFUS_PHYTO, Confiance.ELEVEE, [], redirection_anader=True)
            return await self._journaliser(
                question, langue, self._enrichir_contact(conseil, texte_conv)
            )

        sources = postprocess.extraire_sources(texte, contexte)
        conseil = Conseil(
            reponse=texte,
            confiance=postprocess.estimer_confiance(sources),
            sources=sources,
            redirection_anader=False,
        )
        if not historique:
            await self._cache.set_cached(question, langue.value, _serialiser(conseil))
            if embedding is not None:
                await self._cache.index_semantic(question, langue.value, embedding)
        return await self._journaliser(
            question, langue, self._enrichir_contact(conseil, texte_conv)
        )

    async def prechauffer(self, question: str, langue: Langue) -> bool:
        """Génère et met en cache une réponse FAQ (pré-chauffage).

        Chemin allégé, distinct de :meth:`conseiller` : **pas de rate-limit** (action
        interne) ni de **journalisation** (le pré-chauffage ne doit pas polluer le
        jeu de données de curation). Idempotent : ne régénère pas une réponse déjà
        en cache.

        Args:
            question: Question fréquente à pré-calculer.
            langue: Langue de la réponse.

        Returns:
            True si une réponse a été générée et mise en cache ; False si elle y
            était déjà ou n'est pas cachable (refus / garde-fou de sortie).

        Raises:
            InferenceUnavailable: Si l'inférence échoue (propagée par le port).
        """
        if await self._cache.get_cached(question, langue.value) is not None:
            return False
        # Un refus est instantané (aucune inférence) : inutile de le mettre en cache.
        if guardrails.evaluer(question) is not None:
            return False

        contexte = await self._contexte(question)
        texte = await self._inference.generer(question, contexte=contexte)
        if guardrails.verifier_reponse(texte) is not None:
            return False

        sources = postprocess.extraire_sources(texte, contexte)
        conseil = Conseil(
            reponse=texte,
            confiance=postprocess.estimer_confiance(sources),
            sources=sources,
            redirection_anader=False,
        )
        await self._cache.set_cached(question, langue.value, _serialiser(conseil))
        return True

    async def conseiller_stream(
        self,
        question: str,
        langue: Langue,
        client_ip: str,
        historique: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[dict]:
        """Produit un conseil en flux, pour un rendu progressif côté client.

        Émet des événements ``{"type": "token", "text": ...}`` au fil de l'eau, puis
        un ``{"type": "done", ...}`` final (sources, confiance, disclaimer,
        interaction_id). Le garde-fou de sortie est appliqué phrase par phrase AVANT
        émission : aucune phrase contenant un dosage n'est diffusée. Le contact local
        vérifié est ajouté en fin de flux quand une mise en relation est pertinente.

        Args:
            question: Question du producteur (déjà validée par le DTO).
            langue: Langue de la requête.
            client_ip: IP cliente, pour le rate-limit.
            historique: Tours précédents de la conversation (clarifications), ou None.

        Yields:
            Des événements de flux (dictionnaires sérialisables).

        Raises:
            RateLimitDepasse: Si le quota par IP est dépassé.
            InferenceUnavailable: Si l'inférence échoue (propagée par le port).
        """
        historique = historique or []
        texte_conv = _texte_conversation(question, historique)

        refus = guardrails.evaluer(_fil_ancre(question, historique))
        if refus is not None:
            logger.info("garde_fou_declenche", categorie=refus.categorie.value)
            conseil = self._enrichir_contact(
                Conseil(refus.message, Confiance.ELEVEE, [], redirection_anader=True), texte_conv
            )
            for ev in _evenements_token(refus.message, conseil.reponse):
                yield ev
            yield await self._evenement_final(
                question,
                langue,
                conseil.reponse,
                conseil.sources,
                conseil.confiance,
                redirection=conseil.redirection_anader,
            )
            return

        # Clarification consultative (1er tour) : poser des questions complémentaires.
        clarif = clarification.analyser(question, historique)
        if clarif is not None:
            logger.info("clarification_demandee")
            yield {"type": "token", "text": clarif}
            yield await self._evenement_final(
                question, langue, clarif, [], Confiance.MOYENNE, redirection=False
            )
            return

        # Cache (tour unique) : exact d'abord (le moins cher), puis sémantique.
        embedding: list[float] | None = None
        if not historique:
            cached = await self._cache.get_cached(question, langue.value)
            if cached is not None:
                async for ev in self._diffuser_cache(
                    json.loads(cached), texte_conv, question, langue
                ):
                    yield ev
                return
            embedding = await self._vecteur_question(question, historique)
            paquet = await self._hit_semantique(question, langue.value, embedding)
            if paquet is not None:
                logger.info("cache_semantique_hit")
                async for ev in self._diffuser_cache(paquet, texte_conv, question, langue):
                    yield ev
                return

        # Rate-limit seulement avant l'inférence réelle (équité : cache/refus gratuits).
        if await self._cache.hit_rate_limit(client_ip):
            raise RateLimitDepasse

        emis: list[str] = []
        tampon = ""
        compromis = False

        contexte = await self._contexte(_fil_ancre(question, historique))
        async for delta in self._inference.generer_stream(
            question, contexte=contexte, historique=historique
        ):
            tampon += delta
            while (match := _FIN_PHRASE.search(tampon)) is not None:
                coupe = match.start() + 1
                phrase, tampon = tampon[:coupe], tampon[coupe:]
                if guardrails.verifier_reponse("".join(emis) + phrase) is not None:
                    compromis = True
                    break
                emis.append(phrase)
                yield {"type": "token", "text": phrase}
            if compromis:
                break

        if not compromis and tampon.strip():
            if guardrails.verifier_reponse("".join(emis) + tampon) is not None:
                compromis = True
            else:
                emis.append(tampon)
                yield {"type": "token", "text": tampon}

        if compromis:
            logger.warning("garde_fou_sortie_declenche")
            conseil = self._enrichir_contact(
                Conseil(guardrails.REFUS_PHYTO, Confiance.ELEVEE, [], redirection_anader=True),
                texte_conv,
            )
            yield {"type": "token", "text": " " + conseil.reponse}
            yield await self._evenement_final(
                question,
                langue,
                conseil.reponse,
                conseil.sources,
                conseil.confiance,
                redirection=conseil.redirection_anader,
            )
            return

        texte = "".join(emis)
        sources = postprocess.extraire_sources(texte)
        confiance = postprocess.estimer_confiance(sources)
        base = Conseil(texte, confiance, sources, redirection_anader=False)
        if not historique:
            await self._cache.set_cached(question, langue.value, _serialiser(base))
            if embedding is not None:
                await self._cache.index_semantic(question, langue.value, embedding)
        conseil = self._enrichir_contact(base, texte_conv)
        if conseil.reponse != texte:  # contact ajouté : on le diffuse aussi en flux
            yield {"type": "token", "text": conseil.reponse[len(texte) :]}
        yield await self._evenement_final(
            question,
            langue,
            conseil.reponse,
            conseil.sources,
            confiance,
            redirection=conseil.redirection_anader,
        )

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

    async def _evenement_final(
        self,
        question: str,
        langue: Langue,
        reponse: str,
        sources: list[str],
        confiance: Confiance,
        *,
        redirection: bool,
    ) -> dict:
        """Journalise puis construit l'événement terminal du flux (métadonnées)."""
        interaction_id = await self._journal.enregistrer_interaction(
            question, langue.value, reponse, confiance.value, sources, redirection
        )
        return {
            "type": "done",
            "sources": sources,
            "confiance": confiance.value,
            "redirection_anader": redirection,
            "disclaimer": DISCLAIMER,
            "interaction_id": interaction_id,
        }


def _evenements_token(texte_base: str, texte_enrichi: str) -> list[dict]:
    """Événements 'token' pour un texte envoyé d'un bloc, + le contact ajouté s'il y en a."""
    evenements = [{"type": "token", "text": texte_base}]
    if texte_enrichi != texte_base and texte_enrichi.startswith(texte_base):
        evenements.append({"type": "token", "text": texte_enrichi[len(texte_base) :]})
    return evenements


def _serialiser(conseil: Conseil) -> str:
    """Sérialise un conseil pour le cache (délègue à conseil_commun)."""
    return conseil_commun.serialiser(conseil)
