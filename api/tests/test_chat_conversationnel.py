"""Tests du chat conversationnel : civilités, mémoire du fil, accusé de réception.

Deux exigences y sont verrouillées :

1. Une civilité ne coûte **aucune inférence** — on l'assère sur l'effet de bord (le
   port d'inférence n'est jamais appelé), pas sur le texte affiché, qui pourrait
   ressembler à une réponse polie tout en ayant brûlé 38 s de CPU.
2. Drapeau éteint (**repli**) = comportement d'AVANT, à l'identique. C'est la
   garantie de non-régression du chemin CPU.
"""

from __future__ import annotations

import pytest

from app.application.orchestrateur import Orchestrateur
from app.application.registre import RegistreAgents
from app.application.routage import RouteurIntention
from app.models.domain import Langue
from app.services.agents.agent_rag import AgentRag
from tests.conftest import FakeCache, FakeJournal


class InferenceEspionne:
    """Inférence qui enregistre CHAQUE appel et les arguments reçus."""

    def __init__(self) -> None:
        self.appels: list[dict[str, object]] = []
        self.reponse = "Taillez vos cacaoyers en fin de saison sèche. Sources : CNRA."

    async def generer(self, question: str, **kwargs: object) -> str:
        self.appels.append({"question": question, **kwargs})
        return self.reponse

    async def generer_stream(self, question: str, **kwargs: object):
        self.appels.append({"question": question, **kwargs})
        yield self.reponse

    async def ready(self) -> bool:
        return True

    @property
    def memoires(self) -> list[str]:
        """Blocs de mémoire du fil effectivement transmis au modèle."""
        return [str(a.get("memoire") or "") for a in self.appels]


def _orchestrateur(inference: InferenceEspionne, *, conversationnel: bool) -> Orchestrateur:
    """Orchestrateur minimal : un seul agent (RAG sans corpus), cache et journal faux."""
    registre = RegistreAgents()
    registre.enregistrer(AgentRag(inference, rag=None))
    return Orchestrateur(
        RouteurIntention(registre),
        FakeJournal(),
        FakeCache(),
        inference=inference,
        conversationnel=conversationnel,
    )


async def _texte_du_flux(orchestrateur: Orchestrateur, question: str, historique=None) -> str:
    """Concatène les fragments d'un flux jusqu'à l'événement final."""
    morceaux: list[str] = []
    async for evenement in orchestrateur.traiter_stream(question, Langue.FR, "1.2.3.4", historique):
        if evenement.get("type") == "token":
            morceaux.append(str(evenement.get("text", "")))
    return "".join(morceaux)


@pytest.mark.asyncio
async def test_une_civilite_ne_declenche_aucune_inference() -> None:
    """« Bonjour » répond instantanément : le modèle n'est PAS sollicité."""
    inference = InferenceEspionne()
    conseil = await _orchestrateur(inference, conversationnel=True).traiter(
        "Bonjour", Langue.FR, "1.2.3.4"
    )
    assert inference.appels == []  # l'effet de bord, pas le texte
    assert "OpenCacao" in conseil.reponse


@pytest.mark.asyncio
async def test_une_civilite_ne_declenche_aucune_inference_en_flux() -> None:
    """Même garantie sur /chat/stream, qui est le chemin réel de la production."""
    inference = InferenceEspionne()
    texte = await _texte_du_flux(_orchestrateur(inference, conversationnel=True), "Merci beaucoup")
    assert inference.appels == []
    assert texte.strip()


@pytest.mark.asyncio
async def test_une_civilite_ne_cite_aucune_source() -> None:
    """Un bonjour n'a rien à sourcer : c'est ce qui manquait en production."""
    conseil = await _orchestrateur(InferenceEspionne(), conversationnel=True).traiter(
        "Bonjour", Langue.FR, "1.2.3.4"
    )
    assert conseil.sources == []
    assert conseil.redirection_anader is False


@pytest.mark.asyncio
async def test_une_salutation_sur_conversation_reprise_rappelle_le_fil() -> None:
    """Revenir dire bonjour ne remet pas les compteurs à zéro."""
    historique = [
        {"role": "user", "content": "Mes cabosses pourrissent, je suis à Soubré"},
        {"role": "assistant", "content": "Retirez les cabosses atteintes."},
    ]
    conseil = await _orchestrateur(InferenceEspionne(), conversationnel=True).traiter(
        "Bonjour", Langue.FR, "1.2.3.4", historique
    )
    assert "Soubré" in conseil.reponse


