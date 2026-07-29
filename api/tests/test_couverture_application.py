"""Branches résiduelles de la couche application (orchestrateur, flux, mémoire, agents).

Complète les suites existantes (``tests/agents/test_orchestrateur.py``,
``tests/test_conseil_service.py``, ``tests/test_memoire.py``) sur les chemins encore
non exercés : indisponibilité totale d'agent, refus et hits de cache **en flux**,
quota consommé par une clarification générée par le modèle, diffusion du contact
ANADER en fin de flux, garde-fou de sortie sur un fragment final sans ponctuation,
et repli de la mémoire quand il n'y a rien à résumer.

Aucun appel réseau : inférence, cache et journal sont les doubles de ``conftest``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from app.application import flux, memoire
from app.application.conseil_commun import serialiser
from app.application.conseil_service import ConseilService
from app.application.orchestrateur import Orchestrateur
from app.application.registre import RegistreAgents
from app.application.routage import RouteurIntention
from app.domain.agents import AgentReponse, AgentRequete
from app.domain.entities import Conseil
from app.domain.exceptions import RateLimitDepasse
from app.models.domain import Confiance, Langue
from app.services import guardrails
from app.services.agents.base import AgentBase
from tests.conftest import FakeCache, FakeInference, FakeJournal

# Question de mise en relation : intention de contact (« numéro ») ET localité connue
# (Daloa) — donc aucune clarification, mais un enrichissement contact en fin de réponse.
QUESTION_CONTACT = "Quel est le numéro de l'ANADER à Daloa pour ma plantation de cacao ?"

# Question factuelle neutre : ni garde-fou, ni clarification, ni cache.
QUESTION_NEUTRE = "comment tailler le cacaoyer ?"


# --------------------------------------------------------------------------------------
# Doubles d'agents (le contrat AgentPort n'a pas de double partagé dans conftest)
# --------------------------------------------------------------------------------------


class _AgentFactice:
    """Agent factice streamable, qui mémorise s'il a été sollicité."""

    def __init__(
        self,
        nom: str = "rag",
        score: float = 1.0,
        fragments: tuple[str, ...] = ("Taillez en saison sèche. ",),
        contexte: str | None = None,
    ) -> None:
        """Initialise l'agent factice.

        Args:
            nom: Nom de l'agent (clé de registre).
            score: Score d'aptitude renvoyé par ``peut_traiter``.
            fragments: Fragments émis en flux (et concaténés en synchrone).
            contexte: Contexte documentaire prétendu injecté.
        """
        self.nom = nom
        self.description = nom
        self.mots_cles = (nom,)
        self._score = score
        self._fragments = fragments
        self._contexte = contexte
        self.sollicite = False

    async def peut_traiter(self, requete: AgentRequete) -> float:
        """Score d'aptitude fixe."""
        return self._score

    async def traiter(self, requete: AgentRequete) -> AgentReponse:
        """Réponse synchrone (concaténation des fragments)."""
        self.sollicite = True
        return AgentReponse("".join(self._fragments), [], Confiance.MOYENNE, self.nom)

    async def contexte_pour(self, requete: AgentRequete) -> str | None:
        """Contexte prétendu injecté par l'agent."""
        return self._contexte

    async def traiter_stream(
        self, requete: AgentRequete, contexte: str | None = None
    ) -> AsyncIterator[str]:
        """Flux des fragments."""
        self.sollicite = True
        for fragment in self._fragments:
            yield fragment


class _AgentSynthetiseur(_AgentFactice):
    """Agent de synthèse streamable : déclenche la composition multi-agents."""

    async def synthetiser_stream(
        self, requete: AgentRequete, contributions: list[AgentReponse]
    ) -> AsyncIterator[str]:
        """Fusionne les contributions (ici : émet ses propres fragments)."""
        self.sollicite = True
        for fragment in self._fragments:
            yield fragment

    @staticmethod
    def agreger(contributions: list[AgentReponse]) -> tuple[list[str], Confiance]:
        """Agrège sources et confiance des contributions."""
        return [], Confiance.MOYENNE


