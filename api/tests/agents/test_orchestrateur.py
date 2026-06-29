"""L'orchestrateur : garde-fous centralisés, routage, dispatch, journalisation."""

from __future__ import annotations

import pytest

from app.application.orchestrateur import Orchestrateur
from app.application.registre import RegistreAgents
from app.application.routage import RouteurIntention
from app.domain.agents import AgentReponse, AgentRequete
from app.models.domain import Confiance, Langue


class _AgentEspion:
    def __init__(self, nom: str, score: float, texte: str) -> None:
        self.nom = nom
        self.description = nom
        self.mots_cles = (nom,)
        self._score = score
        self._texte = texte
        self.recue: AgentRequete | None = None

    async def peut_traiter(self, requete: AgentRequete) -> float:
        return self._score

    async def traiter(self, requete: AgentRequete) -> AgentReponse:
        self.recue = requete
        return AgentReponse(self._texte, ["CNRA"], Confiance.ELEVEE, self.nom)


class _JournalFactice:
    def __init__(self) -> None:
        self.interactions: list[tuple] = []

    async def enregistrer_interaction(self, *args: object) -> str:
        self.interactions.append(args)
        return "id-1"

    async def enregistrer_feedback(self, interaction_id: str, vote: str) -> None: ...
    async def enregistrer_visite(self, *a: object) -> None: ...


class _CacheFactice:
    def __init__(self, limite: bool = False) -> None:
        self._limite = limite

    async def get_cached(self, q: str, lg: str) -> str | None:
        return None

    async def set_cached(self, q: str, lg: str, payload: str) -> None: ...
    async def get_semantic(self, lg, emb, th):
        return None

    async def index_semantic(self, q, lg, emb) -> None: ...
    async def hit_rate_limit(self, ip: str) -> bool:
        return self._limite

    async def ping(self) -> bool:
        return True


def _orchestrateur(*agents, journal=None, cache=None, defaut="rag") -> Orchestrateur:
    registre = RegistreAgents()
    for a in agents:
        registre.enregistrer(a)
    routeur = RouteurIntention(registre, seuil=0.3)
    return Orchestrateur(
        routeur,
        journal or _JournalFactice(),
        cache or _CacheFactice(),
        agent_defaut=defaut,
    )


@pytest.mark.asyncio
async def test_route_vers_agent_le_plus_pertinent() -> None:
    rag = _AgentEspion("rag", 0.4, "conseil RAG")
    meteo = _AgentEspion("meteo", 0.9, "conseil météo")
    orch = _orchestrateur(rag, meteo)
    # Question factuelle (sans déclencher de clarification) : on vérifie le routage.
    conseil = await orch.traiter("à quelle période récolter le cacao ?", Langue.FR, "ip")
    assert conseil.reponse == "conseil météo"
    assert meteo.recue is not None
    assert rag.recue is None


@pytest.mark.asyncio
async def test_repli_sur_agent_defaut_si_aucun_routage() -> None:
    rag = _AgentEspion("rag", 0.0, "conseil RAG")
    orch = _orchestrateur(rag, defaut="rag")
    conseil = await orch.traiter("question vague", Langue.FR, "ip")
    assert conseil.reponse == "conseil RAG"  # repli RAG


@pytest.mark.asyncio
async def test_garde_fou_entree_court_circuite_les_agents() -> None:
    rag = _AgentEspion("rag", 1.0, "ne devrait pas répondre")
    orch = _orchestrateur(rag)
    # Question hors filière (maïs) → refus ANADER sans appeler d'agent.
    conseil = await orch.traiter("Comment cultiver le maïs ?", Langue.FR, "ip")
    assert conseil.redirection_anader is True
    assert rag.recue is None


@pytest.mark.asyncio
async def test_journalise_l_interaction() -> None:
    journal = _JournalFactice()
    rag = _AgentEspion("rag", 1.0, "conseil")
    orch = _orchestrateur(rag, journal=journal)
    conseil = await orch.traiter("comment tailler le cacaoyer ?", Langue.FR, "ip")
    assert conseil.interaction_id == "id-1"
    assert len(journal.interactions) == 1


@pytest.mark.asyncio
async def test_rate_limit_avant_inference() -> None:
    from app.domain.exceptions import RateLimitDepasse

    rag = _AgentEspion("rag", 1.0, "conseil")
    orch = _orchestrateur(rag, cache=_CacheFactice(limite=True))
    with pytest.raises(RateLimitDepasse):
        await orch.traiter("comment tailler le cacaoyer ?", Langue.FR, "ip")


@pytest.mark.asyncio
async def test_garde_fou_sortie_remplace_la_reponse(monkeypatch: pytest.MonkeyPatch) -> None:
    # Défense en profondeur : si la sortie d'un agent est jugée compromise par le
    # garde-fou de sortie, l'orchestrateur la remplace par le refus phyto et redirige.
    # On simule le déclenchement sans écrire de dosage (interdit, même en test).
    from app.services import guardrails

    monkeypatch.setattr(guardrails, "verifier_reponse", lambda texte: object())
    rag = _AgentEspion("rag", 1.0, "réponse de l'agent")
    orch = _orchestrateur(rag)
    conseil = await orch.traiter("comment tailler le cacaoyer ?", Langue.FR, "ip")
    assert conseil.redirection_anader is True
    # La réponse compromise est remplacée par le refus phyto (éventuellement enrichi
    # du contact ANADER, puisque la redirection est active).
    assert guardrails.REFUS_PHYTO in conseil.reponse
    assert "réponse de l'agent" not in conseil.reponse


