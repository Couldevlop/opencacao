"""Cas d'usage : produire un conseil agronomique.

Orchestre rate-limit, garde-fous, cache, inférence et journalisation en ne
dépendant que des ports du domaine. Testable sans FastAPI ni Redis.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from dataclasses import replace

from app.core.logging import get_logger
from app.domain.entities import Conseil
from app.domain.exceptions import RateLimitDepasse
from app.domain.ports import CachePort, InferencePort, JournalPort
from app.models.chat import DISCLAIMER
from app.models.domain import Confiance, Langue
from app.services import contacts, guardrails, postprocess
from app.services.rag import RagRecuperateur

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
    ) -> None:
        """Initialise le service avec ses dépendances (ports).

        Args:
            inference: Port d'inférence.
            cache: Port de cache/rate-limit.
            journal: Port de journalisation (jeu de données d'amélioration).
            rag: Récupérateur RAG optionnel (contexte injecté au prompt), ou None.
        """
        self._inference = inference
        self._cache = cache
        self._journal = journal
        self._rag = rag

    async def _contexte(self, question: str) -> str | None:
        """Récupère le contexte RAG si activé (best-effort), sinon None."""
        if self._rag is None:
            return None
        return await self._rag.contexte_pour(question)

    def _enrichir_contact(self, conseil: Conseil, texte_conversation: str) -> Conseil:
        """Ajoute le contact ANADER local exact si une mise en relation est pertinente.

        Le contact (numéro/adresse) provient de l'annuaire vérifié — jamais du modèle.
        Déclenché si la réponse oriente vers l'ANADER OU si l'utilisateur demande un
        contact, ET si une localité connue figure dans la conversation. Sinon (localité
        inconnue), on laisse le modèle demander la ville.

        Args:
            conseil: Conseil produit (avant enrichissement).
            texte_conversation: Texte cumulé de la conversation (pour repérer la ville).

        Returns:
            Le conseil, éventuellement enrichi du contact local.
        """
        if not (conseil.redirection_anader or contacts.intention_contact(texte_conversation)):
            return conseil
        contact = contacts.chercher(texte_conversation)
        if contact is None:
            return conseil
        ligne = contacts.formater(contact)
        if ligne in conseil.reponse:
            return conseil
        sources = conseil.sources if "ANADER" in conseil.sources else [*conseil.sources, "ANADER"]
        return replace(
            conseil,
            reponse=f"{conseil.reponse}\n\n{ligne}",
            sources=sources,
            redirection_anader=True,
        )

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
        refus = guardrails.evaluer(question)
        if refus is not None:
            logger.info("garde_fou_declenche", categorie=refus.categorie.value)
            conseil = Conseil(refus.message, Confiance.ELEVEE, [], redirection_anader=True)
            return await self._journaliser(
                question, langue, self._enrichir_contact(conseil, texte_conv)
            )

        # Cache de réponses (instantané) — uniquement en tour unique : une réponse
        # multi-tours dépend du contexte et ne doit pas polluer/servir le cache.
        if not historique:
            cached = await self._cache.get_cached(question, langue.value)
            if cached is not None:
                donnees = json.loads(cached)
                conseil = Conseil(
                    reponse=donnees["reponse"],
                    confiance=Confiance(donnees["confiance"]),
                    sources=donnees["sources"],
                    redirection_anader=donnees["redirection_anader"],
                )
                return await self._journaliser(
                    question, langue, self._enrichir_contact(conseil, texte_conv)
                )

        # Rate-limit UNIQUEMENT avant l'inférence réelle : un hit de cache ou un
        # refus instantané ne doit pas consommer le quota (équité).
        if await self._cache.hit_rate_limit(client_ip):
            raise RateLimitDepasse

        # Inférence (peut lever InferenceUnavailable), augmentée par RAG si activé.
        contexte = await self._contexte(question)
        texte = await self._inference.generer(question, contexte=contexte, historique=historique)

        # Garde-fou de SORTIE (défense en profondeur) : ne jamais livrer un dosage.
        if guardrails.verifier_reponse(texte) is not None:
            logger.warning("garde_fou_sortie_declenche")
            conseil = Conseil(guardrails.REFUS_PHYTO, Confiance.ELEVEE, [], redirection_anader=True)
            return await self._journaliser(
                question, langue, self._enrichir_contact(conseil, texte_conv)
            )

        sources = postprocess.extraire_sources(texte)
        conseil = Conseil(
            reponse=texte,
            confiance=postprocess.estimer_confiance(sources),
            sources=sources,
            redirection_anader=False,
        )
        if not historique:
            await self._cache.set_cached(question, langue.value, _serialiser(conseil))
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

        sources = postprocess.extraire_sources(texte)
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

        refus = guardrails.evaluer(question)
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

        if not historique:
            cached = await self._cache.get_cached(question, langue.value)
            if cached is not None:
                donnees = json.loads(cached)
                conseil = self._enrichir_contact(
                    Conseil(
                        donnees["reponse"],
                        Confiance(donnees["confiance"]),
                        donnees["sources"],
                        redirection_anader=donnees["redirection_anader"],
                    ),
                    texte_conv,
                )
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
                return

        # Rate-limit seulement avant l'inférence réelle (équité : cache/refus gratuits).
        if await self._cache.hit_rate_limit(client_ip):
            raise RateLimitDepasse

        emis: list[str] = []
        tampon = ""
        compromis = False

        contexte = await self._contexte(question)
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


def _texte_conversation(question: str, historique: list[dict[str, str]]) -> str:
    """Concatène les messages utilisateur (historique + question) pour repérer une ville."""
    parties = [t.get("content", "") for t in historique if t.get("role") == "user"]
    parties.append(question)
    return " ".join(parties)


def _evenements_token(texte_base: str, texte_enrichi: str) -> list[dict]:
    """Événements 'token' pour un texte envoyé d'un bloc, + le contact ajouté s'il y en a."""
    evenements = [{"type": "token", "text": texte_base}]
    if texte_enrichi != texte_base and texte_enrichi.startswith(texte_base):
        evenements.append({"type": "token", "text": texte_enrichi[len(texte_base) :]})
    return evenements


def _serialiser(conseil: Conseil) -> str:
    """Sérialise un conseil pour le cache (sans l'id de journalisation)."""
    return json.dumps(
        {
            "reponse": conseil.reponse,
            "confiance": conseil.confiance.value,
            "sources": conseil.sources,
            "redirection_anader": conseil.redirection_anader,
        }
    )