@pytest.mark.asyncio
async def test_une_salutation_suivie_dune_demande_recoit_un_conseil() -> None:
    """Le chemin nominal reste intact : la demande est bien traitée par un agent."""
    inference = InferenceEspionne()
    conseil = await _orchestrateur(inference, conversationnel=True).traiter(
        "Bonjour, à quelle période tailler mes cacaoyers ?", Langue.FR, "1.2.3.4"
    )
    assert len(inference.appels) == 1
    assert "Taillez" in conseil.reponse


@pytest.mark.asyncio
async def test_la_memoire_du_fil_est_transmise_au_modele() -> None:
    """Ce que le producteur a dit dix tours plus tôt parvient au modèle."""
    inference = InferenceEspionne()
    historique = [{"role": "user", "content": "Je suis à Soubré, ma plantation a 15 ans"}]
    historique += [{"role": "assistant", "content": "Bien noté."}]
    await _orchestrateur(inference, conversationnel=True).traiter(
        "À quelle période tailler mes cacaoyers ?", Langue.FR, "1.2.3.4", historique
    )
    memoire = inference.memoires[0]
    assert "Soubré" in memoire
    assert "15 ans" in memoire


@pytest.mark.asyncio
async def test_la_memoire_du_fil_est_transmise_au_modele_en_flux() -> None:
    """Même transmission sur le chemin streaming."""
    inference = InferenceEspionne()
    historique = [
        {"role": "user", "content": "Je suis à Soubré"},
        {"role": "assistant", "content": "Bien noté."},
    ]
    await _texte_du_flux(
        _orchestrateur(inference, conversationnel=True),
        "À quelle période tailler mes cacaoyers ?",
        historique,
    )
    assert "Soubré" in inference.memoires[0]


@pytest.mark.asyncio
async def test_sans_fait_connu_aucun_bloc_de_memoire_nest_injecte() -> None:
    """Question factuelle sans contexte : le prompt n'est pas alourdi pour rien (CPU)."""
    inference = InferenceEspionne()
    await _orchestrateur(inference, conversationnel=True).traiter(
        "À quelle période tailler mes cacaoyers ?", Langue.FR, "1.2.3.4"
    )
    assert inference.memoires[0] == ""  # rien à retenir, rien d'ajouté


# --- Repli : drapeau éteint = comportement strictement antérieur ---


@pytest.mark.asyncio
async def test_repli_une_civilite_repasse_par_le_modele_comme_avant() -> None:
    """Drapeau OFF : « Bonjour » suit le pipeline complet, exactement comme avant."""
    inference = InferenceEspionne()
    await _orchestrateur(inference, conversationnel=False).traiter("Bonjour", Langue.FR, "1.2.3.4")
    assert len(inference.appels) == 1


@pytest.mark.asyncio
async def test_repli_aucune_memoire_du_fil_nest_injectee() -> None:
    """Drapeau OFF : le prompt système reste celui d'avant, sans bloc de mémoire."""
    inference = InferenceEspionne()
    historique = [
        {"role": "user", "content": "Je suis à Soubré, ma plantation a 15 ans"},
        {"role": "assistant", "content": "Bien noté."},
    ]
    await _orchestrateur(inference, conversationnel=False).traiter(
        "À quelle période tailler mes cacaoyers ?", Langue.FR, "1.2.3.4", historique
    )
    assert inference.memoires[0] == ""


@pytest.mark.asyncio
async def test_repli_en_flux_aucune_memoire_du_fil_nest_injectee() -> None:
    """Drapeau OFF sur le chemin streaming : rien n'est ajouté au prompt non plus."""
    inference = InferenceEspionne()
    historique = [
        {"role": "user", "content": "Je suis à Soubré"},
        {"role": "assistant", "content": "Bien noté."},
    ]
    await _texte_du_flux(
        _orchestrateur(inference, conversationnel=False),
        "À quelle période tailler mes cacaoyers ?",
        historique,
    )
    assert inference.memoires[0] == ""