class _CacheCompteur(_CacheFactice):
    def __init__(self) -> None:
        super().__init__()
        self.appels_rate = 0

    async def hit_rate_limit(self, ip: str) -> bool:
        self.appels_rate += 1
        return False


@pytest.mark.asyncio
async def test_refus_entree_ne_consomme_pas_le_quota() -> None:
    # Équité : un refus de garde-fou ne doit pas décompter le quota (pas d'inférence).
    cache = _CacheCompteur()
    rag = _AgentEspion("rag", 1.0, "ne devrait pas répondre")
    orch = _orchestrateur(rag, cache=cache)
    await orch.traiter("comment cultiver le maïs ?", Langue.FR, "ip")
    assert cache.appels_rate == 0
    assert rag.recue is None


@pytest.mark.asyncio
async def test_clarification_court_circuite_les_agents() -> None:
    # Parité V2 : un symptôme déclenche une salve de clarification avant tout dispatch.
    rag = _AgentEspion("rag", 1.0, "ne devrait pas répondre")
    orch = _orchestrateur(rag)
    conseil = await orch.traiter("les feuilles de mon cacaoyer jaunissent", Langue.FR, "ip")
    assert "?" in conseil.reponse  # le système pose des questions complémentaires
    assert rag.recue is None


class _CacheAvecEntree(_CacheFactice):
    def __init__(self, paquet: str) -> None:
        super().__init__()
        self._paquet = paquet

    async def get_cached(self, q: str, lg: str) -> str | None:
        return self._paquet


@pytest.mark.asyncio
async def test_cache_hit_court_circuite_les_agents() -> None:
    # Parité V2 : une réponse en cache (tour unique) est servie sans appeler d'agent.
    import json

    paquet = json.dumps(
        {
            "reponse": "réponse cachée",
            "confiance": "elevee",
            "sources": ["CNRA"],
            "redirection_anader": False,
        }
    )
    rag = _AgentEspion("rag", 1.0, "ne devrait pas répondre")
    orch = _orchestrateur(rag, cache=_CacheAvecEntree(paquet))
    conseil = await orch.traiter("comment tailler le cacaoyer ?", Langue.FR, "ip")
    assert conseil.reponse == "réponse cachée"
    assert rag.recue is None


class _AgentAnader:
    nom = "rag"
    description = "x"
    mots_cles = ()

    async def peut_traiter(self, requete: AgentRequete) -> float:
        return 1.0

    async def traiter(self, requete: AgentRequete) -> AgentReponse:
        return AgentReponse(
            "Rapprochez-vous de l'ANADER.", [], Confiance.MOYENNE, "rag", redirection_anader=True
        )


@pytest.mark.asyncio
async def test_enrichit_le_contact_anader_quand_la_reponse_oriente() -> None:
    # Parité V2 : une réponse orientant vers l'ANADER est enrichie du contact local.
    orch = _orchestrateur(_AgentAnader())
    conseil = await orch.traiter("que faire pour mon cacao ?", Langue.FR, "ip")
    assert conseil.redirection_anader is True
    assert "ANADER" in conseil.sources


class _AgentStream:
    nom = "rag"
    description = "x"
    mots_cles = ()

    def __init__(self, fragments: list[str]) -> None:
        self._fragments = fragments

    async def peut_traiter(self, requete: AgentRequete) -> float:
        return 1.0

    async def traiter(self, requete: AgentRequete) -> AgentReponse:
        return AgentReponse("".join(self._fragments), [], Confiance.MOYENNE, "rag")

    async def traiter_stream(self, requete: AgentRequete):
        for fragment in self._fragments:
            yield fragment


@pytest.mark.asyncio
async def test_traiter_stream_emet_des_phrases_puis_done() -> None:
    agent = _AgentStream(["Taillez en saison sèche. ", "Sources : CNRA."])
    orch = _orchestrateur(agent)
    evenements = [
        e async for e in orch.traiter_stream("comment tailler le cacaoyer ?", Langue.FR, "ip")
    ]
    assert evenements[-1]["type"] == "done"
    tokens = [e for e in evenements if e["type"] == "token"]
    assert len(tokens) >= 1
    texte = "".join(e["text"] for e in tokens)
    assert "saison sèche" in texte
    assert "CNRA" in evenements[-1]["sources"]


@pytest.mark.asyncio
async def test_traiter_stream_garde_fou_sortie(monkeypatch: pytest.MonkeyPatch) -> None:
    # Le garde-fou de sortie phrase par phrase ne diffuse pas la phrase compromise.
    from app.services import guardrails

    monkeypatch.setattr(guardrails, "verifier_reponse", lambda texte: object())
    agent = _AgentStream(["Phrase compromise. "])
    orch = _orchestrateur(agent)
    evenements = [
        e async for e in orch.traiter_stream("comment tailler le cacaoyer ?", Langue.FR, "ip")
    ]
    texte = "".join(e["text"] for e in evenements if e["type"] == "token")
    assert "Phrase compromise" not in texte
    assert guardrails.REFUS_PHYTO in texte
    assert evenements[-1]["redirection_anader"] is True


@pytest.mark.asyncio
async def test_traiter_stream_clarification_emise_en_bloc() -> None:
    agent = _AgentStream(["ne devrait pas répondre"])
    orch = _orchestrateur(agent)
    evenements = [
        e
        async for e in orch.traiter_stream(
            "les feuilles de mon cacaoyer jaunissent", Langue.FR, "ip"
        )
    ]
    texte = "".join(e["text"] for e in evenements if e["type"] == "token")
    assert "?" in texte  # questions de clarification
    assert "ne devrait pas répondre" not in texte
