"""L'orchestrateur : garde-fous centralisés, routage, dispatch, journalisation."""

from __future__ import annotations

import json

import pytest

from app.application import flux as flux_module
from app.application import orchestrateur as orchestrateur_module
from app.application.cache_semantique import CacheSemantique
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
    def __init__(self, limite: bool = False, semantic: tuple[str, str] | None = None) -> None:
        self._limite = limite
        self._semantic = semantic
        self.indexes: list[tuple[str, str, list[float]]] = []
        self.set: list[tuple[str, str, str]] = []

    async def get_cached(self, q: str, lg: str) -> str | None:
        return None

    async def set_cached(self, q: str, lg: str, payload: str) -> None:
        self.set.append((q, lg, payload))

    async def get_semantic(self, lg, emb, th):
        return self._semantic

    async def index_semantic(self, q, lg, emb) -> None:
        self.indexes.append((q, lg, emb))

    async def hit_rate_limit(self, ip: str) -> bool:
        return self._limite

    async def ping(self) -> bool:
        return True


class _EmbeddingsFactice:
    async def embed(self, textes: list[str]) -> list[list[float]] | None:
        return [[1.0, 0.0] for _ in textes]


def _semantique(cache: _CacheFactice, *, actif: bool = True) -> CacheSemantique:
    # Garde-fou lexical permissif (0.0) : on teste le branchement, pas le seuil lexical
    # (couvert dans test_cache_semantique.py).
    return CacheSemantique(
        cache, _EmbeddingsFactice() if actif else None, seuil=0.9, seuil_lexical=0.0
    )