def _orchestrateur(
    *agents: _AgentFactice,
    journal: FakeJournal | None = None,
    cache: FakeCache | None = None,
    defaut: str = "rag",
    inference: FakeInference | None = None,
    dialogue_naturel: bool = False,
) -> Orchestrateur:
    """Assemble un orchestrateur sur un registre ad hoc (cache/journal factices)."""
    registre = RegistreAgents()
    for agent in agents:
        registre.enregistrer(agent)
    return Orchestrateur(
        RouteurIntention(registre, seuil=0.3),
        journal or FakeJournal(),
        cache or FakeCache(),
        agent_defaut=defaut,
        inference=inference,
        dialogue_naturel=dialogue_naturel,
    )


async def _flux(orch: Orchestrateur, question: str, **kwargs: object) -> list[dict]:
    """Collecte tous les événements d'un flux d'orchestrateur."""
    return [e async for e in orch.traiter_stream(question, Langue.FR, "ip", **kwargs)]


def _texte(evenements: list[dict]) -> str:
    """Concatène le texte des événements « token » d'un flux."""
    return "".join(e["text"] for e in evenements if e["type"] == "token")


# --------------------------------------------------------------------------------------
# Orchestrateur — aucune capacité disponible
# --------------------------------------------------------------------------------------


async def test_sans_aucun_agent_disponible_le_conseil_annonce_l_indisponibilite() -> None:
    """Aucun agent classé ni agent de repli : on annonce l'indisponibilité, sans planter.

    Le producteur reçoit une réponse honnête (confiance faible, redirection ANADER)
    plutôt qu'une erreur 500, et l'interaction reste journalisée.
    """
    journal = FakeJournal()
    muet = _AgentFactice("muet", score=0.0)  # sous le seuil de routage
    orch = _orchestrateur(muet, journal=journal, defaut="agent-absent")

    conseil = await orch.traiter(QUESTION_NEUTRE, Langue.FR, "ip")

    assert conseil.confiance is Confiance.FAIBLE
    assert conseil.redirection_anader is True
    assert "indisponible" in conseil.reponse
    assert muet.sollicite is False
    assert len(journal.interactions) == 1  # l'indisponibilité est tracée


async def test_stream_sans_aucun_agent_disponible_annonce_l_indisponibilite() -> None:
    """Même honnêteté en flux : un message d'indisponibilité puis l'événement final."""
    journal = FakeJournal()
    muet = _AgentFactice("muet", score=0.0)
    orch = _orchestrateur(muet, journal=journal, defaut="agent-absent")

    evenements = await _flux(orch, QUESTION_NEUTRE)

    assert "indisponible" in _texte(evenements)
    assert evenements[-1]["type"] == "done"
    assert evenements[-1]["confiance"] == Confiance.FAIBLE.value
    assert evenements[-1]["redirection_anader"] is True
    assert len(journal.interactions) == 1


# --------------------------------------------------------------------------------------
# Orchestrateur — quota et clarification formulée par le modèle
# --------------------------------------------------------------------------------------


async def test_clarification_naturelle_consomme_le_quota() -> None:
    """Une clarification formulée par le modèle est une inférence : elle est soumise au quota.

    Contrairement à la clarification scriptée (gratuite), le mode naturel appelle le
    modèle : le rate-limit doit donc s'appliquer avant de générer la question.
    """
    orch = _orchestrateur(
        _AgentFactice(),
        cache=FakeCache(rate_limit=0),
        inference=FakeInference(reponse="Sur quelle partie ?"),
        dialogue_naturel=True,
    )

    with pytest.raises(RateLimitDepasse):
        await orch.traiter("Mes feuilles jaunissent", Langue.FR, "ip")


