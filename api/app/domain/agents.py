"""Contrat des agents de la plateforme V3 — structures et ports purs.

Un agent est une capacité bornée derrière une interface stable : il déclare ce
qu'il sait traiter (routage) et rend une réponse normalisée. L'orchestrateur, le
registre et le routeur ne dépendent QUE de ces abstractions — jamais d'un agent
concret. C'est l'inversion de dépendance de la clean architecture appliquée à
l'agentique : ajouter un agent (n°5..n°11) = implémenter ``AgentPort`` + l'enregistrer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from app.models.domain import Confiance, Langue


@dataclass(frozen=True)
class AgentRequete:
    """Requête normalisée transmise à un agent.

    Attributes:
        question: Dernière question du producteur (déjà validée par le DTO).
        langue: Langue de la requête.
        fil_ancre: Question ancrée sur le dernier tour utilisateur (anti-dérive
            multi-tours) — sert au routage et à la récupération.
        client_ip: IP cliente (rate-limit appliqué en amont par l'orchestrateur).
        historique: Tours précédents [{"role", "content"}], ou liste vide.
    """

    question: str
    langue: Langue
    fil_ancre: str
    client_ip: str
    historique: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class AgentReponse:
    """Réponse normalisée produite par un agent.

    Attributes:
        texte: Texte de la réponse.
        sources: Sources fiables citées.
        confiance: Niveau de confiance estimé.
        agent: Nom de l'agent qui a produit la réponse (traçabilité).
        redirection_anader: Vrai si la réponse oriente vers l'ANADER.
    """

    texte: str
    sources: list[str]
    confiance: Confiance
    agent: str
    redirection_anader: bool = False


@runtime_checkable
class AgentPort(Protocol):
    """Contrat que tout agent spécialisé doit respecter.

    Attributes:
        nom: Identifiant unique de l'agent (clé de registre).
        description: Phrase décrivant la capacité (lisible, sert au routage futur).
        mots_cles: Termes déclencheurs pour le routage déterministe initial.
    """

    nom: str
    description: str
    mots_cles: tuple[str, ...]

    async def peut_traiter(self, requete: AgentRequete) -> float:
        """Score d'aptitude [0..1] : à quel point cet agent est pertinent ?"""
        ...

    async def traiter(self, requete: AgentRequete) -> AgentReponse:
        """Produit une réponse. Lève AgentIndisponible si l'agent échoue."""
        ...


@runtime_checkable
class Outil(Protocol):
    """Contrat d'un outil invocable par un agent (météo, prix, RAG…).

    Un outil est une fonction nommée à entrée/sortie sérialisables : c'est le
    « tool use » de l'agentique. Toujours mockable (aucun appel réseau en test).
    """

    nom: str

    async def invoquer(self, **kwargs: object) -> dict[str, object]:
        """Exécute l'outil et retourne un dictionnaire de résultats."""
        ...