def _orchestrateur(*agents, journal=None, cache=None, defaut="rag", cache_semantique=None):
    registre = RegistreAgents()
    for a in agents:
        registre.enregistrer(a)
    routeur = RouteurIntention(registre, seuil=0.3)
    return Orchestrateur(
        routeur,
        journal or _JournalFactice(),
        cache or _CacheFactice(),
        agent_defaut=defaut,
        cache_semantique=cache_semantique,
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

    def __init__(self, fragments: list[str], contexte: str | None = None) -> None:
        self._fragments = fragments
        self._contexte = contexte

    async def peut_traiter(self, requete: AgentRequete) -> float:
        return 1.0

    async def traiter(self, requete: AgentRequete) -> AgentReponse:
        return AgentReponse("".join(self._fragments), [], Confiance.MOYENNE, "rag")

    async def contexte_pour(self, requete: AgentRequete) -> str | None:
        return self._contexte

    async def traiter_stream(self, requete: AgentRequete, contexte: str | None = None):
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
async def test_traiter_stream_ancre_les_sources_sur_le_contexte() -> None:
    # Souveraineté en streaming : seules les sources ANCRÉES dans le contexte comptent.
    # Le modèle cite CNRA ET ANADER, mais seul CNRA est dans le contexte injecté.
    agent = _AgentStream(
        ["D'après le CNRA et l'ANADER, taillez en saison sèche. Sources : CNRA, ANADER."],
        contexte="[1] (source : CNRA) Taille du cacaoyer en saison sèche.",
    )
    orch = _orchestrateur(agent)
    evenements = [e async for e in orch.traiter_stream("comment tailler ?", Langue.FR, "ip")]
    # ANADER cité de mémoire (absent du contexte) est écarté : seul CNRA (ancré) reste.
    assert evenements[-1]["sources"] == ["CNRA"]


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


class _AgentSynthetiseur:
    """Espion : agent de synthèse STREAMABLE, enregistrant ses contributions.

    Expose ``synthetiser_stream`` (détecté par l'orchestrateur) et ``agreger``. La
    composition ne s'active que sur le flux ; en synchrone, l'espion répond seul via
    ``traiter`` (repli mono-agent).
    """

    def __init__(
        self, nom: str = "reporting", score: float = 0.8, fragments: list[str] | None = None
    ) -> None:
        self.nom = nom
        self.description = nom
        self.mots_cles = (nom,)
        self._score = score
        self._fragments = fragments if fragments is not None else ["synthèse décisionnelle."]
        self.contributions_recues: list[AgentReponse] | None = None

    async def peut_traiter(self, requete: AgentRequete) -> float:
        return self._score

    async def traiter(self, requete: AgentRequete) -> AgentReponse:
        # Chemin SYNCHRONE (mono-agent) : le synthétiseur répond seul, sans composer.
        return AgentReponse("mono " + "".join(self._fragments), [], Confiance.MOYENNE, self.nom)

    async def synthetiser_stream(self, requete: AgentRequete, contributions: list[AgentReponse]):
        self.contributions_recues = contributions
        for fragment in self._fragments:
            yield fragment

    @staticmethod
    def agreger(contributions: list[AgentReponse]) -> tuple[list[str], Confiance]:
        sources: list[str] = []
        for contribution in contributions:
            for source in contribution.sources:
                if source not in sources:
                    sources.append(source)
        return sources, Confiance.MOYENNE


async def _flux(orch: Orchestrateur, question: str) -> list[dict]:
    return [e async for e in orch.traiter_stream(question, Langue.FR, "ip")]


@pytest.mark.asyncio
async def test_composition_stream_declenchee_et_premier_octet_progress() -> None:
    # Un synthétiseur présent dans le classement → fan-out puis synthèse streamée.
    rag = _AgentEspion("rag", 0.4, "analyse RAG")
    meteo = _AgentEspion("meteo", 0.9, "analyse météo")
    reporting = _AgentSynthetiseur("reporting", 0.8, ["synthèse ", "décisionnelle."])
    orch = _orchestrateur(rag, meteo, reporting)
    evenements = await _flux(orch, "comment tailler le cacaoyer ?")

    # Premier octet = progression (anti-524 : arrive avant le fan-out CPU).
    assert evenements[0]["type"] == "progress"
    assert evenements[-1]["type"] == "done"
    texte = "".join(e["text"] for e in evenements if e["type"] == "token")
    assert "synthèse décisionnelle" in texte
    noms = [c.agent for c in reporting.contributions_recues or []]
    assert "meteo" in noms and "rag" in noms
    assert "reporting" not in noms  # le synthétiseur n'est pas sa propre contribution
    assert meteo.recue is not None and rag.recue is not None
    assert evenements[-1]["sources"] == ["CNRA"]  # agrégées des contributions


@pytest.mark.asyncio
async def test_composition_stream_plafonne_les_contributeurs() -> None:
    rag = _AgentEspion("rag", 0.4, "a")
    meteo = _AgentEspion("meteo", 0.9, "b")
    prix = _AgentEspion("prix", 0.85, "c")
    reglement = _AgentEspion("reglementation", 0.8, "d")
    reporting = _AgentSynthetiseur("reporting", 0.7)
    orch = _orchestrateur(rag, meteo, prix, reglement, reporting)
    await _flux(orch, "comment tailler le cacaoyer ?")
    contributions = reporting.contributions_recues or []
    assert len(contributions) == 2  # plafond MAX_CONTRIBUTEURS
    assert {c.agent for c in contributions} == {"meteo", "prix"}  # les deux mieux classés


@pytest.mark.asyncio
async def test_composition_stream_repli_si_synthetiseur_seul() -> None:
    # Aucun autre agent classé → l'agent de repli fournit l'unique contribution.
    reporting = _AgentSynthetiseur("reporting", 0.8)
    rag = _AgentEspion("rag", 0.0, "repli RAG")  # score 0 → hors classement
    orch = _orchestrateur(rag, reporting, defaut="rag")
    await _flux(orch, "comment tailler le cacaoyer ?")
    assert [c.agent for c in reporting.contributions_recues or []] == ["rag"]
    assert rag.recue is not None


@pytest.mark.asyncio
async def test_composition_stream_heartbeat_par_contribution() -> None:
    # Un événement de progression est ré-émis après chaque contribution (anti-524 idle).
    rag = _AgentEspion("rag", 0.4, "analyse RAG")
    meteo = _AgentEspion("meteo", 0.9, "analyse météo")
    reporting = _AgentSynthetiseur("reporting", 0.8)
    orch = _orchestrateur(rag, meteo, reporting)
    evenements = await _flux(orch, "comment tailler le cacaoyer ?")
    progress = [e for e in evenements if e["type"] == "progress"]
    assert len(progress) >= 3  # 1 en tête + 1 par contribution (meteo, rag)


@pytest.mark.asyncio
async def test_composition_stream_garde_fou_sortie(monkeypatch: pytest.MonkeyPatch) -> None:
    # Le garde-fou de SORTIE s'applique au texte SYNTHÉTISÉ streamé (défense en profondeur).
    from app.services import guardrails

    monkeypatch.setattr(guardrails, "verifier_reponse", lambda texte: object())
    rag = _AgentEspion("rag", 0.4, "analyse")
    reporting = _AgentSynthetiseur("reporting", 0.8, ["synthèse compromise. "])
    orch = _orchestrateur(rag, reporting)
    evenements = await _flux(orch, "comment tailler le cacaoyer ?")
    texte = "".join(e["text"] for e in evenements if e["type"] == "token")
    assert "synthèse compromise" not in texte
    assert guardrails.REFUS_PHYTO in texte
    assert evenements[-1]["redirection_anader"] is True


@pytest.mark.asyncio
async def test_composition_mono_agent_preserve() -> None:
    # Aucun synthétiseur → dispatch mono-agent inchangé (flux token-par-token),
    # sans message de composition (la progression standard, elle, est émise).
    agent = _AgentStream(["Taillez en saison sèche. "])
    orch = _orchestrateur(agent)
    evenements = await _flux(orch, "comment tailler le cacaoyer ?")
    progres = [e["text"] for e in evenements if e["type"] == "progress"]
    assert orchestrateur_module._PROGRES_COMPOSITION not in progres
    texte = "".join(e["text"] for e in evenements if e["type"] == "token")
    assert "saison sèche" in texte


@pytest.mark.asyncio
async def test_sync_ne_compose_pas_repli_mono_agent() -> None:
    # SYNCHRONE (/chat) : un synthétiseur présent NE compose PAS (éviterait le 524) → mono-agent.
    rag = _AgentEspion("rag", 0.4, "analyse RAG")
    meteo = _AgentEspion("meteo", 0.9, "analyse météo")
    reporting = _AgentSynthetiseur("reporting", 0.8)
    orch = _orchestrateur(rag, meteo, reporting)
    conseil = await orch.traiter("comment tailler le cacaoyer ?", Langue.FR, "ip")
    assert conseil.reponse == "analyse météo"  # agent le mieux classé, pas de synthèse
    assert reporting.contributions_recues is None  # synthetiser_stream jamais appelé


def _paquet_cache(reponse: str) -> str:
    return json.dumps(
        {
            "reponse": reponse,
            "confiance": "moyenne",
            "sources": ["CNRA"],
            "redirection_anader": False,
        }
    )


@pytest.mark.asyncio
async def test_cache_semantique_hit_sert_une_paraphrase() -> None:
    # Miss exact mais voisin sémantique compatible → servi SANS solliciter d'agent.
    cache = _CacheFactice(semantic=(_paquet_cache("Taillez en saison sèche."), "comment tailler"))
    rag = _AgentEspion("rag", 1.0, "ne devrait pas répondre")
    orch = _orchestrateur(rag, cache=cache, cache_semantique=_semantique(cache))
    conseil = await orch.traiter("comment élaguer le cacaoyer ?", Langue.FR, "ip")
    assert conseil.reponse == "Taillez en saison sèche."
    assert rag.recue is None  # aucune inférence


@pytest.mark.asyncio
async def test_cache_semantique_indexe_apres_generation() -> None:
    cache = _CacheFactice()  # aucun hit
    rag = _AgentEspion("rag", 1.0, "conseil taille")
    orch = _orchestrateur(rag, cache=cache, cache_semantique=_semantique(cache))
    await orch.traiter("comment tailler le cacaoyer ?", Langue.FR, "ip")
    assert len(cache.indexes) == 1  # embedding indexé pour de futures paraphrases
    assert cache.indexes[0][2] == [1.0, 0.0]


@pytest.mark.asyncio
async def test_cache_semantique_ignore_en_multi_tours() -> None:
    cache = _CacheFactice(semantic=(_paquet_cache("cachée"), "q"))
    rag = _AgentEspion("rag", 1.0, "conseil frais")
    orch = _orchestrateur(rag, cache=cache, cache_semantique=_semantique(cache))
    hist = [{"role": "user", "content": "bonjour"}]
    conseil = await orch.traiter("et la taille du cacaoyer ?", Langue.FR, "ip", historique=hist)
    assert conseil.reponse == "conseil frais"  # pas de hit sémantique en multi-tours
    assert cache.indexes == []  # ni indexation


@pytest.mark.asyncio
async def test_cache_semantique_inerte_sans_embeddings() -> None:
    # cache_semantique inerte (embeddings None) → jamais de hit ni d'index sémantique.
    cache = _CacheFactice(semantic=(_paquet_cache("cachée"), "q"))
    rag = _AgentEspion("rag", 1.0, "conseil frais")
    orch = _orchestrateur(rag, cache=cache, cache_semantique=_semantique(cache, actif=False))
    conseil = await orch.traiter("comment tailler le cacaoyer ?", Langue.FR, "ip")
    assert conseil.reponse == "conseil frais"  # exact seul : le voisin sémantique est ignoré
    assert cache.indexes == []


@pytest.mark.asyncio
async def test_cache_semantique_ignore_si_intention_synthese_stream() -> None:
    # Vécu prod (05/07) : « bilan météo+prix » servi par la seule réponse météo cachée,
    # composition court-circuitée. Une intention de synthèse (synthétiseur au classement)
    # ne consulte PAS le cache sémantique : un voisin mono-agent ne couvre pas un bilan.
    cache = _CacheFactice(semantic=(_paquet_cache("analyse météo cachée"), "météo à Daloa"))
    rag = _AgentEspion("rag", 0.4, "analyse RAG")
    meteo = _AgentEspion("meteo", 0.9, "analyse météo")
    reporting = _AgentSynthetiseur("reporting", 0.8, ["synthèse complète."])
    orch = _orchestrateur(rag, meteo, reporting, cache=cache, cache_semantique=_semantique(cache))
    evenements = await _flux(orch, "comment tailler le cacaoyer ?")
    texte = "".join(e["text"] for e in evenements if e["type"] == "token")
    assert "synthèse complète" in texte  # la composition s'exécute bien
    assert "cachée" not in texte  # le voisin mono-agent n'est pas servi
    assert reporting.contributions_recues is not None


@pytest.mark.asyncio
async def test_cache_semantique_ignore_si_intention_synthese_sync() -> None:
    # Même règle en synchrone : pas de composition (anti-524), mais pas non plus de
    # voisin mono-agent caché — dispatch vers l'agent le mieux classé.
    cache = _CacheFactice(semantic=(_paquet_cache("analyse météo cachée"), "météo à Daloa"))
    rag = _AgentEspion("rag", 0.4, "analyse RAG")
    meteo = _AgentEspion("meteo", 0.9, "analyse météo")
    reporting = _AgentSynthetiseur("reporting", 0.8)
    orch = _orchestrateur(rag, meteo, reporting, cache=cache, cache_semantique=_semantique(cache))
    conseil = await orch.traiter("comment tailler le cacaoyer ?", Langue.FR, "ip")
    assert conseil.reponse == "analyse météo"
    assert meteo.recue is not None


@pytest.mark.asyncio
async def test_intention_synthese_non_indexee_semantiquement() -> None:
    # Une intention de synthèse n'alimente pas l'index sémantique : sa réponse (composée
    # ou mono) ne doit jamais être servie comme voisin d'une future question mono-agent.
    cache = _CacheFactice()
    rag = _AgentEspion("rag", 0.4, "analyse RAG")
    reporting = _AgentSynthetiseur("reporting", 0.8)
    orch = _orchestrateur(rag, reporting, cache=cache, cache_semantique=_semantique(cache))
    await _flux(orch, "comment tailler le cacaoyer ?")
    assert cache.indexes == []


@pytest.mark.asyncio
async def test_cache_semantique_stream_sert_une_paraphrase() -> None:
    cache = _CacheFactice(semantic=(_paquet_cache("Taillez en saison sèche."), "comment tailler"))
    agent = _AgentStream(["ne devrait pas être généré"])
    orch = _orchestrateur(agent, cache=cache, cache_semantique=_semantique(cache))
    evenements = await _flux(orch, "comment élaguer le cacaoyer ?")
    texte = "".join(e["text"] for e in evenements if e["type"] == "token")
    assert "saison sèche" in texte
    assert "généré" not in texte


# --- Progression pendant l'attente (tous les flux, pas seulement la composition) ---


class _AgentStreamNomme(_AgentStream):
    """Agent streamable dont le nom est paramétrable (libellés de progression)."""

    def __init__(self, nom: str, fragments: list[str]) -> None:
        super().__init__(fragments)
        self.nom = nom


@pytest.mark.asyncio
async def test_stream_progression_analyse_puis_libelle_agent() -> None:
    # Mono-agent : « J'analyse… » en premier octet, puis le libellé de l'agent,
    # TOUS deux émis avant le premier token (l'utilisateur voit l'étape en cours).
    agent = _AgentStreamNomme("satellite", ["Aucune alerte détectée. "])
    orch = _orchestrateur(agent, defaut="satellite")
    evenements = await _flux(orch, "comment tailler le cacaoyer ?")
    assert evenements[0] == {"type": "progress", "text": flux_module.PROGRES_ANALYSE}
    types = [e["type"] for e in evenements]
    progres = [e["text"] for e in evenements if e["type"] == "progress"]
    assert orchestrateur_module.PROGRES_AGENTS["satellite"] in progres
    assert types.index("token") > types.index("progress")  # progression avant la réponse
    # Aucune progression après le premier token : le statut disparaît à la réponse.
    assert "progress" not in types[types.index("token") :]


@pytest.mark.asyncio
async def test_stream_progression_agent_inconnu_libelle_generique() -> None:
    # Agent absent de la table → libellé générique (jamais de KeyError).
    agent = _AgentStreamNomme("futur-agent", ["Réponse. "])
    orch = _orchestrateur(agent, defaut="futur-agent")
    evenements = await _flux(orch, "comment tailler le cacaoyer ?")
    progres = [e["text"] for e in evenements if e["type"] == "progress"]
    assert flux_module.PROGRES_REDACTION in progres


@pytest.mark.asyncio
async def test_stream_clarification_progression_analyse_seule() -> None:
    # Clarification (réponse immédiate) : « J'analyse… » seul, pas de libellé agent.
    agent = _AgentStream(["ne devrait pas répondre"])
    orch = _orchestrateur(agent)
    evenements = [
        e
        async for e in orch.traiter_stream(
            "les feuilles de mon cacaoyer jaunissent", Langue.FR, "ip"
        )
    ]
    progres = [e["text"] for e in evenements if e["type"] == "progress"]
    assert progres == [flux_module.PROGRES_ANALYSE]


@pytest.mark.asyncio
async def test_composition_stream_libelles_humains() -> None:
    # Composition : les heartbeats bruts « [nom] » sont remplacés par des libellés
    # lisibles, et « Je rédige… » précède la synthèse streamée.
    rag = _AgentEspion("rag", 0.4, "analyse RAG")
    meteo = _AgentEspion("meteo", 0.9, "analyse météo")
    reporting = _AgentSynthetiseur("reporting", 0.8, ["synthèse. "])
    orch = _orchestrateur(rag, meteo, reporting)
    evenements = await _flux(orch, "comment tailler le cacaoyer ?")
    progres = [e["text"] for e in evenements if e["type"] == "progress"]
    assert not any(t.startswith("[") for t in progres)
    assert orchestrateur_module.PROGRES_AGENTS["meteo"] in progres
    assert progres[-1] == flux_module.PROGRES_REDACTION