async def test_stream_clarification_naturelle_consomme_le_quota() -> None:
    """Même règle en flux : le quota est vérifié avant de générer la clarification."""
    orch = _orchestrateur(
        _AgentFactice(),
        cache=FakeCache(rate_limit=0),
        inference=FakeInference(reponse="Sur quelle partie ?"),
        dialogue_naturel=True,
    )

    with pytest.raises(RateLimitDepasse):
        await _flux(orch, "Mes feuilles jaunissent")


async def test_stream_rate_limit_avant_toute_inference() -> None:
    """Quota dépassé : aucun agent n'est sollicité, l'appel échoue avant l'inférence."""
    agent = _AgentFactice()
    orch = _orchestrateur(agent, cache=FakeCache(rate_limit=0))

    with pytest.raises(RateLimitDepasse):
        await _flux(orch, QUESTION_NEUTRE)

    assert agent.sollicite is False


# --------------------------------------------------------------------------------------
# Orchestrateur — flux : refus d'entrée et cache exact
# --------------------------------------------------------------------------------------


async def test_stream_refus_garde_fou_emis_sans_solliciter_d_agent() -> None:
    """Une question hors filière est refusée d'un bloc en flux, sans appeler d'agent.

    Le refus est diffusé comme du texte (le client voit une réponse normale) et
    l'événement final porte la redirection ANADER.
    """
    journal = FakeJournal()
    agent = _AgentFactice()
    orch = _orchestrateur(agent, journal=journal)

    evenements = await _flux(orch, "comment cultiver le maïs ?")

    assert agent.sollicite is False
    assert "ANADER" in _texte(evenements)
    assert evenements[-1]["type"] == "done"
    assert evenements[-1]["redirection_anader"] is True
    assert len(journal.interactions) == 1


async def test_stream_cache_exact_sert_la_reponse_sans_solliciter_d_agent() -> None:
    """Un hit de cache exact est diffusé tel quel, sans routage ni génération."""
    cache = FakeCache()
    await cache.set_cached(
        QUESTION_NEUTRE,
        Langue.FR.value,
        serialiser(Conseil("Taillez en saison sèche.", Confiance.ELEVEE, ["CNRA"])),
    )
    agent = _AgentFactice(fragments=("ne devrait pas être généré",))
    orch = _orchestrateur(agent, cache=cache)

    evenements = await _flux(orch, QUESTION_NEUTRE)

    assert _texte(evenements) == "Taillez en saison sèche."
    assert agent.sollicite is False
    assert evenements[-1]["sources"] == ["CNRA"]


# --------------------------------------------------------------------------------------
# Orchestrateur — flux : diffusion du contact ANADER ajouté après coup
# --------------------------------------------------------------------------------------


async def test_stream_mono_agent_diffuse_le_contact_ajoute() -> None:
    """Le contact ANADER ajouté après la génération est aussi diffusé en flux.

    Sans cela, le client en streaming afficherait une réponse amputée du contact que
    l'événement final annonce pourtant (incohérence flux/métadonnées).
    """
    orch = _orchestrateur(_AgentFactice(fragments=("Voici ce qu'il faut savoir. ",)))

    evenements = await _flux(orch, QUESTION_CONTACT)

    texte = _texte(evenements)
    assert "Voici ce qu'il faut savoir" in texte
    assert "ANADER" in texte  # le contact vérifié est bien diffusé
    assert evenements[-1]["redirection_anader"] is True
    assert "ANADER" in evenements[-1]["sources"]


async def test_stream_composition_diffuse_le_contact_ajoute() -> None:
    """Même exigence sur le chemin composé : la synthèse streamée reçoit aussi le contact."""
    rag = _AgentFactice("rag", score=0.4)
    reporting = _AgentSynthetiseur("reporting", score=0.8, fragments=("Synthèse du bilan. ",))
    orch = _orchestrateur(rag, reporting)

    evenements = await _flux(orch, QUESTION_CONTACT)

    texte = _texte(evenements)
    assert "Synthèse du bilan" in texte
    assert "ANADER" in texte
    assert evenements[-1]["redirection_anader"] is True


