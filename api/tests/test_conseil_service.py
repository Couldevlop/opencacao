"""Tests unitaires du ConseilService (orchestration), inférence/cache/journal mockés.

Cible les chemins peu couverts : garde-fou de SORTIE (bloc et flux), branche cache
en streaming, clarification en streaming, enrichissement de contact en flux.
"""

from __future__ import annotations

import pytest

from app.application import conseil_service as cs_module
from app.application.conseil_service import ConseilService, _serialiser
from app.domain.entities import Conseil
from app.domain.exceptions import RateLimitDepasse
from app.models.domain import Confiance, Langue
from app.services import guardrails

from .conftest import FakeCache, FakeInference, FakeJournal


def _service(
    inference: FakeInference | None = None,
    cache: FakeCache | None = None,
    journal: FakeJournal | None = None,
) -> tuple[ConseilService, FakeCache, FakeJournal]:
    cache = cache or FakeCache()
    journal = journal or FakeJournal()
    service = ConseilService(
        inference=inference or FakeInference(),
        cache=cache,
        journal=journal,
    )
    return service, cache, journal


def _refus_phyto(_texte: str):
    """Faux garde-fou de sortie : déclenche sur le marqueur, sans écrire de dosage."""
    return guardrails.Refus(guardrails.CategorieRefus.PHYTOSANITAIRE) if "MARQ" in _texte else None


# --- Garde-fou de SORTIE en mode bloc (lignes 174-177) ---


async def test_conseiller_garde_fou_sortie_bloque(monkeypatch) -> None:
    """Si l'inférence renvoie un dosage, la réponse est remplacée par le refus phyto."""
    inference = FakeInference(reponse="Texte compromis MARQ.")
    service, _, journal = _service(inference)
    monkeypatch.setattr(cs_module.guardrails, "verifier_reponse", _refus_phyto)

    conseil = await service.conseiller("Comment sécher mes fèves de cacao ?", Langue.FR, "1.2.3.4")

    assert conseil.redirection_anader is True
    assert conseil.reponse.startswith(guardrails.REFUS_PHYTO)
    assert "MARQ" not in conseil.reponse  # le texte compromis n'est jamais livré
    assert conseil.interaction_id is not None
    # Journalisé (la réponse de refus, pas le texte compromis).
    assert journal.interactions[-1]["reponse"].startswith(guardrails.REFUS_PHYTO)


# --- Cache en mode bloc + enrichissement de contact (rappel) ---


async def test_conseiller_depuis_cache(monkeypatch) -> None:
    """Une réponse en cache est resservie sans appeler l'inférence."""
    service, cache, journal = _service()
    paquet = _serialiser(Conseil("Réponse en cache. Sources : CNRA.", Confiance.ELEVEE, ["CNRA"]))
    await cache.set_cached("Comment sécher ?", "fr", paquet)

    conseil = await service.conseiller("Comment sécher ?", Langue.FR, "9.9.9.9")

    assert conseil.reponse.startswith("Réponse en cache")
    assert service._inference.appels == []  # pas d'inférence
    assert conseil.interaction_id is not None


# --- Rate-limit avant inférence (mode bloc) ---


async def test_conseiller_rate_limit(monkeypatch) -> None:
    cache = FakeCache(rate_limit=0)  # tout dépasse immédiatement
    service, _, _ = _service(cache=cache)
    with pytest.raises(RateLimitDepasse):
        await service.conseiller("Question agronomique neuve ?", Langue.FR, "5.5.5.5")


# --- Streaming : clarification (lignes 286-291) ---


async def test_stream_clarification(monkeypatch) -> None:
    """Au 1er tour, une question de rendement déclenche une salve de clarification en flux."""
    service, _, journal = _service()
    evts = [
        ev
        async for ev in service.conseiller_stream(
            "Mon verger a un faible rendement, que faire ?", Langue.FR, "1.1.1.1"
        )
    ]
    tokens = [e for e in evts if e["type"] == "token"]
    final = evts[-1]
    assert tokens  # au moins un fragment émis
    assert any("précisions" in e["text"] or "rendement" in e["text"].lower() for e in tokens)
    assert final["type"] == "done"
    assert final["confiance"] == Confiance.MOYENNE.value
    assert service._inference.appels == []  # pas d'inférence


# --- Streaming : réponse depuis le cache (lignes 296-316) ---


