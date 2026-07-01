"""Tests du pré-chauffage du cache (méthode service + orchestrateur)."""

from __future__ import annotations

from app.application.conseil_service import ConseilService
from app.application.prewarm import prechauffer_cache
from app.domain.exceptions import InferenceUnavailable
from app.models.domain import Langue

# --- ConseilService.prechauffer ---


def _service(fake_inference, fake_cache, fake_journal) -> ConseilService:
    return ConseilService(inference=fake_inference, cache=fake_cache, journal=fake_journal)


async def test_prechauffer_genere_et_cache(fake_inference, fake_cache, fake_journal) -> None:
    service = _service(fake_inference, fake_cache, fake_journal)
    genere = await service.prechauffer("Quand récolter le cacao ?", Langue.FR)
    assert genere is True
    assert fake_inference.appels == ["Quand récolter le cacao ?"]
    # Mis en cache, et pas de journalisation (pré-chauffage discret).
    assert await fake_cache.get_cached("Quand récolter le cacao ?", "fr") is not None
    assert fake_journal.interactions == []


async def test_prechauffer_ignore_si_deja_en_cache(
    fake_inference, fake_cache, fake_journal
) -> None:
    await fake_cache.set_cached("Q", "fr", "{}")
    service = _service(fake_inference, fake_cache, fake_journal)
    assert await service.prechauffer("Q", Langue.FR) is False
    assert fake_inference.appels == []  # aucune inférence


async def test_prechauffer_refus_non_cache(fake_inference, fake_cache, fake_journal) -> None:
    service = _service(fake_inference, fake_cache, fake_journal)
    # Question hors filière -> refus instantané, pas d'inférence, pas de cache.
    assert await service.prechauffer("Quel telephone acheter ?", Langue.FR) is False
    assert fake_inference.appels == []


async def test_prechauffer_sortie_compromise_non_cachee(
    fake_inference, fake_cache, fake_journal, monkeypatch
) -> None:
    from app.application import conseil_service as cs

    monkeypatch.setattr(cs.guardrails, "verifier_reponse", lambda _t: object())
    service = _service(fake_inference, fake_cache, fake_journal)
    assert await service.prechauffer("Une question valide cacao ?", Langue.FR) is False
    assert await fake_cache.get_cached("Une question valide cacao ?", "fr") is None


# --- Orchestrateur prechauffer_cache ---


class ServiceStub:
    """Service simulé : la 1ʳᵉ valeur de `plan` pilote chaque appel."""

    def __init__(self, plan: list) -> None:
        self._plan = plan
        self.appels: list[str] = []

    async def prechauffer(self, question: str, langue) -> bool:
        self.appels.append(question)
        resultat = self._plan.pop(0)
        if isinstance(resultat, Exception):
            raise resultat
        return resultat


async def _no_sleep(_delai: float) -> None:
    return None


async def test_orchestrateur_genere_tout() -> None:
    stub = ServiceStub([True, True, False])
    n = await prechauffer_cache(stub, ["a", "b", "c"], dormir=_no_sleep)
    assert n == 2  # "c" était déjà en cache
    assert stub.appels == ["a", "b", "c"]


async def test_orchestrateur_reessaie_puis_reussit() -> None:
    # 1ʳᵉ question : indisponible 2 fois, puis OK ; 2ᵉ : OK.
    stub = ServiceStub([InferenceUnavailable("x"), InferenceUnavailable("x"), True, True])
    n = await prechauffer_cache(stub, ["a", "b"], tentatives=5, dormir=_no_sleep)
    assert n == 2
    assert stub.appels == ["a", "a", "a", "b"]


async def test_orchestrateur_abandonne_si_inference_ko() -> None:
    stub = ServiceStub([InferenceUnavailable("x")] * 5)
    n = await prechauffer_cache(stub, ["a", "b"], tentatives=3, dormir=_no_sleep)
    assert n == 0
    assert stub.appels == ["a", "a", "a"]  # 3 essais puis abandon


# --- Intégration lifespan (_lancer_prechauffage) ---


def _faux_app(fake_inference, fake_cache, fake_journal):
    from types import SimpleNamespace

    return SimpleNamespace(
        state=SimpleNamespace(
            inference=fake_inference, cache=fake_cache, journal=fake_journal, rag=None
        )
    )


async def test_lancer_prechauffage_desactive(fake_inference, fake_cache, fake_journal) -> None:
    from app.core.config import Settings
    from app.main import _lancer_prechauffage

    app = _faux_app(fake_inference, fake_cache, fake_journal)
    assert _lancer_prechauffage(app, Settings(prewarm_enabled=False)) is None


async def test_lancer_prechauffage_actif(fake_inference, fake_cache, fake_journal) -> None:
    from app.application.faq import QUESTIONS_FAQ
    from app.core.config import Settings
    from app.main import _lancer_prechauffage

    app = _faux_app(fake_inference, fake_cache, fake_journal)
    tache = _lancer_prechauffage(app, Settings(prewarm_enabled=True))
    assert tache is not None
    await tache  # avec les fakes, le pré-chauffage se termine vite
    assert await fake_cache.get_cached(QUESTIONS_FAQ[0], "fr") is not None


def test_faq_sans_doublon_et_non_refusee() -> None:
    """Les questions FAQ pré-chauffées : pas de doublon, et aucune bloquée par un garde-fou
    (sinon on pré-chaufferait un refus au lieu d'une vraie réponse)."""
    from app.application.faq import QUESTIONS_FAQ
    from app.services import guardrails

    assert len(QUESTIONS_FAQ) == len(set(QUESTIONS_FAQ)), "doublon dans QUESTIONS_FAQ"
    refusees = [q for q in QUESTIONS_FAQ if guardrails.evaluer(q) is not None]
    assert refusees == [], f"FAQ refusées par un garde-fou : {refusees}"