# --------------------------------------------------------------------------------------
# ConseilService (V2) — quota de la clarification naturelle
# --------------------------------------------------------------------------------------


def _service_naturel() -> ConseilService:
    """ConseilService en mode dialogue naturel, avec un quota déjà épuisé."""
    return ConseilService(
        inference=FakeInference(reponse="Sur quelle partie ?"),
        cache=FakeCache(rate_limit=0),
        journal=FakeJournal(),
        dialogue_naturel=True,
    )


async def test_conseil_service_clarification_naturelle_consomme_le_quota() -> None:
    """V2 : la clarification générée par le modèle est soumise au rate-limit."""
    with pytest.raises(RateLimitDepasse):
        await _service_naturel().conseiller("Mes feuilles jaunissent", Langue.FR, "ip")


async def test_conseil_service_stream_clarification_naturelle_consomme_le_quota() -> None:
    """V2 en flux : le quota est vérifié avant de générer la clarification."""
    service = _service_naturel()
    with pytest.raises(RateLimitDepasse):
        [e async for e in service.conseiller_stream("Mes feuilles jaunissent", Langue.FR, "ip")]


# --------------------------------------------------------------------------------------
# flux.py — garde-fou de sortie et événements « token »
# --------------------------------------------------------------------------------------


async def _emettre(morceaux: list[str]) -> AsyncIterator[str]:
    """Simule un flux d'inférence à partir d'une liste de fragments."""
    for morceau in morceaux:
        yield morceau


