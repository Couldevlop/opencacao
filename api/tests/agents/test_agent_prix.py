"""Agent Prix : tool use sur les données de marché du cacao."""

from __future__ import annotations

import re

import pytest

from app.domain.agents import AgentRequete
from app.models.domain import Langue
from app.services.agents.agent_prix import AgentPrix
from app.services.outils.prix import OutilPrix


class _InferenceFactice:
    def __init__(self) -> None:
        self.contexte_recu: str | None = None

    async def generer(self, question, *, contexte=None, historique=None, **kw) -> str:
        self.contexte_recu = contexte
        return "Le prix bord-champ garanti est de 1800 FCFA/kg."

    def generer_stream(self, *a, **k): ...
    async def ready(self) -> bool:
        return True


class _PrixFactice:
    async def cours(self) -> dict:
        return {"prix_bord_champ_fcfa_kg": 1800, "campagne": "2025-2026"}


class _PrixVide:
    async def cours(self) -> dict:
        return {}


class _RagFactice:
    async def contexte_pour(self, texte: str) -> str:
        return "Prix de mise à marché du cacao, juin 2026 : 2 966 FCFA/kg."


def _requete(q: str) -> AgentRequete:
    return AgentRequete(q, Langue.FR, q, "ip", [])


@pytest.mark.asyncio
async def test_peut_traiter_eleve_sur_question_prix() -> None:
    agent = AgentPrix(_InferenceFactice(), OutilPrix(_PrixFactice()))
    assert await agent.peut_traiter(_requete("à combien se vend le cacao ?")) >= 0.7
    assert await agent.peut_traiter(_requete("comment tailler le cacaoyer ?")) < 0.3


@pytest.mark.asyncio
async def test_traiter_injecte_le_cours_dans_le_contexte() -> None:
    inf = _InferenceFactice()
    agent = AgentPrix(inf, OutilPrix(_PrixFactice()))
    reponse = await agent.traiter(_requete("quel est le prix du cacao ?"))
    assert reponse.agent == "prix"
    assert inf.contexte_recu is not None
    assert "1800" in inf.contexte_recu


@pytest.mark.asyncio
async def test_prix_indisponible_ninvente_pas_de_prix() -> None:
    # Garde-fou souveraineté (non négociable) : sans prix officiel configuré, l'agent
    # doit injecter une CONSIGNE explicite interdisant d'avancer un chiffre et
    # redirigeant vers la source officielle — jamais un contexte vide qui laisserait
    # le modèle fabriquer un prix (bug observé en prod : « 800 FCFA/kg » inventé).
    inf = _InferenceFactice()
    agent = AgentPrix(inf, OutilPrix(_PrixVide()))
    await agent.traiter(_requete("quel est le prix du cacao cette campagne ?"))
    assert inf.contexte_recu is not None, "un contexte directif doit être injecté"
    ctx = inf.contexte_recu.lower()
    # Consigne de non-invention présente…
    assert "aucun" in ctx and ("n'avance" in ctx or "aucun chiffre" in ctx)
    # …aucun chiffre de prix fabriqué dans la consigne…
    assert not re.search(r"\d+\s*fcfa", ctx)
    # …et redirection vers la source officielle.
    assert "conseil" in ctx or "anader" in ctx


@pytest.mark.asyncio
async def test_prix_combine_le_cours_officiel_et_le_contexte_rag() -> None:
    # L'agent Prix doit combiner le prix bord-champ officiel (autoritaire, configuré)
    # ET le contexte documentaire RAG (prix de mise à marché, historique, café…)
    # afin de ne pas être plus pauvre que le chemin RAG sur les questions de marché.
    inf = _InferenceFactice()
    agent = AgentPrix(inf, OutilPrix(_PrixFactice()), rag=_RagFactice())
    await agent.traiter(_requete("quels sont les prix de mise à marché du cacao ?"))
    assert inf.contexte_recu is not None
    assert "1800" in inf.contexte_recu  # prix bord-champ officiel (configuré)
    assert "mise à marché" in inf.contexte_recu  # contexte documentaire RAG


@pytest.mark.asyncio
async def test_prix_sans_rag_garde_le_seul_cours() -> None:
    # Sans RAG branché (rag=None), l'agent reste fonctionnel sur le seul cours.
    inf = _InferenceFactice()
    agent = AgentPrix(inf, OutilPrix(_PrixFactice()))
    await agent.traiter(_requete("quel est le prix du cacao ?"))
    assert inf.contexte_recu is not None
    assert "1800" in inf.contexte_recu


@pytest.mark.asyncio
async def test_prix_evite_les_faux_positifs_de_sous_chaine() -> None:
    # « discours » ne déclenche pas « cours » ; « vendredi » ne déclenche pas « vend ».
    agent = AgentPrix(_InferenceFactice(), OutilPrix(_PrixFactice()))
    assert await agent.peut_traiter(_requete("le discours du président vendredi")) == 0.0


def test_formater_cours_pose_le_prix_officiel_comme_autoritaire() -> None:
    # Souveraineté : le prix officiel doit PRIMER sur les prix historiques du RAG
    # (bug prod : « 850 FCFA » historique servi au lieu du 1200 officiel). La consigne
    # doit poser le prix comme autoritaire ET désamorcer les autres montants du contexte.
    from app.services.agents.agent_prix import _formater_cours

    contexte = _formater_cours({"prix_bord_champ_fcfa_kg": 1200, "campagne": "intermédiaire 2026"})
    assert "1200" in contexte
    assert "officiel" in contexte.lower()
    assert "actuel" in contexte.lower()
    assert "historique" in contexte.lower()
