"""Agent Reporting : synthèse narrative de contributions multi-agents."""

from __future__ import annotations

import pytest

from app.domain.agents import AgentReponse, AgentRequete
from app.models.domain import Confiance, Langue
from app.services.agents.agent_reporting import AgentReporting


class _InferenceFactice:
    def __init__(self) -> None:
        self.contexte_recu: str | None = None

    async def generer(self, question, *, contexte=None, historique=None, **kw) -> str:
        self.contexte_recu = contexte
        return "Synthèse : conditions favorables et prix porteur."

    def generer_stream(self, *a, **k): ...
    async def ready(self) -> bool:
        return True


def _requete(q: str = "fais-moi un bilan") -> AgentRequete:
    return AgentRequete(q, Langue.FR, q, "ip", [])


@pytest.mark.asyncio
async def test_peut_traiter_eleve_sur_demande_de_rapport() -> None:
    agent = AgentReporting(_InferenceFactice())
    assert await agent.peut_traiter(_requete("fais-moi une synthèse")) >= 0.7
    assert await agent.peut_traiter(_requete("comment tailler ?")) < 0.3


@pytest.mark.asyncio
async def test_synthetiser_fusionne_les_contributions() -> None:
    inf = _InferenceFactice()
    agent = AgentReporting(inf)
    contributions = [
        AgentReponse("Pluie demain.", ["meteo"], Confiance.MOYENNE, "meteo"),
        AgentReponse("Prix 1800 FCFA/kg.", ["CCC"], Confiance.ELEVEE, "prix"),
    ]
    reponse = await agent.synthetiser(_requete(), contributions)
    assert reponse.agent == "reporting"
    # Les contributions sont passées au LLM comme contexte de synthèse.
    assert "Pluie demain." in (inf.contexte_recu or "")
    assert "1800" in (inf.contexte_recu or "")
    # Les sources des contributions sont agrégées.
    assert "meteo" in reponse.sources and "CCC" in reponse.sources
    # Confiance prudente : la plus basse des contributions.
    assert reponse.confiance is Confiance.MOYENNE


@pytest.mark.asyncio
async def test_synthetiser_cadre_les_contributions_comme_analyses() -> None:
    # Souveraineté : les contributions sont cadrées comme des analyses d'agents (pas des
    # sources officielles brutes) et le modèle ne doit pas y ajouter de source non fournie.
    inf = _InferenceFactice()
    agent = AgentReporting(inf)
    contributions = [AgentReponse("Pluie demain.", ["meteo"], Confiance.MOYENNE, "meteo")]
    await agent.synthetiser(_requete(), contributions)
    ctx = (inf.contexte_recu or "").lower()
    assert "analyses" in ctx and "agents" in ctx
    assert "n'ajoute" in ctx and "aucune source" in ctx


@pytest.mark.asyncio
async def test_synthetiser_sans_contribution_degrade_proprement() -> None:
    # Aucune contribution : pas de plantage, confiance faible (rien à synthétiser).
    agent = AgentReporting(_InferenceFactice())
    reponse = await agent.synthetiser(_requete(), [])
    assert reponse.agent == "reporting"
    assert reponse.confiance is Confiance.FAIBLE


class _InferenceStream:
    def __init__(self, fragments: list[str]) -> None:
        self._fragments = fragments
        self.contexte_recu: str | None = None

    async def generer(self, *a, **k) -> str:
        return ""

    async def generer_stream(self, question, *, contexte=None, historique=None, **kw):
        self.contexte_recu = contexte
        for fragment in self._fragments:
            yield fragment

    async def ready(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_synthetiser_stream_diffuse_les_fragments_et_cadre() -> None:
    # Variante flux : la synthèse est streamée, les contributions injectées au contexte.
    inf = _InferenceStream(["Synthèse : ", "conditions favorables."])
    agent = AgentReporting(inf)
    contributions = [AgentReponse("Pluie demain.", ["meteo"], Confiance.MOYENNE, "meteo")]
    fragments = [f async for f in agent.synthetiser_stream(_requete(), contributions)]
    assert "".join(fragments) == "Synthèse : conditions favorables."
    assert "Pluie demain." in (inf.contexte_recu or "")


def test_agreger_union_sources_et_confiance_min() -> None:
    # Sources unionnées sans doublon (ordre préservé), confiance la plus basse.
    contributions = [
        AgentReponse("a", ["meteo", "CNRA"], Confiance.ELEVEE, "meteo"),
        AgentReponse("b", ["CNRA", "CCC"], Confiance.FAIBLE, "prix"),
    ]
    sources, confiance = AgentReporting.agreger(contributions)
    assert sources == ["meteo", "CNRA", "CCC"]
    assert confiance is Confiance.FAIBLE


def test_agreger_sans_contribution_confiance_moyenne() -> None:
    # Jamais None : défaut prudent MOYENNE (utilisé si le repli n'a produit aucune source).
    sources, confiance = AgentReporting.agreger([])
    assert sources == []
    assert confiance is Confiance.MOYENNE


class _InferenceMemoire:
    """Inférence qui retient le bloc de mémoire reçu en flux."""

    def __init__(self) -> None:
        self.memoire_recue: str | None = None

    async def generer(self, question, **kw) -> str:
        self.memoire_recue = kw.get("memoire")
        return "Synthèse."

    async def generer_stream(self, question, **kw):
        self.memoire_recue = kw.get("memoire")
        yield "Synthèse."

    async def ready(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_la_synthese_en_flux_transmet_la_memoire_du_fil() -> None:
    """La synthèse multi-agents appelle l'inférence DIRECTEMENT, hors AgentBase.

    Sans ce passage explicite, la réponse composée — celle que sert la production —
    serait la seule à ignorer ce que le producteur a déjà dit.
    """
    inference = _InferenceMemoire()
    agent = AgentReporting(inference)
    requete = AgentRequete(
        "fais-moi un bilan", Langue.FR, "fais-moi un bilan", "ip", [], memoire="- Localité : Soubré"
    )
    contribution = AgentReponse(
        "Prix bord-champ : 1200 FCFA/kg.", ["CCC"], Confiance.ELEVEE, "prix"
    )
    async for _ in agent.synthetiser_stream(requete, [contribution]):
        pass
    assert inference.memoire_recue == "- Localité : Soubré"