async def test_filtre_sortie_retient_un_reliquat_compromis(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un reliquat final SANS ponctuation compromis n'est jamais diffusé.

    Le découpage phrase par phrase ne voit pas la fin d'une réponse tronquée : le
    garde-fou doit aussi contrôler ce reliquat avant émission (défense en profondeur).
    On simule le déclenchement sans écrire de dosage (interdit, même en test).
    """
    monkeypatch.setattr(guardrails, "verifier_reponse", lambda texte: object())
    filtre = flux.FiltreSortie()

    phrases = [p async for p in filtre.diffuser(_emettre(["reliquat sans ponctuation finale"]))]

    assert phrases == []
    assert filtre.compromis is True
    assert filtre.texte == ""


def test_evenements_token_diffuse_le_contact_ajoute() -> None:
    """Le contact ajouté à une réponse envoyée d'un bloc part comme un second token."""
    evenements = flux.evenements_token("Réponse.", "Réponse.\n\nANADER — Siège")

    assert [e["type"] for e in evenements] == ["token", "token"]
    assert evenements[0]["text"] == "Réponse."
    assert evenements[1]["text"] == "\n\nANADER — Siège"


def test_evenements_token_sans_enrichissement_reste_unique() -> None:
    """Sans contact ajouté, un seul token est émis (pas de doublon de texte)."""
    assert flux.evenements_token("Réponse.", "Réponse.") == [{"type": "token", "text": "Réponse."}]


# --------------------------------------------------------------------------------------
# memoire.py — rien à résumer
# --------------------------------------------------------------------------------------


def test_fenetre_couvrant_tout_l_historique_laisse_l_historique_intact() -> None:
    """Si la fenêtre couvre tout l'historique, il n'y a rien d'ancien : aucun résumé.

    Le contexte transmis au modèle ne doit alors pas être préfixé d'un en-tête de
    résumé vide, qui gonflerait le prompt sans rien apporter.
    """
    historique = [
        {"role": "user", "content": "Question 1 sur le cacao"},
        {"role": "assistant", "content": "Conseil 1"},
        {"role": "user", "content": "Question 2 sur le cacao"},
        {"role": "assistant", "content": "Conseil 2"},
    ]

    fenetre = memoire.fenetre_dialogue(historique, fenetre=4, seuil=2)

    assert fenetre == historique


def test_tours_anciens_vides_ne_produisent_pas_de_resume() -> None:
    """Des tours anciens sans contenu ne fabriquent pas un résumé creux.

    Un résumé réduit à son seul en-tête n'apporte rien : on ne transmet alors que la
    fenêtre des tours récents.
    """
    historique = [
        {"role": "assistant", "content": ""},
        {"role": "assistant", "content": ""},
        {"role": "user", "content": "Question récente sur le cacao"},
        {"role": "assistant", "content": "Conseil récent"},
    ]

    fenetre = memoire.fenetre_dialogue(historique, fenetre=2, seuil=2)

    assert fenetre == historique[2:]
    assert all("Résumé de nos échanges" not in m["content"] for m in fenetre)


# --------------------------------------------------------------------------------------
# services/agents/base.py — contexte du squelette d'agent
# --------------------------------------------------------------------------------------


class _InferenceQuiEnregistre(FakeInference):
    """Inférence factice qui mémorise le contexte reçu (ignoré par ``FakeInference``)."""

    def __init__(self, reponse: str | None = None) -> None:
        """Initialise l'inférence et son journal de contextes."""
        super().__init__(reponse=reponse)
        self.contextes: list[object] = []

    async def generer(self, question: str, **kwargs: object) -> str:
        """Mémorise le contexte puis délègue à la réponse fixe."""
        self.contextes.append(kwargs.get("contexte"))
        return await super().generer(question, **kwargs)

    async def generer_stream(self, question: str, **kwargs: object) -> AsyncIterator[str]:
        """Mémorise le contexte puis délègue au flux fixe."""
        self.contextes.append(kwargs.get("contexte"))
        async for fragment in super().generer_stream(question, **kwargs):
            yield fragment


class _AgentAncre(AgentBase):
    """Agent concret minimal : fournit un contexte documentaire et compte ses calculs."""

    nom = "ancre"

    def __init__(self, inference: _InferenceQuiEnregistre) -> None:
        """Initialise l'agent et son compteur de calculs de contexte."""
        super().__init__(inference)
        self.calculs = 0

    async def _contexte(self, requete: AgentRequete) -> str | None:
        """Contexte spécifique (compté, pour vérifier qui le calcule)."""
        self.calculs += 1
        return "[1] (source : CNRA) Taille du cacaoyer."


def _requete(question: str = QUESTION_NEUTRE) -> AgentRequete:
    """Requête d'agent normalisée pour les tests."""
    return AgentRequete(question=question, langue=Langue.FR, fil_ancre=question, client_ip="ip")


async def test_agent_base_n_injecte_aucun_contexte() -> None:
    """Le squelette d'agent n'invente aucun ancrage : il génère sans contexte.

    Seul un agent concret sait construire son contexte ; la base doit donc annoncer
    l'absence d'ancrage plutôt qu'un contexte fabriqué.
    """
    inference = _InferenceQuiEnregistre(reponse="Réponse sans ancrage.")
    agent = AgentBase(inference)

    assert await agent.contexte_pour(_requete()) is None

    reponse = await agent.traiter(_requete())

    assert inference.contextes == [None]
    assert reponse.sources == []
    assert reponse.confiance is Confiance.FAIBLE


async def test_traiter_stream_calcule_le_contexte_si_l_appelant_ne_le_fournit_pas() -> None:
    """Appelé sans contexte pré-calculé, l'agent reste autonome et le calcule lui-même.

    L'orchestrateur pré-calcule le contexte (pour ancrer les sources) ; tout autre
    appelant doit obtenir la même génération ancrée sans avoir à le faire.
    """
    inference = _InferenceQuiEnregistre(reponse="Taillez en saison sèche.")
    agent = _AgentAncre(inference)

    fragments = [f async for f in agent.traiter_stream(_requete())]

    assert "".join(fragments) == "Taillez en saison sèche."
    assert agent.calculs == 1  # calculé par l'agent, une seule fois
    assert inference.contextes == ["[1] (source : CNRA) Taille du cacaoyer."]