async def test_stream_depuis_cache() -> None:
    """En flux, une réponse en cache est rejouée fragment par fragment, sans inférence."""
    service, cache, _ = _service()
    paquet = _serialiser(
        Conseil("Réponse cachée en flux. Sources : ANADER.", Confiance.ELEVEE, ["ANADER"])
    )
    await cache.set_cached("Quand tailler le cacaoyer ?", "fr", paquet)

    evts = [
        ev
        async for ev in service.conseiller_stream(
            "Quand tailler le cacaoyer ?", Langue.FR, "2.2.2.2"
        )
    ]
    texte = "".join(e["text"] for e in evts if e["type"] == "token")
    assert "Réponse cachée en flux" in texte
    assert service._inference.appels == []
    assert evts[-1]["type"] == "done"


# --- Streaming : garde-fou de sortie sur le tampon final (ligne 344) ---


async def test_stream_garde_fou_sortie_tampon_final(monkeypatch) -> None:
    """Un dosage logé dans le DERNIER fragment (sans ponctuation finale) est bloqué."""
    # Réponse sans ponctuation de fin -> tout reste dans le tampon final.
    inference = FakeInference(reponse="conseil final compromis MARQ")
    service, _, journal = _service(inference)
    monkeypatch.setattr(cs_module.guardrails, "verifier_reponse", _refus_phyto)

    evts = [
        ev
        async for ev in service.conseiller_stream(
            "Comment bien sécher mes fèves de cacao ?", Langue.FR, "3.3.3.3"
        )
    ]
    texte = "".join(e["text"] for e in evts if e["type"] == "token")
    assert guardrails.REFUS_PHYTO in texte
    assert "MARQ" not in texte  # le texte compromis n'est jamais diffusé
    assert evts[-1]["redirection_anader"] is True


# --- Streaming : garde-fou déclenché en cours de phrase (break du while) ---


async def test_stream_garde_fou_sortie_phrase(monkeypatch) -> None:
    """Un dosage dans une phrase complète interrompt l'émission avant diffusion."""
    inference = FakeInference(reponse="Phrase saine. Phrase MARQ avec dosage. Suite.")
    service, _, _ = _service(inference)
    monkeypatch.setattr(cs_module.guardrails, "verifier_reponse", _refus_phyto)

    evts = [
        ev
        async for ev in service.conseiller_stream(
            "Comment améliorer la fermentation du cacao ?", Langue.FR, "4.4.4.4"
        )
    ]
    texte = "".join(e["text"] for e in evts if e["type"] == "token")
    assert "MARQ" not in texte
    assert guardrails.REFUS_PHYTO in texte


# --- Streaming : enrichissement de contact diffusé en flux (ligne 374) ---


async def test_stream_enrichit_contact_en_flux() -> None:
    """Quand l'utilisateur demande un contact et cite une ville, le contact est ajouté au flux."""
    inference = FakeInference(reponse="Voici un conseil général sur le cacao. Sources : ANADER.")
    service, _, _ = _service(inference)

    evts = [
        ev
        async for ev in service.conseiller_stream(
            "Je suis à Bouaké, donnez-moi le contact de l'ANADER.",
            Langue.FR,
            "7.7.7.7",
        )
    ]
    texte = "".join(e["text"] for e in evts if e["type"] == "token")
    assert "📍" in texte  # une fiche contact a été ajoutée
    assert evts[-1]["redirection_anader"] is True


# --- Streaming : rate-limit avant inférence ---


async def test_stream_rate_limit() -> None:
    cache = FakeCache(rate_limit=0)
    service, _, _ = _service(cache=cache)
    with pytest.raises(RateLimitDepasse):
        _ = [
            ev
            async for ev in service.conseiller_stream(
                "Une question neuve sur le cacao ?", Langue.FR, "8.8.8.8"
            )
        ]


# --- _enrichir_contact : ajout vide -> conseil inchangé (ligne 93) ---


def test_enrichir_contact_ajout_deja_present() -> None:
    """Si la fiche contact figure déjà dans la réponse, rien n'est ajouté (ajout vide)."""
    service, _, _ = _service()
    base = Conseil("Réponse.", Confiance.ELEVEE, [], redirection_anader=True)
    # Premier enrichissement : ajoute la/les fiche(s).
    enrichi = service._enrichir_contact(base, "Je suis à Bouaké")
    assert enrichi.reponse != base.reponse
    # Réenrichir le texte déjà enrichi : aucune ligne nouvelle -> conseil inchangé.
    re_enrichi = service._enrichir_contact(enrichi, "Je suis à Bouaké")
    assert re_enrichi.reponse == enrichi.reponse
